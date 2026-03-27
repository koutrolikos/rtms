from __future__ import annotations

import io
import zipfile

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
from server.app.models.entities import AgentHost, Artifact, Job, RawArtifact, Report, Session as SessionModel
from server.app.services.storage import FileStorage
from shared.schemas import BuildRecipe, ConfiguredRepo


APP_CONFIG_SAMPLE = """
#ifndef APP_HUMAN_LOG_ENABLE
#ifdef APP_DEBUG_ENABLE
#define APP_HUMAN_LOG_ENABLE (APP_DEBUG_ENABLE)
#else
#define APP_HUMAN_LOG_ENABLE (1)
#endif
#endif
#ifndef APP_MACHINE_LOG_DETAIL_SUMMARY
#define APP_MACHINE_LOG_DETAIL_SUMMARY (0)
#endif
#ifndef APP_MACHINE_LOG_DETAIL_PACKET
#define APP_MACHINE_LOG_DETAIL_PACKET (1)
#endif
#ifndef APP_MACHINE_LOG_DETAIL
#ifdef APP_REPORT_DETAIL
#define APP_MACHINE_LOG_DETAIL (APP_REPORT_DETAIL)
#else
#define APP_MACHINE_LOG_DETAIL (APP_MACHINE_LOG_DETAIL_PACKET)
#endif
#endif
#ifndef APP_MACHINE_LOG_ENABLE
#ifdef APP_REPORT_ENABLE
#define APP_MACHINE_LOG_ENABLE (APP_REPORT_ENABLE)
#else
#define APP_MACHINE_LOG_ENABLE (APP_HUMAN_LOG_ENABLE)
#endif
#endif
#ifndef APP_MACHINE_LOG_STAT_PERIOD_MS
#ifdef APP_REPORT_STAT_PERIOD_MS
#define APP_MACHINE_LOG_STAT_PERIOD_MS (APP_REPORT_STAT_PERIOD_MS)
#else
#define APP_MACHINE_LOG_STAT_PERIOD_MS (5000U)
#endif
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
            build_command="range-test-agent build-high-altitude-cc --source . --build-dir build/debug",
            artifact_globs=[
                "build/debug/HighAltitudeCC.elf",
                "build/debug/HighAltitudeCC.hex",
                "build/debug/HighAltitudeCC.bin",
                "build/debug/HighAltitudeCC.map",
            ],
            elf_glob="build/debug/HighAltitudeCC.elf",
            flash_image_glob="build/debug/HighAltitudeCC.elf",
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


class EmptyGitHubService:
    def list_repos(self) -> list[ConfiguredRepo]:
        return []

    def get_repo(self, repo_id: str) -> ConfiguredRepo:
        raise KeyError(f"unknown repo_id {repo_id}")


def _client_with_db(db_session) -> TestClient:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _client_with_db_and_settings(db_session, monkeypatch, settings: ServerSettings) -> TestClient:
    monkeypatch.setattr("server.app.main.get_settings", lambda: settings)
    monkeypatch.setattr("server.app.api.agent.get_settings", lambda: settings)
    monkeypatch.setattr("server.app.api.operator.get_settings", lambda: settings)
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _bundle_bytes(*, session_id: str, artifact_id: str | None = None) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            (
                "{"
                f'"origin_type":"manual_upload",'
                f'"created_at":"2026-03-21T12:00:00+00:00",'
                f'"session_id":"{session_id}"'
                + (f',"artifact_id":"{artifact_id}"' if artifact_id else "")
                + "}"
            ),
        )
    return payload.getvalue()


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


def test_home_redirects_to_sessions(db_session) -> None:
    client = _client_with_db(db_session)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/sessions"


def test_sessions_page_groups_search_filters_and_quick_actions(db_session) -> None:
    client = _client_with_db(db_session)

    active_session = SessionModel(
        name="Alpha Active",
        status="capturing",
        stop_mode="default_duration",
        location_mode="manual",
        location_text="ridge",
    )
    failed_session = SessionModel(
        name="Bravo Failed",
        status="failed",
        stop_mode="default_duration",
        location_mode="manual",
        location_text="tree line",
    )
    completed_session = SessionModel(
        name="Charlie Complete",
        status="report_ready",
        stop_mode="default_duration",
        location_mode="manual",
        location_text="valley",
        report_status="ready",
    )
    db_session.add_all([active_session, failed_session, completed_session])
    db_session.commit()

    db_session.add(
        Report(
            session_id=completed_session.id,
            status="ready",
            html_storage_path=f"reports/{completed_session.id}/report.html",
            diagnostics_json={"status": "ready"},
        )
    )
    db_session.add(
        RawArtifact(
            session_id=completed_session.id,
            role=None,
            type="parser_output",
            storage_path=f"raw/{completed_session.id}/parser_output.json",
            hash_sha256="hash",
            size_bytes=42,
            metadata_json={"generated": True},
        )
    )
    db_session.commit()

    response = client.get("/sessions")

    assert response.status_code == 200
    assert "The operator home for active runs, failures, and completed reports." in response.text
    assert 'href="/sessions"' in response.text
    assert 'id="sessions-search"' in response.text
    assert 'data-session-filter="active"' in response.text
    assert 'data-session-filter="needs_attention"' in response.text
    assert 'data-session-filter="completed"' in response.text
    assert 'data-session-group="active"' in response.text
    assert 'data-session-group="needs_attention"' in response.text
    assert 'data-session-group="completed"' in response.text
    assert "Alpha Active" in response.text
    assert "Bravo Failed" in response.text
    assert "Charlie Complete" in response.text
    assert "Open Session" in response.text
    assert f'href="/sessions/{completed_session.id}/report"' in response.text
    assert f'href="/sessions/{completed_session.id}/artifacts"' in response.text


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


def test_hosts_fragment_returns_refreshable_shell_and_markers(db_session) -> None:
    client = _client_with_db(db_session)

    register_response = client.post(
        "/api/agent/register",
        json={
            "name": "agent-fragment",
            "label": "Fragment Agent",
            "hostname": "fragment-host",
            "capabilities": {
                "build_capable": True,
                "flash_capable": True,
                "capture_capable": True,
            },
            "ip_address": "127.0.0.1",
            "connected_probe_count": 2,
            "location_text": "bench",
            "software_version": "0.1.0",
        },
    )
    assert register_response.status_code == 200

    fragment = client.get("/hosts/fragment")

    assert fragment.status_code == 200
    assert 'id="hosts-live-shell"' in fragment.text
    assert "data-live-shell" in fragment.text
    assert "data-live-updated-at" in fragment.text
    assert "Recent Sessions" in fragment.text


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
    assert payload["build_config"]["machine_log_detail"] == 1
    assert payload["build_config"]["machine_log_stat_period_ms"] == 5000
    assert payload["constraints"]["machine_log_detail_options"][0]["label"] == "Summary"


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
                "machine_log_detail": 1,
                "machine_log_stat_period_ms": 5000,
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
    assert artifact.metadata_json["requested_build_config"]["machine_log_detail"] == 1
    assert job.role == "TX"
    assert job.payload_json["build_config"]["machine_log_stat_period_ms"] == 5000


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
        metadata_json={"requested_build_config": {"machine_log_detail": 1, "machine_log_stat_period_ms": 5000}},
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
    assert "Machine Detail" in response.text
    assert "Detail Level" not in response.text
    assert "Stat Period (ms)" in response.text
    assert "Human-readable RTT is always disabled for these builds." in response.text
    assert "UI builds always use packet detail so generated reports have per-packet telemetry." in response.text
    assert "Channel Selection" not in response.text
    assert "Config Detail Packet | Stat period 5000 ms" in response.text
    assert "requested_build_config" not in response.text
    assert "build log" in response.text
    assert "Existing Artifacts" not in response.text
    assert "Assign TX" not in response.text
    assert "Assign RX" not in response.text
    assert "Select TX-compatible artifact" not in response.text
    assert "Select RX-compatible artifact" not in response.text
    assert "Target Slot" in response.text
    assert "Use Existing Artifact" not in response.text
    assert "Build outputs auto-assign to the selected slot when ready." in response.text
    assert 'data-copy-label="session id"' in response.text
    assert 'data-copy-label="artifact id"' in response.text
    assert 'data-copy-label="git sha"' in response.text
    assert 'data-enter-click="search-commits"' in response.text
    assert 'data-enter-click="load-build-config"' in response.text
    assert f'data-auto-refresh-url="/sessions/{session_id}/live"' in response.text
    assert 'data-active-stage-id="stage-configure"' in response.text
    assert f'data-active-stage-storage-key="session-active-stage:{session_id}"' in response.text
    assert f'data-stage-storage-key="session-stage:{session_id}"' in response.text
    assert f"/api/raw-artifacts/{raw_artifact.id}/download" in response.text


def test_session_build_form_forces_packet_detail_for_high_altitude_cc(db_session, monkeypatch) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "ui-build-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "ridge",
        },
    )
    session_id = session_response.json()["id"]

    response = client.post(
        f"/sessions/{session_id}/builds",
        data={
            "repo_id": "high-altitude-cc",
            "git_sha": "deadbeefcafebabe",
            "build_agent_id": "agent-build",
            "role": "TX",
            "build_config_json": '{"machine_log_detail":0,"machine_log_stat_period_ms":5000}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    artifact = db_session.query(Artifact).filter(Artifact.session_id == session_id).one()
    job = db_session.query(Job).filter(Job.session_id == session_id).one()
    assert artifact.metadata_json["requested_build_config"]["machine_log_detail"] == 1
    assert artifact.metadata_json["requested_build_config"]["machine_log_stat_period_ms"] == 5000
    assert job.payload_json["build_config"]["machine_log_detail"] == 1
    assert job.payload_json["build_config"]["machine_log_stat_period_ms"] == 5000


def test_session_detail_page_hides_existing_artifact_assignment_when_no_ready_artifacts(
    db_session, monkeypatch
) -> None:
    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())
    client = _client_with_db(db_session)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "empty-artifacts-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "yard",
        },
    )
    session_id = session_response.json()["id"]

    response = client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    assert "Existing Artifacts" not in response.text
    assert "Use Existing Artifact" not in response.text
    assert "Select TX-compatible artifact" not in response.text
    assert "Select RX-compatible artifact" not in response.text
    assert "Target Slot" in response.text
    assert "Build outputs auto-assign to the selected slot when ready." in response.text


def test_session_detail_page_marks_run_stage_active_once_setup_and_artifacts_are_ready(
    db_session, monkeypatch
) -> None:
    monkeypatch.setattr("server.app.api.operator._github", lambda: EmptyGitHubService())
    client = _client_with_db(db_session)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "progression-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "yard",
        },
    )
    session_id = session_response.json()["id"]

    tx_host = AgentHost(name="tx-agent", label="TX Agent", hostname="tx.local", status="idle")
    rx_host = AgentHost(name="rx-agent", label="RX Agent", hostname="rx.local", status="idle")
    tx_artifact = Artifact(
        session_id=session_id,
        status="ready",
        origin_type="manual_upload",
        role_compatibility_json=["TX"],
        storage_path="artifacts/session-id/tx/bundle.zip",
    )
    rx_artifact = Artifact(
        session_id=session_id,
        status="ready",
        origin_type="manual_upload",
        role_compatibility_json=["RX"],
        storage_path="artifacts/session-id/rx/bundle.zip",
    )
    db_session.add_all([tx_host, rx_host, tx_artifact, rx_artifact])
    db_session.commit()

    response = client.post(
        f"/api/sessions/{session_id}/hosts",
        json={"tx_agent_id": tx_host.id, "rx_agent_id": rx_host.id},
    )
    assert response.status_code == 200

    response = client.post(
        f"/api/sessions/{session_id}/artifacts/assign",
        json={"role": "TX", "artifact_id": tx_artifact.id},
    )
    assert response.status_code == 200

    response = client.post(
        f"/api/sessions/{session_id}/artifacts/assign",
        json={"role": "RX", "artifact_id": rx_artifact.id},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_hosts"

    page = client.get(f"/sessions/{session_id}")

    assert page.status_code == 200
    assert 'data-active-stage-id="stage-run"' in page.text
    assert f'data-active-stage-storage-key="session-active-stage:{session_id}"' in page.text


def test_session_fragment_returns_refreshable_shell_and_markers(db_session) -> None:
    client = _client_with_db(db_session)

    session_id = client.post(
        "/api/sessions",
        json={
            "name": "fragment-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]

    fragment = client.get(f"/sessions/{session_id}/fragment")

    assert fragment.status_code == 200
    assert 'id="session-live-shell"' in fragment.text
    assert "data-live-shell" in fragment.text
    assert "data-live-updated-at" in fragment.text
    assert "Next Required Action" in fragment.text
    assert "Assign both hosts" in fragment.text


def test_session_detail_next_action_card_covers_major_state_transitions(db_session) -> None:
    client = _client_with_db(db_session)

    tx_host = AgentHost(name="tx-next", label="TX Next", hostname="tx.next", status="idle")
    rx_host = AgentHost(name="rx-next", label="RX Next", hostname="rx.next", status="idle")
    db_session.add_all([tx_host, rx_host])
    db_session.commit()

    needs_hosts = SessionModel(name="needs-hosts", status="selecting_artifacts", stop_mode="default_duration")
    needs_artifacts = SessionModel(
        name="needs-artifacts",
        status="selecting_artifacts",
        stop_mode="default_duration",
        tx_agent_id=tx_host.id,
        rx_agent_id=rx_host.id,
    )
    ready_to_start = SessionModel(
        name="ready-to-start",
        status="awaiting_hosts",
        stop_mode="default_duration",
        tx_agent_id=tx_host.id,
        rx_agent_id=rx_host.id,
        tx_artifact_id="tx-artifact",
        rx_artifact_id="rx-artifact",
    )
    capturing = SessionModel(
        name="capturing-session",
        status="capturing",
        stop_mode="default_duration",
        tx_agent_id=tx_host.id,
        rx_agent_id=rx_host.id,
        tx_artifact_id="tx-artifact",
        rx_artifact_id="rx-artifact",
    )
    outputs_ready = SessionModel(
        name="outputs-ready",
        status="report_ready",
        stop_mode="default_duration",
        tx_agent_id=tx_host.id,
        rx_agent_id=rx_host.id,
        tx_artifact_id="tx-artifact",
        rx_artifact_id="rx-artifact",
        report_status="ready",
    )
    db_session.add_all([needs_hosts, needs_artifacts, ready_to_start, capturing, outputs_ready])
    db_session.commit()

    db_session.add(
        Report(
            session_id=outputs_ready.id,
            status="ready",
            html_storage_path=f"reports/{outputs_ready.id}/report.html",
            diagnostics_json={"status": "ready"},
        )
    )
    db_session.commit()

    needs_hosts_fragment = client.get(f"/sessions/{needs_hosts.id}/fragment")
    needs_artifacts_fragment = client.get(f"/sessions/{needs_artifacts.id}/fragment")
    ready_fragment = client.get(f"/sessions/{ready_to_start.id}/fragment")
    capturing_fragment = client.get(f"/sessions/{capturing.id}/fragment")
    outputs_ready_fragment = client.get(f"/sessions/{outputs_ready.id}/fragment")

    assert "Assign both hosts" in needs_hosts_fragment.text
    assert "Build or assign the remaining artifacts" in needs_artifacts_fragment.text
    assert "Start the session" in ready_fragment.text
    assert "Capture is live" in capturing_fragment.text
    assert "Outputs are ready" in outputs_ready_fragment.text
    assert "Open Raw Artifacts" in outputs_ready_fragment.text


def test_session_live_endpoint_version_changes_when_session_data_changes(db_session) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "live-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    )
    session_id = session_response.json()["id"]

    initial_response = client.get(f"/sessions/{session_id}/live")
    assert initial_response.status_code == 200
    initial_version = initial_response.json()["version"]

    annotation_response = client.post(
        f"/api/sessions/{session_id}/annotations",
        json={"text": "backend event"},
    )
    assert annotation_response.status_code == 200

    updated_response = client.get(f"/sessions/{session_id}/live")
    assert updated_response.status_code == 200
    assert updated_response.json()["version"] != initial_version


def test_session_live_endpoint_version_changes_when_build_job_finishes(db_session, monkeypatch) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    session_response = client.post(
        "/api/sessions",
        json={
            "name": "live-build-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    )
    session_id = session_response.json()["id"]

    build_response = client.post(
        f"/api/sessions/{session_id}/builds",
        json={
            "session_id": session_id,
            "role": "TX",
            "repo_id": "high-altitude-cc",
            "git_sha": "deadbeefcafebabe",
            "build_agent_id": "agent-build",
            "build_config": {
                "machine_log_detail": 1,
                "machine_log_stat_period_ms": 5000,
            },
        },
    )
    assert build_response.status_code == 200
    job_id = build_response.json()["job_id"]

    pending_live_response = client.get(f"/sessions/{session_id}/live")
    assert pending_live_response.status_code == 200
    pending_version = pending_live_response.json()["version"]

    result_response = client.post(
        f"/api/agent/jobs/{job_id}/result",
        json={
            "success": False,
            "failure_reason": "build_failed",
            "diagnostics": {"stage": "build"},
        },
    )
    assert result_response.status_code == 200

    completed_live_response = client.get(f"/sessions/{session_id}/live")
    assert completed_live_response.status_code == 200
    assert completed_live_response.json()["version"] != pending_version


def test_hosts_page_and_live_endpoint_refresh_when_hosts_change(db_session) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    initial_live_response = client.get("/hosts/live")
    assert initial_live_response.status_code == 200
    initial_version = initial_live_response.json()["version"]

    register_response = client.post(
        "/api/agent/register",
        json={
            "name": "agent-live",
            "label": "Field Agent",
            "hostname": "field-host",
            "capabilities": {
                "build_capable": True,
                "flash_capable": True,
                "capture_capable": True,
            },
            "ip_address": "127.0.0.1",
            "connected_probe_count": 1,
            "location_text": "track",
            "software_version": "0.1.0",
        },
    )
    assert register_response.status_code == 200

    updated_live_response = client.get("/hosts/live")
    assert updated_live_response.status_code == 200
    assert updated_live_response.json()["version"] != initial_version

    hosts_page = client.get("/hosts")
    assert hosts_page.status_code == 200
    assert 'data-auto-refresh-url="/hosts/live"' in hosts_page.text


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
    assert "RF Link Verdict" in report_page.text
    assert "Packet Type Breakdown" in report_page.text
    assert "Loss Hotspots Over Time" in report_page.text
    assert "payload_json" not in report_page.text
    assert 'data-copy-label="session id"' in report_page.text


def test_report_page_includes_toc_controls_links_and_section_anchors(
    db_session, monkeypatch, tmp_path
) -> None:
    settings = ServerSettings(
        data_dir=tmp_path / "server_data",
        repo_config_path=tmp_path / "server_data" / "repos.json",
    )
    client = _client_with_db_and_settings(db_session, monkeypatch, settings)

    session_id = client.post(
        "/api/sessions",
        json={
            "name": "report-controls-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]

    generate_response = client.post(f"/sessions/{session_id}/report/generate", follow_redirects=False)
    assert generate_response.status_code == 303

    report_page = client.get(f"/sessions/{session_id}/report")

    assert report_page.status_code == 200
    assert 'id="report-toc"' in report_page.text
    assert 'id="report-collapse-evidence"' in report_page.text
    assert 'id="report-expand-summary"' in report_page.text
    assert f'href="/api/sessions/{session_id}/report/json"' in report_page.text
    assert f'href="/sessions/{session_id}/artifacts"' in report_page.text
    assert f'href="/sessions/{session_id}"' in report_page.text
    assert 'id="report-verdict"' in report_page.text
    assert 'id="report-loss-hotspots"' in report_page.text
    assert 'id="report-evidence"' in report_page.text
    assert "Collapse All Evidence" in report_page.text
    assert "Jump to Summary" in report_page.text


def test_storage_rejects_escape_paths(tmp_path) -> None:
    storage = FileStorage(tmp_path / "server_data")
    try:
        storage.save_bytes(b"escape", "../escape.txt")
    except ValueError as exc:
        assert "cannot escape base directory" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected ValueError")


def test_request_build_json_rejects_session_id_mismatch(db_session, monkeypatch) -> None:
    client = _client_with_db(db_session)
    monkeypatch.setattr("server.app.api.operator._github", lambda: FakeGitHubService())

    first_session = client.post(
        "/api/sessions",
        json={
            "name": "session-a",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "ridge",
        },
    ).json()["id"]
    second_session = client.post(
        "/api/sessions",
        json={
            "name": "session-b",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "ridge",
        },
    ).json()["id"]

    response = client.post(
        f"/api/sessions/{first_session}/builds",
        json={
            "session_id": second_session,
            "role": "TX",
            "repo_id": "high-altitude-cc",
            "git_sha": "deadbeef",
            "build_agent_id": "agent-build",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "payload session_id must match path session_id"


def test_repo_endpoints_return_404_for_unknown_repo(db_session, monkeypatch) -> None:
    client = _client_with_db(db_session)
    monkeypatch.setattr("server.app.api.operator._github", lambda: EmptyGitHubService())

    build_config_response = client.get("/api/repos/missing/build-config", params={"git_sha": "deadbeef"})
    commits_response = client.get("/api/repos/missing/commits")

    assert build_config_response.status_code == 404
    assert "unknown repo_id missing" in build_config_response.json()["detail"]
    assert commits_response.status_code == 404
    assert "unknown repo_id missing" in commits_response.json()["detail"]


def test_upload_raw_artifact_rejects_invalid_metadata_json(db_session, monkeypatch, tmp_path) -> None:
    settings = ServerSettings(data_dir=tmp_path / "server_data")
    client = _client_with_db_and_settings(db_session, monkeypatch, settings)
    session_id = client.post(
        "/api/sessions",
        json={
            "name": "raw-upload-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]

    response = client.post(
        "/api/agent/raw-artifacts/upload",
        data={
            "session_id": session_id,
            "artifact_type": "other",
            "metadata_json": "{bad-json",
        },
        files={"file": ("capture.log", b"log-data", "text/plain")},
    )

    assert response.status_code == 400
    assert "invalid metadata_json" in response.json()["detail"]


def test_upload_raw_artifact_sanitizes_path_like_filename(db_session, monkeypatch, tmp_path) -> None:
    settings = ServerSettings(data_dir=tmp_path / "server_data")
    client = _client_with_db_and_settings(db_session, monkeypatch, settings)
    session_id = client.post(
        "/api/sessions",
        json={
            "name": "raw-upload-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]

    response = client.post(
        "/api/agent/raw-artifacts/upload",
        data={
            "session_id": session_id,
            "artifact_type": "other",
            "role": "TX",
        },
        files={"file": ("../../capture.log", b"log-data", "text/plain")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["storage_path"] == f"raw/{session_id}/TX/capture.log"
    assert (settings.data_dir / payload["storage_path"]).read_bytes() == b"log-data"


def test_upload_artifact_rejects_invalid_bundle(db_session, monkeypatch, tmp_path) -> None:
    settings = ServerSettings(data_dir=tmp_path / "server_data")
    client = _client_with_db_and_settings(db_session, monkeypatch, settings)
    session_id = client.post(
        "/api/sessions",
        json={
            "name": "artifact-upload-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]

    response = client.post(
        "/api/agent/artifacts/upload",
        data={"session_id": session_id, "origin_type": "manual_upload", "role_hint": "TX"},
        files={"artifact_bundle": ("bundle.zip", b"not-a-zip", "application/zip")},
    )

    assert response.status_code == 400
    assert "invalid artifact bundle" in response.json()["detail"]


def test_upload_artifact_rejects_cross_session_artifact_id(db_session, monkeypatch, tmp_path) -> None:
    settings = ServerSettings(data_dir=tmp_path / "server_data")
    client = _client_with_db_and_settings(db_session, monkeypatch, settings)
    first_session = client.post(
        "/api/sessions",
        json={
            "name": "session-a",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]
    second_session = client.post(
        "/api/sessions",
        json={
            "name": "session-b",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]
    foreign_artifact = Artifact(
        session_id=second_session,
        status="pending",
        origin_type="manual_upload",
    )
    db_session.add(foreign_artifact)
    db_session.commit()

    response = client.post(
        "/api/agent/artifacts/upload",
        data={"session_id": first_session, "artifact_id": foreign_artifact.id},
        files={"artifact_bundle": ("bundle.zip", _bundle_bytes(session_id=first_session), "application/zip")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "artifact not found"


def test_download_artifact_returns_404_when_storage_file_is_missing(db_session, monkeypatch, tmp_path) -> None:
    settings = ServerSettings(data_dir=tmp_path / "server_data")
    client = _client_with_db_and_settings(db_session, monkeypatch, settings)
    session_id = client.post(
        "/api/sessions",
        json={
            "name": "missing-file-session",
            "stop_mode": "default_duration",
            "location_mode": "manual",
            "location_text": "field",
        },
    ).json()["id"]
    artifact = Artifact(
        session_id=session_id,
        status="ready",
        origin_type="manual_upload",
        storage_path="artifacts/missing/bundle.zip",
    )
    db_session.add(artifact)
    db_session.commit()

    response = client.get(f"/api/artifacts/{artifact.id}/download")

    assert response.status_code == 404
    assert response.json()["detail"] == "artifact not found"
