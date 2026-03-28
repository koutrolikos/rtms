from __future__ import annotations

from rtms.server.app.core.config import ServerSettings
from rtms.server.app.models.entities import Artifact, Host, Job, RunSession
from rtms.server.app.services.sessions import (
    assign_artifact,
    assign_hosts,
    create_session,
    start_session,
)
from rtms.shared.enums import ArtifactStatus, Role
from rtms.shared.schemas import AssignArtifactRequest, AssignHostsRequest, SessionCreateRequest


def _runtime_host(name: str) -> Host:
    return Host(
        name=name,
        label=name,
        hostname=f"{name}.local",
        status="idle",
        capabilities={
            "build_capable": False,
            "flash_capable": True,
            "capture_capable": True,
        },
        connected_probe_count=1,
    )


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
    tx_host = _runtime_host("host-tx")
    rx_host = _runtime_host("host-rx")
    db_session.add_all([tx_host, rx_host, tx_artifact, rx_artifact])
    db_session.commit()
    assign_hosts(
        db_session,
        session.id,
        AssignHostsRequest(tx_host_id=tx_host.id, rx_host_id=rx_host.id),
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
