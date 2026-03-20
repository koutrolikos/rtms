from __future__ import annotations

from fastapi.testclient import TestClient

from server.app.db.session import get_db
from server.app.main import create_app
from server.app.models.entities import Artifact, Job
from server.app.core.config import ServerSettings
from agent.app.services.api_client import (
    ServerConnectionError,
    describe_connect_error,
    validate_server_url,
)


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


def test_start_session_uses_public_base_url_for_agent_downloads(db_session, monkeypatch) -> None:
    app = create_app()

    def override_get_db():
        yield db_session

    settings = ServerSettings(public_base_url="http://192.168.1.50:8000")
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
        job.payload_json["artifact_download_url"].startswith("http://192.168.1.50:8000/api/artifacts/")
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


def test_validate_server_url_rejects_localhost_for_remote_agent() -> None:
    try:
        validate_server_url("http://127.0.0.1:8000")
    except ServerConnectionError as exc:
        assert "LAN/VPS IP" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected ServerConnectionError")


def test_describe_connect_error_explains_https_against_http_server() -> None:
    exc = describe_connect_error(
        "https://192.168.1.50:8000",
        RuntimeError("[SSL: WRONG_VERSION_NUMBER] wrong version number"),
    )
    assert "using https://" in str(exc)
    assert "plain HTTP" in str(exc)
