from __future__ import annotations

from sqlalchemy import and_
from sqlalchemy.orm import Session

from rtms.server.app.models.entities import Job
from rtms.shared.enums import JobState, JobType
from rtms.shared.schemas import JobEnvelope
from rtms.shared.state_machine import transition_job
from rtms.shared.time_sync import utc_now


def create_job(
    db: Session,
    *,
    host_id: str,
    job_type: JobType,
    payload: dict,
    session_id: str | None = None,
    role: str | None = None,
) -> Job:
    job = Job(
        host_id=host_id,
        session_id=session_id,
        role=role,
        type=job_type.value,
        status=JobState.PENDING.value,
        payload=payload,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def poll_next_job(db: Session, host_id: str) -> Job | None:
    job = (
        db.query(Job)
        .filter(and_(Job.host_id == host_id, Job.status == JobState.PENDING.value))
        .order_by(Job.created_at.asc())
        .first()
    )
    if job is None:
        return None
    job.status = transition_job(JobState(job.status), JobState.RUNNING).value
    job.started_at = utc_now()
    db.commit()
    db.refresh(job)
    return job


def job_to_envelope(job: Job) -> JobEnvelope:
    return JobEnvelope(
        id=job.id,
        host_id=job.host_id,
        session_id=job.session_id,
        role=job.role,
        type=JobType(job.type),
        state=JobState(job.status),
        payload=job.payload or {},
        created_at=job.created_at,
    )

