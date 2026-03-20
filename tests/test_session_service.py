from __future__ import annotations

from server.app.core.config import ServerSettings
from server.app.models.entities import Artifact, Job, Session as SessionModel
from server.app.services.sessions import (
    assign_artifact,
    assign_hosts,
    create_session,
    start_session,
)
from shared.enums import ArtifactStatus, Role
from shared.schemas import AssignArtifactRequest, AssignHostsRequest, SessionCreateRequest


def test_session_start_creates_prepare_jobs(db_session) -> None:
    settings = ServerSettings(data_dir="server_data")
    session = create_session(db_session, settings, SessionCreateRequest(name="field"))
    tx_artifact = Artifact(
        session_id=session.id,
        status=ArtifactStatus.READY.value,
        origin_type="github_build",
        source_repo="org/repo",
    )
    rx_artifact = Artifact(
        session_id=session.id,
        status=ArtifactStatus.READY.value,
        origin_type="github_build",
        source_repo="org/repo",
    )
    db_session.add_all([tx_artifact, rx_artifact])
    db_session.commit()
    assign_hosts(
        db_session,
        session.id,
        AssignHostsRequest(tx_agent_id="agent-tx", rx_agent_id="agent-rx"),
    )
    assign_artifact(db_session, session.id, AssignArtifactRequest(role=Role.TX, artifact_id=tx_artifact.id))
    assign_artifact(db_session, session.id, AssignArtifactRequest(role=Role.RX, artifact_id=rx_artifact.id))
    session = start_session(
        db_session,
        settings=settings,
        session_id=session.id,
        base_url="http://server.test",
    )
    jobs = db_session.query(Job).filter(Job.session_id == session.id).all()
    assert session.status == "preparing_roles"
    assert len(jobs) == 2
    assert {job.role for job in jobs} == {"TX", "RX"}
