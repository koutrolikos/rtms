from __future__ import annotations

from fastapi.testclient import TestClient

from server.app.db.session import get_db
from server.app.main import create_app


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
