from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from server.app.models.entities import (
    AgentHost,
    Annotation,
    Artifact,
    Job,
    RawArtifact,
    Report,
    Session as SessionModel,
    SessionEvent,
    SessionRoleRun,
)


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _serialize_timestamp(value: datetime | None) -> str:
    normalized = _normalize_timestamp(value)
    return normalized.isoformat(timespec="microseconds") if normalized else ""


def _count_and_latest(query, column) -> tuple[int, datetime | None]:
    count, latest = query.with_entities(func.count(), func.max(column)).one()
    return int(count or 0), latest


def hosts_change_token(db: Session) -> str:
    host_count, latest_host_change = _count_and_latest(db.query(AgentHost), AgentHost.updated_at)
    session_count, latest_session_change = _count_and_latest(db.query(SessionModel), SessionModel.updated_at)
    latest_change = max(
        (
            value
            for value in (
                _normalize_timestamp(latest_host_change),
                _normalize_timestamp(latest_session_change),
            )
            if value is not None
        ),
        default=None,
    )
    return "|".join(
        [
            f"hosts:{host_count}:{_serialize_timestamp(latest_host_change)}",
            f"sessions:{session_count}:{_serialize_timestamp(latest_session_change)}",
            f"latest:{_serialize_timestamp(latest_change)}",
        ]
    )


def session_change_token(db: Session, session: SessionModel) -> str:
    artifact_count, latest_artifact_change = _count_and_latest(
        db.query(Artifact).filter(Artifact.session_id == session.id),
        Artifact.updated_at,
    )
    role_run_count, latest_role_run_change = _count_and_latest(
        db.query(SessionRoleRun).filter(SessionRoleRun.session_id == session.id),
        SessionRoleRun.updated_at,
    )
    job_count, latest_job_change = _count_and_latest(
        db.query(Job).filter(Job.session_id == session.id),
        Job.updated_at,
    )
    event_count, latest_event = _count_and_latest(
        db.query(SessionEvent).filter(SessionEvent.session_id == session.id),
        SessionEvent.created_at,
    )
    annotation_count, latest_annotation = _count_and_latest(
        db.query(Annotation).filter(Annotation.session_id == session.id),
        Annotation.created_at,
    )
    raw_artifact_count, latest_raw_artifact = _count_and_latest(
        db.query(RawArtifact).filter(RawArtifact.session_id == session.id),
        RawArtifact.created_at,
    )
    report = db.query(Report).filter(Report.session_id == session.id).one_or_none()

    latest_change = max(
        (
            value
            for value in (
                _normalize_timestamp(session.created_at),
                _normalize_timestamp(session.updated_at),
                _normalize_timestamp(session.started_at),
                _normalize_timestamp(session.ended_at),
                _normalize_timestamp(latest_artifact_change),
                _normalize_timestamp(latest_role_run_change),
                _normalize_timestamp(latest_job_change),
                _normalize_timestamp(latest_event),
                _normalize_timestamp(latest_annotation),
                _normalize_timestamp(latest_raw_artifact),
                _normalize_timestamp(report.generated_at if report else None),
            )
            if value is not None
        ),
        default=None,
    )

    return "|".join(
        [
            f"session:{session.id}",
            f"status:{session.status}",
            f"merge:{session.merge_status}",
            f"report:{session.report_status}",
            f"session-updated:{_serialize_timestamp(session.updated_at)}",
            f"artifacts:{artifact_count}:{_serialize_timestamp(latest_artifact_change)}",
            f"roles:{role_run_count}:{_serialize_timestamp(latest_role_run_change)}",
            f"jobs:{job_count}:{_serialize_timestamp(latest_job_change)}",
            f"events:{event_count}:{_serialize_timestamp(latest_event)}",
            f"annotations:{annotation_count}:{_serialize_timestamp(latest_annotation)}",
            f"raw:{raw_artifact_count}:{_serialize_timestamp(latest_raw_artifact)}",
            (
                f"report-object:{1}:{report.status}:{int(bool(report.html_storage_path))}:"
                f"{_serialize_timestamp(report.generated_at)}"
                if report is not None
                else "report-object:0:missing:0:"
            ),
            f"latest:{_serialize_timestamp(latest_change)}",
        ]
    )
