from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from agent.app.services.api_client import (
    ServerConnectionError,
    describe_connect_error,
    validate_server_url,
)
from server.app.core.config import ServerSettings
from server.app.db.session import get_db
from server.app.main import create_app
from server.app.models.entities import Artifact, Job, RawArtifact
from shared.schemas import BuildRecipe, ConfiguredRepo


APP_CONFIG_SAMPLE = """
#ifndef APP_DEBUG_ENABLE
#define APP_DEBUG_ENABLE (0)
#endif
#ifndef APP_LOG_LEVEL
#define APP_LOG_LEVEL (2)
#endif
#ifndef APP_CHSEL_ALLOWLIST_COUNT
#define APP_CHSEL_ALLOWLIST_COUNT (2U)
#endif
#ifndef APP_CHSEL_ALLOWLIST_HZ_LIST
#define APP_CHSEL_ALLOWLIST_HZ_LIST 433200000UL,434600000UL
#endif
#ifndef APP_CHSEL_BAND_MIN_HZ
#define APP_CHSEL_BAND_MIN_HZ (433050000UL)
#endif
#ifndef APP_CHSEL_BAND_MAX_HZ
#define APP_CHSEL_BAND_MAX_HZ (434790000UL)
#endif
#ifndef APP_CHSEL_OUR_HALF_BW_HZ
#define APP_CHSEL_OUR_HALF_BW_HZ (108500UL)
#endif
#ifndef APP_CHSEL_GUARD_BAND_HZ
#define APP_CHSEL_GUARD_BAND_HZ (30000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK_COUNT
#define APP_CHSEL_EXCLUSION_MASK_COUNT (1U)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (433920000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (25000UL)
#endif
#ifndef APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS
#define APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS (15000U)
#endif
""".strip()


def _high_altitude_cc_repo() -> ConfiguredRepo:
    return ConfiguredRepo(
        id="high-altitude-cc",
        display_name="High-Altitude-CC",
        full_name="koutrolikos/High-Altitude-CC",
        clone_url="https://github.com/koutrolikos/High-Altitude-CC.git",
        default_branch="dev",
        build_recipe=BuildRecipe(
            build_command="make -f Debug/makefile DEBUG=1 all hex bin",
            artifact_globs=[
                "build/debug/High-Altitude-CC.elf",
                "build/debug/High-Altitude-CC.hex",
                "build/debug/High-Altitude-CC.bin",
                "build/debug/High-Altitude-CC.map",
            ],
            elf_glob="build/debug/High-Altitude-CC.elf",
            flash_image_glob="build/debug/High-Altitude-CC.elf",
            timeout_seconds=1200,
            env={},
            rtt_symbol="_SEGGER_RTT",
        ),
    )


class FakeGitHubService:
    def __init__(self) -> None:
        self.repo = _high_altitude_cc_repo()

    def list_repos(self) -> list[ConfiguredRepo]:
        return [self.repo]

    def get_repo(self, repo_id: str) -> ConfiguredRepo:
        assert repo_id == self.repo.id
        return self.repo

    def fetch_file_at_ref(self, repo_id: str, path: str, ref: str) -> str:
        assert repo_id == self.repo.id
        assert path == "Core/Inc/app_config.h"
        assert ref
        return APP_CONFIG_SAMPLE


def test_create_session_api(db_session) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    response = client.post(
        "/api/sessions",
        json={
            "name": "api-session",
            "stop_mode": "default_duration",
            "selected_duration_minutes": 7,
            "initial_notes": "baseline route",
            "location_mode": "manual",
            "location_text": "north ridge",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "selecting_artifacts"
    session_response = client.get(f"/api/sessions/{payload['id']}")
    assert session_response.status_code == 200
    assert session_response.json()["name"] == "api-session"


def test_hosts_page_after_agent_register(db_session) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    response = client.post(
        "/api/agent/register",
        json={
            "name": "agent-1",
            "label": "Bench Agent",
            "hostname": "bench-host",
            "capabilities": {
                "build_capable": True,
                "flash_capable": True,
                "capture_capable": True,
            },
            "ip_address": "127.0.0.1",
            "connected_probe_count": 1,
            "location_text": "lab",
            "software_version": "0.1.0",
        },
    )
    assert response.status_code == 200
    hosts_page = client.get("/hosts")
    assert hosts_page.status_code == 200
    assert "Bench Agent" in hosts_page.text
    assert "Build" in hosts_page.text
    assert "build_capable" not in hosts_page.text
    assert 'data-copy-label="public url"' in hosts_page.text


def test_start_session_uses_public_base_url_for_agent_downloads(db_session, monkeypatch) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    settings = ServerSettings(public_base_url="http://172.20.10.3:8000")
    monkeypatch.setattr("server.app.api.operator.get_settings", lambda: settings)
    monkeypatch.setattr("server.app.db.session.get_settings", lambda: settings)
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "network-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "yard",
        },
    )
    session_id = session_response.json()["id"]

    tx_artifact = Artifact(
        session_id=session_id,
        status="ready",
        origin_type="github_build",
        source_repo="org/repo",
    )
    rx_artifact = Artifact(
        session_id=session_id,
        status="ready",
        origin_type="github_build",
        source_repo="org/repo",
    )
    db_session.add_all([tx_artifact, rx_artifact])
    db_session.commit()

    client.post(
        f"/api/sessions/{session_id}/hosts",
        json={"tx_agent_id": "agent-tx", "rx_agent_id": "agent-rx"},
    )
    client.post(
        f"/api/sessions/{session_id}/artifacts/assign",
        json={"role": "TX", "artifact_id": tx_artifact.id},
    )
    client.post(
        f"/api/sessions/{session_id}/artifacts/assign",
        json={"role": "RX", "artifact_id": rx_artifact.id},
    )
    start_response = client.post(f"/api/sessions/{session_id}/start")
    assert start_response.status_code == 200

    jobs = db_session.query(Job).filter(Job.session_id == session_id).all()
    assert len(jobs) == 2
    assert all(
        job.payload_json["artifact_download_url"].startswith("http://172.20.10.3:8000/api/artifacts/")
        for job in jobs
    )


def test_healthz_reports_public_network_url(db_session) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "public_base_url" in payload


def test_validate_server_url_allows_localhost_for_same_machine_dev() -> None:
    validate_server_url("http://127.0.0.1:8000")


def test_validate_server_url_rejects_listen_address() -> None:
    try:
        validate_server_url("http://0.0.0.0:8000")
    except ServerConnectionError as exc:
        assert "127.0.0.1:8000" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected ServerConnectionError")


def test_describe_connect_error_explains_https_against_http_server() -> None:
    exc = describe_connect_error(
        "https://172.20.10.3:8000",
        RuntimeError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),
    )
    assert "using https://" in str(exc)
    assert "plain HTTP" in str(exc)


def test_describe_connect_error_explains_timeout_and_localhost_hint() -> None:
    exc = describe_connect_error(
        "http://172.20.10.3:8000",
        httpx.ConnectTimeout("timed out"),
    )
    assert "Timed out while connecting" in str(exc)
    assert "http://127.0.0.1:8000" in str(exc)


def test_repo_build_config_endpoint_parses_high_altitude_cc_defaults(db_session, monkeypatch) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    response = client.get("/api/repos/high-altitude-cc/build-config", params={"git_sha": "deadbeef"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["repo_id"] == "high-altitude-cc"
    assert payload["git_sha"] == "deadbeef"
    assert payload["build_config"]["app_debug_enable"] == 0
    assert payload["build_config"]["app_log_level"] == 2
    assert payload["build_config"]["chsel"]["allowlist_hz"] == [433200000, 434600000]
    assert payload["constraints"]["exclusion_mask_count_max"] == 4


def test_request_build_json_stores_high_altitude_cc_build_config_and_payload(
    db_session, monkeypatch
) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "config-build-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "ridge",
        },
    )
    session_id = session_response.json()["id"]

    response = client.post(
        f"/api/sessions/{session_id}/builds",
        json={
            "session_id": session_id,
            "role": "TX",
            "repo_id": "high-altitude-cc",
            "git_sha": "deadbeefcafebabe",
            "build_agent_id": "agent-build",
            "build_config": {
                "app_debug_enable": 0,
                "app_log_level": 2,
                "chsel": {
                    "allowlist_hz": [433200000, 434600000],
                    "band_min_hz": 433050000,
                    "band_max_hz": 434790000,
                    "our_half_bw_hz": 108500,
                    "guard_band_hz": 30000,
                    "exclusion_masks": [
                        {"center_hz": 433920000, "half_bw_hz": 25000},
                    ],
                    "backup_failover_holdoff_ms": 15000,
                },
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    artifact = db_session.get(Artifact, payload["artifact_id"])
    job = db_session.get(Job, payload["job_id"])
    assert artifact is not None
    assert job is not None
    assert artifact.metadata_json["auto_assign_role"] == "TX"
    assert artifact.metadata_json["requested_build_config"]["app_log_level"] == 2
    assert job.role == "TX"
    assert job.payload_json["build_config"]["chsel"]["allowlist_hz"] == [433200000, 434600000]


def test_session_detail_page_shows_build_controls_metadata_and_build_log_link(
    db_session, monkeypatch
) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "detail-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "yard",
        },
    )
    session_id = session_response.json()["id"]

    artifact = Artifact(
        session_id=session_id,
        status="ready",
        origin_type="github_build",
        source_repo="koutrolikos/High-Altitude-CC",
        git_sha="deadbeef",
        role_compatibility_json=["TX"],
        metadata_json={"requested_build_config": {"app_debug_enable": 0}},
        storage_path="artifacts/session-id/artifact-id/bundle.zip",
    )
    raw_artifact = RawArtifact(
        session_id=session_id,
        role="TX",
        type="build_log",
        storage_path="raw/session-id/TX/build.log",
        hash_sha256="abc123",
        size_bytes=128,
        metadata_json={"stage": "build"},
    )
    db_session.add_all([artifact, raw_artifact])
    db_session.commit()

    response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    assert "Load Config" in response.text
    assert "APP_DEBUG_ENABLE" in response.text
    assert "Channel Selection" in response.text
    assert "Config Debug 0" in response.text
    assert "requested_build_config" not in response.text
    assert "build log" in response.text
    assert 'data-copy-label="session id"' in response.text
    assert 'data-copy-label="artifact id"' in response.text
    assert 'data-copy-label="git sha"' in response.text
    assert 'data-enter-click="search-commits"' in response.text
    assert 'data-enter-click="load-build-config"' in response.text
    assert f"/api/raw-artifacts/{raw_artifact.id}/download" in response.text


def test_report_page_renders_summaries_without_raw_payload_dumps(db_session) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "report-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    )
    session_id = session_response.json()["id"]

    annotation_response = client.post(
        f"/api/sessions/{session_id}/annotations",
        json={"text": "Vehicle entered tree line"},
    )
    assert annotation_response.status_code == 200

    generate_response = client.post(f"/sessions/{session_id}/report/generate", follow_redirects=False)
    assert generate_response.status_code == 303

    report_page = client.get(f"/sessions/{session_id}/report")

    assert report_page.status_code == 200
    assert "Session Event Audit" in report_page.text
    assert "Vehicle entered tree line" in report_page.text
    assert "payload_json" not in report_page.text
    assert 'data-copy-label="session id"' in report_page.text
