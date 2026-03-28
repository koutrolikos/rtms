from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from server.app.core.config import ServerSettings
from server.app.models.entities import (
    Annotation,
    Artifact,
    Job,
    RawArtifact,
    Report,
    Session as SessionModel,
    SessionEvent,
    SessionRoleRun,
)
from server.app.services import jobs as job_service
from server.app.services.storage import FileStorage
from shared.high_altitude_cc import HIGH_ALTITUDE_CC_REPO_ID
from shared.enums import (
    ArtifactOriginType,
    ArtifactStatus,
    EventSourceType,
    EventType,
    JobState,
    JobType,
    RawArtifactType,
    ReportStatus,
    Role,
    RoleRunState,
    SessionState,
    StopMode,
)
from shared.schemas import (
    AnnotationCreateRequest,
    AssignArtifactRequest,
    AssignHostsRequest,
    BuildRequest,
    ConfiguredRepo,
    JobResult,
    SessionCreateRequest,
    SessionUpdateRequest,
)
from shared.state_machine import transition_job, transition_role_run, transition_session
from shared.time_sync import utc_now

logger = logging.getLogger(__name__)


TERMINAL_SESSION_STATES = {
    SessionState.REPORT_READY.value,
    SessionState.FAILED.value,
    SessionState.CANCELLED.value,
}


def _coerce_role(role: str | Role) -> Role:
    return role if isinstance(role, Role) else Role(role)


def is_terminal_session_status(status: str) -> bool:
    return status in TERMINAL_SESSION_STATES


def canonical_raw_artifact_storage_path(
    *,
    session_id: str,
    artifact_type: RawArtifactType,
    role: Role | None,
    metadata: dict[str, Any] | None = None,
    filename: str | None = None,
) -> str:
    role_dir = role.value if role else "session"
    metadata = metadata or {}
    singleton_filenames = {
        RawArtifactType.OPENOCD_LOG: "openocd.log",
        RawArtifactType.AGENT_EVENT_LOG: "agent_events.jsonl",
        RawArtifactType.TIMING_SAMPLES: "timing_samples.json",
        RawArtifactType.RTT_LOG: "rtt.log",
        RawArtifactType.RTT_MACHINE_LOG: "rtt.rttbin",
        RawArtifactType.CAPTURE_COMMAND_LOG: "capture_command.log",
    }
    if artifact_type in singleton_filenames:
        if role is None:
            raise ValueError(f"{artifact_type.value} uploads require a role")
        return str(Path("raw") / session_id / role.value / singleton_filenames[artifact_type])
    if artifact_type == RawArtifactType.BUILD_LOG:
        artifact_id = str(metadata.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("build_log uploads require metadata.artifact_id")
        return str(Path("raw") / session_id / "artifacts" / artifact_id / "build.log")
    if artifact_type == RawArtifactType.PARSER_OUTPUT:
        return str(Path("reports") / session_id / "parser_output.json")
    if filename:
        return str(Path("raw") / session_id / role_dir / filename)
    return str(Path("raw") / session_id / role_dir / f"{artifact_type.value}.bin")


def _delete_storage_file(storage: FileStorage, storage_path: str) -> bool:
    try:
        path = storage.resolve(storage_path)
    except ValueError:
        return False
    if path.exists() and not path.is_file():
        return False
    removed = False
    try:
        path.unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    current = path.parent
    while current != storage.base_dir and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
    return removed


def cleanup_terminal_artifact_bundles(
    db: Session,
    *,
    settings: ServerSettings | None = None,
    storage_root: Path | None = None,
    session_id: str,
) -> list[str]:
    session = get_session_or_404(db, session_id)
    if not is_terminal_session_status(session.status):
        return []
    base_dir = storage_root or (settings.data_dir if settings is not None else None)
    if base_dir is None:
        raise ValueError("cleanup_terminal_artifact_bundles requires settings or storage_root")
    storage = FileStorage(base_dir)
    removed_paths: list[str] = []
    for artifact in session_artifacts(db, session_id):
        if not artifact.storage_path:
            continue
        storage_path = artifact.storage_path
        _delete_storage_file(storage, storage_path)
        artifact.storage_path = None
        removed_paths.append(storage_path)
    db.commit()
    return removed_paths


def delete_terminal_session(
    db: Session,
    *,
    settings: ServerSettings,
    session_id: str,
) -> None:
    session = get_session_or_404(db, session_id)
    if not is_terminal_session_status(session.status):
        raise HTTPException(status_code=400, detail="only terminal sessions can be deleted")

    report = session_report(db, session_id)
    storage_paths = {
        item.storage_path
        for item in session_artifacts(db, session_id)
        if item.storage_path
    }
    storage_paths.update(
        item.storage_path
        for item in raw_artifacts(db, session_id)
        if item.storage_path
    )
    if report and report.html_storage_path:
        storage_paths.add(report.html_storage_path)

    storage = FileStorage(settings.data_dir)
    for storage_path in sorted(storage_paths):
        _delete_storage_file(storage, storage_path)

    for model in (Annotation, SessionEvent, Job, SessionRoleRun, RawArtifact, Artifact):
        db.query(model).filter(model.session_id == session_id).delete(synchronize_session=False)
    db.query(Report).filter(Report.session_id == session_id).delete(synchronize_session=False)
    db.query(SessionModel).filter(SessionModel.id == session_id).delete(synchronize_session=False)
    db.commit()


def log_event(
    db: Session,
    *,
    session_id: str,
    source_type: EventSourceType,
    source_ref: str | None,
    event_type: EventType,
    payload: dict[str, Any],
    local_timestamp=None,
    corrected_timestamp=None,
) -> SessionEvent:
    event = SessionEvent(
        session_id=session_id,
        source_type=source_type.value,
        source_ref=source_ref,
        event_type=event_type.value,
        local_timestamp=local_timestamp,
        corrected_timestamp=corrected_timestamp,
        payload_json=payload,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def create_session(db: Session, settings: ServerSettings, request: SessionCreateRequest) -> SessionModel:
    session = SessionModel(
        name=request.name,
        status=SessionState.DRAFT.value,
        stop_mode=request.stop_mode.value,
        default_duration_minutes=settings.default_duration_minutes,
        selected_duration_minutes=request.selected_duration_minutes,
        initial_notes=request.initial_notes,
        location_mode=request.location_mode.value,
        location_text=request.location_text,
        location_lat=request.location_lat,
        location_lon=request.location_lon,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    log_event(
        db,
        session_id=session.id,
        source_type=EventSourceType.OPERATOR,
        source_ref=None,
        event_type=EventType.STATE_CHANGE,
        payload={"from": None, "to": session.status},
    )
    session.status = transition_session(SessionState.DRAFT, SessionState.SELECTING_ARTIFACTS).value
    db.commit()
    db.refresh(session)
    return session


def update_session_metadata(db: Session, session_id: str, request: SessionUpdateRequest) -> SessionModel:
    session = get_session_or_404(db, session_id)
    for field, value in request.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(session, field, value.value if hasattr(value, "value") else value)
    db.commit()
    db.refresh(session)
    return session


def get_session_or_404(db: Session, session_id: str) -> SessionModel:
    session = db.get(SessionModel, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def list_sessions(db: Session) -> list[SessionModel]:
    return db.query(SessionModel).order_by(SessionModel.created_at.desc()).all()


def get_active_session(db: Session) -> SessionModel | None:
    return (
        db.query(SessionModel)
        .filter(
            SessionModel.status.in_(
                [
                    SessionState.DISTRIBUTING_ARTIFACTS.value,
                    SessionState.PREPARING_ROLES.value,
                    SessionState.READY_TO_CAPTURE.value,
                    SessionState.CAPTURING.value,
                    SessionState.MERGING.value,
                ]
            )
        )
        .one_or_none()
    )


def _reconcile_preflight_state(session: SessionModel) -> None:
    if session.status in {
        SessionState.PREPARING_ROLES.value,
        SessionState.READY_TO_CAPTURE.value,
        SessionState.CAPTURING.value,
        SessionState.MERGING.value,
        SessionState.REPORT_READY.value,
        SessionState.FAILED.value,
        SessionState.CANCELLED.value,
    }:
        return
    if session.tx_artifact_id and session.rx_artifact_id and session.tx_agent_id and session.rx_agent_id:
        desired = SessionState.AWAITING_HOSTS
    else:
        desired = SessionState.SELECTING_ARTIFACTS
    if session.status != desired.value:
        current = SessionState(session.status)
        session.status = transition_session(current, desired).value


def assign_hosts(db: Session, session_id: str, request: AssignHostsRequest) -> SessionModel:
    session = get_session_or_404(db, session_id)
    session.tx_agent_id = request.tx_agent_id
    session.rx_agent_id = request.rx_agent_id
    _ensure_role_runs_exist(db, session)
    _reconcile_preflight_state(session)
    db.commit()
    db.refresh(session)
    return session


def assign_artifact(db: Session, session_id: str, request: AssignArtifactRequest) -> SessionModel:
    session = get_session_or_404(db, session_id)
    artifact = db.get(Artifact, request.artifact_id)
    if artifact is None or artifact.session_id != session.id:
        raise HTTPException(status_code=404, detail="artifact not found")
    if artifact.status != ArtifactStatus.READY.value:
        raise HTTPException(status_code=400, detail="artifact not ready")
    if request.role == Role.TX:
        session.tx_artifact_id = artifact.id
    else:
        session.rx_artifact_id = artifact.id
    _ensure_role_runs_exist(db, session)
    role_run = _role_run_for(db, session.id, request.role)
    role_run.artifact_id = artifact.id
    _reconcile_preflight_state(session)
    db.commit()
    db.refresh(session)
    return session


def request_build(
    db: Session,
    *,
    settings: ServerSettings,
    request: BuildRequest,
    repo: ConfiguredRepo,
) -> tuple[Artifact, Job]:
    if repo.id == HIGH_ALTITUDE_CC_REPO_ID and request.build_config is None:
        raise HTTPException(status_code=400, detail="high-altitude-cc builds require build_config")
    session = get_session_or_404(db, request.session_id)
    artifact = Artifact(
        session_id=session.id,
        status=ArtifactStatus.PENDING.value,
        origin_type=ArtifactOriginType.GITHUB_BUILD.value,
        source_repo=repo.full_name,
        git_sha=request.git_sha,
        producing_agent_id=request.build_agent_id,
        role_compatibility_json=[request.role.value],
        metadata_json={
            "repo_id": repo.id,
            "auto_assign_role": request.role.value,
            "requested_build_config": (
                request.build_config.model_dump(mode="json") if request.build_config else None
            ),
        },
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    current = SessionState(session.status)
    session.status = transition_session(current, SessionState.BUILDING_ARTIFACTS).value
    db.commit()
    job = job_service.create_job(
        db,
        agent_id=request.build_agent_id,
        job_type=JobType.BUILD_ARTIFACT,
        session_id=session.id,
        role=request.role.value if request.role else None,
        payload={
            "artifact_id": artifact.id,
            "session_id": session.id,
            "role_hint": request.role.value,
            "repo": repo.model_dump(mode="json"),
            "git_sha": request.git_sha,
            "build_config": request.build_config.model_dump(mode="json") if request.build_config else None,
        },
    )
    log_event(
        db,
        session_id=session.id,
        source_type=EventSourceType.SERVER,
        source_ref=job.id,
        event_type=EventType.JOB_UPDATE,
        payload={"job_type": job.type, "status": job.status, "artifact_id": artifact.id},
    )
    return artifact, job


def create_manual_artifact_record(
    db: Session,
    *,
    session_id: str,
    origin_type: ArtifactOriginType,
    producing_agent_id: str | None,
    role_hint: Role | None,
    source_repo: str | None,
    git_sha: str | None,
) -> Artifact:
    artifact = Artifact(
        session_id=session_id,
        status=ArtifactStatus.PENDING.value,
        origin_type=origin_type.value,
        source_repo=source_repo,
        git_sha=git_sha,
        producing_agent_id=producing_agent_id,
        role_compatibility_json=[role_hint.value] if role_hint else [],
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def add_annotation(db: Session, session_id: str, request: AnnotationCreateRequest) -> Annotation:
    session = get_session_or_404(db, session_id)
    annotation = Annotation(session_id=session.id, text=request.text)
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    log_event(
        db,
        session_id=session.id,
        source_type=EventSourceType.OPERATOR,
        source_ref=annotation.id,
        event_type=EventType.ANNOTATION,
        payload={"text": request.text},
        corrected_timestamp=annotation.created_at,
    )
    return annotation


def _ensure_role_runs_exist(db: Session, session: SessionModel) -> None:
    for role, agent_id, artifact_id in (
        (Role.TX, session.tx_agent_id, session.tx_artifact_id),
        (Role.RX, session.rx_agent_id, session.rx_artifact_id),
    ):
        role_run = _role_run_for(db, session.id, role, required=False)
        if role_run is None and agent_id:
            role_run = SessionRoleRun(
                session_id=session.id,
                role=role.value,
                agent_id=agent_id,
                status=RoleRunState.IDLE.value,
                artifact_id=artifact_id,
            )
            db.add(role_run)
        elif role_run is not None:
            if agent_id:
                role_run.agent_id = agent_id
            if artifact_id:
                role_run.artifact_id = artifact_id
    db.commit()


def _role_run_for(
    db: Session, session_id: str, role: Role, *, required: bool = True
) -> SessionRoleRun | None:
    role_run = (
        db.query(SessionRoleRun)
        .filter(and_(SessionRoleRun.session_id == session_id, SessionRoleRun.role == role.value))
        .one_or_none()
    )
    if required and role_run is None:
        raise HTTPException(status_code=404, detail=f"missing role run for {role.value}")
    return role_run


def session_roles(db: Session, session_id: str) -> list[SessionRoleRun]:
    return (
        db.query(SessionRoleRun)
        .filter(SessionRoleRun.session_id == session_id)
        .order_by(SessionRoleRun.role.asc())
        .all()
    )


def session_artifacts(db: Session, session_id: str) -> list[Artifact]:
    return (
        db.query(Artifact)
        .filter(Artifact.session_id == session_id)
        .order_by(Artifact.created_at.desc())
        .all()
    )


def raw_artifacts(db: Session, session_id: str) -> list[RawArtifact]:
    return (
        db.query(RawArtifact)
        .filter(RawArtifact.session_id == session_id)
        .order_by(RawArtifact.created_at.asc())
        .all()
    )


def annotations(db: Session, session_id: str) -> list[Annotation]:
    return (
        db.query(Annotation)
        .filter(Annotation.session_id == session_id)
        .order_by(Annotation.created_at.asc())
        .all()
    )


def session_events(db: Session, session_id: str) -> list[SessionEvent]:
    return (
        db.query(SessionEvent)
        .filter(SessionEvent.session_id == session_id)
        .order_by(SessionEvent.created_at.asc())
        .all()
    )


def session_jobs(db: Session, session_id: str) -> list[Job]:
    return (
        db.query(Job)
        .filter(Job.session_id == session_id)
        .order_by(Job.created_at.desc())
        .all()
    )


def _assert_session_startable(db: Session, session: SessionModel) -> None:
    active = get_active_session(db)
    if active is not None and active.id != session.id:
        raise HTTPException(status_code=409, detail=f"session {active.id} is already active")
    if not all([session.tx_agent_id, session.rx_agent_id, session.tx_artifact_id, session.rx_artifact_id]):
        raise HTTPException(status_code=400, detail="session missing host or artifact assignments")


def start_session(
    db: Session,
    *,
    settings: ServerSettings,
    session_id: str,
    base_url: str,
) -> SessionModel:
    session = get_session_or_404(db, session_id)
    _assert_session_startable(db, session)
    _ensure_role_runs_exist(db, session)
    current = SessionState(session.status)
    session.status = transition_session(current, SessionState.DISTRIBUTING_ARTIFACTS).value
    db.commit()
    session.status = transition_session(SessionState(session.status), SessionState.PREPARING_ROLES).value
    session.started_at = utc_now()
    db.commit()
    for role, agent_id, artifact_id in (
        (Role.TX, session.tx_agent_id, session.tx_artifact_id),
        (Role.RX, session.rx_agent_id, session.rx_artifact_id),
    ):
        role_run = _role_run_for(db, session.id, role)
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.ASSIGNED).value
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.ARTIFACT_PENDING).value
        job_service.create_job(
            db,
            agent_id=agent_id,
            job_type=JobType.PREPARE_ROLE,
            session_id=session.id,
            role=role.value,
            payload={
                "session_id": session.id,
                "role_run_id": role_run.id,
                "role": role.value,
                "artifact_id": artifact_id,
                "artifact_download_url": f"{base_url}/api/artifacts/{artifact_id}/download",
                "capture_duration_seconds": _capture_duration_seconds(session),
                "stop_mode": session.stop_mode,
            },
        )
        log_event(
            db,
            session_id=session.id,
            source_type=EventSourceType.SERVER,
            source_ref=agent_id,
            event_type=EventType.JOB_UPDATE,
            payload={"role": role.value, "job_type": JobType.PREPARE_ROLE.value, "status": JobState.PENDING.value},
        )
    db.commit()
    db.refresh(session)
    return session


def _capture_duration_seconds(session: SessionModel) -> int | None:
    if session.stop_mode == StopMode.MANUAL.value:
        return None
    minutes = session.selected_duration_minutes or session.default_duration_minutes
    return minutes * 60


def stop_session(db: Session, session_id: str) -> SessionModel:
    session = get_session_or_404(db, session_id)
    if session.status != SessionState.CAPTURING.value:
        raise HTTPException(status_code=400, detail="session is not capturing")
    for role_run in session_roles(db, session.id):
        if role_run.status == RoleRunState.CAPTURING.value:
            job_service.create_job(
                db,
                agent_id=role_run.agent_id,
                job_type=JobType.STOP_CAPTURE,
                session_id=session.id,
                role=role_run.role,
                payload={
                    "session_id": session.id,
                    "role_run_id": role_run.id,
                    "role": role_run.role,
                    "reason": "manual_stop",
                },
            )
    db.commit()
    return session


def _dispatch_capture_start(db: Session, settings: ServerSettings, session: SessionModel) -> None:
    planned_start = utc_now()
    planned_start = planned_start.replace(microsecond=0)
    planned_start = planned_start + timedelta(seconds=settings.capture_start_lead_seconds)
    for role_run in session_roles(db, session.id):
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.CAPTURING).value
        role_run.capture_started_at = planned_start
        job_service.create_job(
            db,
            agent_id=role_run.agent_id,
            job_type=JobType.START_CAPTURE,
            session_id=session.id,
            role=role_run.role,
            payload={
                "session_id": session.id,
                "role_run_id": role_run.id,
                "role": role_run.role,
                "planned_start_at": planned_start.isoformat(),
                "duration_seconds": _capture_duration_seconds(session),
                "stop_mode": session.stop_mode,
            },
        )
    session.status = transition_session(SessionState(session.status), SessionState.CAPTURING).value
    db.commit()
    log_event(
        db,
        session_id=session.id,
        source_type=EventSourceType.SERVER,
        source_ref=None,
        event_type=EventType.CAPTURE,
        payload={"planned_start_at": planned_start.isoformat()},
        corrected_timestamp=planned_start,
    )


def register_raw_artifact(
    db: Session,
    *,
    session_id: str,
    role: Role | None,
    artifact_type: RawArtifactType,
    storage_path: str,
    sha256: str,
    size_bytes: int,
    metadata: dict[str, Any] | None = None,
) -> RawArtifact:
    existing = (
        db.query(RawArtifact)
        .filter(
            RawArtifact.session_id == session_id,
            RawArtifact.storage_path == storage_path,
        )
        .order_by(RawArtifact.created_at.asc())
        .all()
    )
    raw = existing[0] if existing else RawArtifact(session_id=session_id, storage_path=storage_path)
    raw.role = role.value if role else None
    raw.type = artifact_type.value
    raw.storage_path = storage_path
    raw.hash_sha256 = sha256
    raw.size_bytes = size_bytes
    raw.metadata_json = metadata or {}
    raw.created_at = utc_now()
    if not existing:
        db.add(raw)
    for duplicate in existing[1:]:
        db.delete(duplicate)
    db.commit()
    db.refresh(raw)
    log_event(
        db,
        session_id=session_id,
        source_type=EventSourceType.AGENT,
        source_ref=raw.id,
        event_type=EventType.UPLOAD,
        payload={"type": artifact_type.value, "role": role.value if role else None},
    )
    return raw


def apply_artifact_upload(
    db: Session,
    *,
    artifact_id: str,
    storage_path: str,
    sha256: str,
    metadata: dict[str, Any],
    producing_agent_id: str | None,
) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    artifact.storage_path = storage_path
    artifact.hash_sha256 = sha256
    artifact.metadata_json = {**(artifact.metadata_json or {}), **metadata}
    artifact.status = ArtifactStatus.READY.value
    if producing_agent_id:
        artifact.producing_agent_id = producing_agent_id
    db.commit()
    db.refresh(artifact)
    session = db.get(SessionModel, artifact.session_id)
    if session is not None:
        auto_assign = (artifact.metadata_json or {}).get("auto_assign_role")
        if auto_assign == Role.TX.value:
            session.tx_artifact_id = artifact.id
        elif auto_assign == Role.RX.value:
            session.rx_artifact_id = artifact.id
        _ensure_role_runs_exist(db, session)
        for role in (Role.TX, Role.RX):
            role_run = _role_run_for(db, session.id, role, required=False)
            if role_run is not None:
                if role == Role.TX and session.tx_artifact_id:
                    role_run.artifact_id = session.tx_artifact_id
                if role == Role.RX and session.rx_artifact_id:
                    role_run.artifact_id = session.rx_artifact_id
        if session.status == SessionState.BUILDING_ARTIFACTS.value:
            session.status = transition_session(
                SessionState.BUILDING_ARTIFACTS,
                SessionState.AWAITING_HOSTS
                if session.tx_agent_id and session.rx_agent_id and session.tx_artifact_id and session.rx_artifact_id
                else SessionState.SELECTING_ARTIFACTS,
            ).value
        db.commit()
    return artifact


def _mark_session_failed(
    db: Session,
    *,
    settings: ServerSettings,
    session: SessionModel,
    reason: str,
) -> None:
    if session.status not in {SessionState.FAILED.value, SessionState.CANCELLED.value}:
        session.status = transition_session(SessionState(session.status), SessionState.FAILED).value
        session.ended_at = utc_now()
        db.commit()
    log_event(
        db,
        session_id=session.id,
        source_type=EventSourceType.SERVER,
        source_ref=None,
        event_type=EventType.DIAGNOSTIC,
        payload={"failure_reason": reason},
    )
    cleanup_terminal_artifact_bundles(db, settings=settings, session_id=session.id)


def _upsert_report(db: Session, session_id: str) -> Report:
    report = db.query(Report).filter(Report.session_id == session_id).one_or_none()
    if report is None:
        report = Report(session_id=session_id, status=ReportStatus.PENDING.value)
        db.add(report)
        db.commit()
        db.refresh(report)
    return report


def handle_job_result(
    db: Session,
    *,
    settings: ServerSettings,
    job_id: str,
    result: JobResult,
) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    job.status = transition_job(JobState(job.status), JobState.COMPLETED if result.success else JobState.FAILED).value
    job.result_json = result.model_dump(mode="json")
    job.diagnostics_json = result.diagnostics
    job.failure_reason = result.failure_reason
    job.finished_at = utc_now()
    db.commit()
    session = db.get(SessionModel, job.session_id) if job.session_id else None
    if session is not None:
        log_event(
            db,
            session_id=session.id,
            source_type=EventSourceType.AGENT,
            source_ref=job.agent_id,
            event_type=EventType.JOB_UPDATE,
            payload={
                "job_id": job.id,
                "job_type": job.type,
                "status": job.status,
                "success": result.success,
                "failure_reason": result.failure_reason,
            },
        )
    if session is None:
        return job
    if job.type == JobType.BUILD_ARTIFACT.value:
        artifact_id = result.artifact_id or job.payload_json.get("artifact_id")
        if artifact_id:
            artifact = db.get(Artifact, artifact_id)
            if artifact is not None:
                metadata = dict(artifact.metadata_json or {})
                if result.uploaded_raw_artifacts:
                    metadata["uploaded_raw_artifacts"] = result.uploaded_raw_artifacts
                if result.diagnostics.get("build_log_upload_error"):
                    metadata["build_log_upload_error"] = result.diagnostics["build_log_upload_error"]
                artifact.metadata_json = metadata
            if artifact is not None and not result.success:
                artifact.status = ArtifactStatus.FAILED.value
            db.commit()
        if not result.success:
            session.status = transition_session(SessionState(session.status), SessionState.SELECTING_ARTIFACTS).value
            db.commit()
        return job
    role = _coerce_role(job.role) if job.role else None
    role_run = _role_run_for(db, session.id, role) if role else None
    if role_run is not None:
        diagnostics = dict(role_run.diagnostics_json or {})
        diagnostics.update(result.diagnostics)
        if result.time_samples:
            diagnostics["time_sync_samples"] = [sample.model_dump(mode="json") for sample in result.time_samples]
        role_run.diagnostics_json = diagnostics
    if job.type == JobType.PREPARE_ROLE.value:
        if role_run is None:
            return job
        if not result.success:
            role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.FAILED).value
            role_run.failure_reason = result.failure_reason
            db.commit()
            _mark_session_failed(
                db,
                settings=settings,
                session=session,
                reason=result.failure_reason or "prepare role failed",
            )
            return job
        role_run.hidden_probe_identity = result.diagnostics.get("probe_serial")
        role_run.flash_started_at = role_run.flash_started_at or utc_now()
        role_run.flash_finished_at = utc_now()
        role_run.flash_result = result.diagnostics.get("flash_result", "ok")
        role_run.verify_result = result.diagnostics.get("verify_result", "ok")
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.ARTIFACT_READY).value
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.FLASHING).value
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.FLASH_VERIFIED).value
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.PREPARE_CAPTURE).value
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.CAPTURE_READY).value
        db.commit()
        if all(r.status == RoleRunState.CAPTURE_READY.value for r in session_roles(db, session.id)):
            session.status = transition_session(SessionState(session.status), SessionState.READY_TO_CAPTURE).value
            db.commit()
            _dispatch_capture_start(db, settings, session)
        return job
    if job.type == JobType.STOP_CAPTURE.value:
        return job
    if job.type == JobType.START_CAPTURE.value:
        if role_run is None:
            return job
        if not result.success:
            role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.FAILED).value
            role_run.failure_reason = result.failure_reason
            db.commit()
            _mark_session_failed(
                db,
                settings=settings,
                session=session,
                reason=result.failure_reason or "capture failed",
            )
            return job
        role_run.capture_finished_at = utc_now()
        role_run.status = transition_role_run(RoleRunState(role_run.status), RoleRunState.COMPLETED).value
        db.commit()
        if all(r.status == RoleRunState.COMPLETED.value for r in session_roles(db, session.id)):
            session.status = transition_session(SessionState(session.status), SessionState.MERGING).value
            session.ended_at = utc_now()
            session.merge_status = "running"
            session.report_status = ReportStatus.PENDING.value
            _upsert_report(db, session.id)
            db.commit()
            from server.app.services.reporting import generate_report

            generate_report(
                db,
                session=session,
                storage_root=settings.data_dir,
                reports_dir=settings.reports_dir,
                template_dir=Path(__file__).resolve().parent.parent / "templates",
            )
        return job
    return job


def session_report(db: Session, session_id: str) -> Report | None:
    return db.query(Report).filter(Report.session_id == session_id).one_or_none()
