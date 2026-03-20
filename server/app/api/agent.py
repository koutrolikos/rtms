from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.db.session import get_db
from server.app.models.entities import Artifact
from server.app.services.agents import heartbeat_agent, register_agent
from server.app.services.jobs import job_to_envelope, poll_next_job
from server.app.services.sessions import (
    apply_artifact_upload,
    create_manual_artifact_record,
    get_session_or_404,
    handle_job_result,
    register_raw_artifact,
)
from server.app.services.storage import FileStorage
from shared.enums import AgentStatus, ArtifactOriginType, RawArtifactType, Role
from shared.schemas import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentPollRequest,
    AgentPollResponse,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    ArtifactUploadResult,
    JobResult,
    RawArtifactUploadResult,
)
from shared.time_sync import utc_now

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _storage() -> FileStorage:
    return FileStorage(get_settings().data_dir)


@router.post("/register", response_model=AgentRegistrationResponse)
def register(request: AgentRegistrationRequest, db: Session = Depends(get_db)) -> AgentRegistrationResponse:
    agent = register_agent(db, request)
    return AgentRegistrationResponse(agent_id=agent.id, server_time=utc_now())


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
def heartbeat(request: AgentHeartbeatRequest, db: Session = Depends(get_db)) -> AgentHeartbeatResponse:
    heartbeat_agent(db, request)
    return AgentHeartbeatResponse(server_time=utc_now())


@router.get("/time-sync")
def time_sync() -> dict[str, str]:
    return {"server_time": utc_now().isoformat()}


@router.post("/poll", response_model=AgentPollResponse)
def poll(request: AgentPollRequest, db: Session = Depends(get_db)) -> AgentPollResponse:
    job = poll_next_job(db, request.agent_id)
    return AgentPollResponse(server_time=utc_now(), job=job_to_envelope(job) if job else None)


@router.post("/jobs/{job_id}/result")
def job_result(job_id: str, result: JobResult, db: Session = Depends(get_db)) -> dict[str, str]:
    handle_job_result(db, settings=get_settings(), job_id=job_id, result=result)
    return {"status": "ok"}


@router.post("/artifacts/upload", response_model=ArtifactUploadResult)
async def upload_artifact(
    artifact_bundle: UploadFile = File(...),
    session_id: str = Form(...),
    artifact_id: str | None = Form(None),
    origin_type: str = Form(ArtifactOriginType.LOCAL_AGENT_BUILD.value),
    producing_agent_id: str | None = Form(None),
    role_hint: str | None = Form(None),
    source_repo: str | None = Form(None),
    git_sha: str | None = Form(None),
    db: Session = Depends(get_db),
) -> ArtifactUploadResult:
    get_session_or_404(db, session_id)
    artifact = (
        db.get(Artifact, artifact_id)
        if artifact_id
        else create_manual_artifact_record(
            db,
            session_id=session_id,
            origin_type=ArtifactOriginType(origin_type),
            producing_agent_id=producing_agent_id,
            role_hint=Role(role_hint) if role_hint else None,
            source_repo=source_repo,
            git_sha=git_sha,
        )
    )
    storage = _storage()
    relative_path = Path("artifacts") / session_id / artifact.id / "bundle.zip"
    stored_path, sha256, _size = storage.save_upload(artifact_bundle.file, relative_path)
    manifest = storage.parse_bundle_manifest(stored_path)
    metadata = manifest.model_dump(mode="json")
    apply_artifact_upload(
        db,
        artifact_id=artifact.id,
        storage_path=stored_path,
        sha256=sha256,
        metadata=metadata,
        producing_agent_id=producing_agent_id,
    )
    return ArtifactUploadResult(
        artifact_id=artifact.id,
        storage_path=stored_path,
        sha256=sha256,
        manifest=manifest,
    )


@router.post("/raw-artifacts/upload", response_model=RawArtifactUploadResult)
async def upload_raw_artifact(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    artifact_type: str = Form(...),
    role: str | None = Form(None),
    metadata_json: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RawArtifactUploadResult:
    get_session_or_404(db, session_id)
    storage = _storage()
    relative_path = Path("raw") / session_id / (role or "session") / file.filename
    stored_path, sha256, size = storage.save_upload(file.file, relative_path)
    raw = register_raw_artifact(
        db,
        session_id=session_id,
        role=Role(role) if role else None,
        artifact_type=RawArtifactType(artifact_type),
        storage_path=stored_path,
        sha256=sha256,
        size_bytes=size,
        metadata=json.loads(metadata_json) if metadata_json else {},
    )
    return RawArtifactUploadResult(
        raw_artifact_id=raw.id,
        storage_path=stored_path,
        sha256=sha256,
        size_bytes=size,
    )


@router.get("/artifacts/{artifact_id}/download")
def agent_download_artifact(artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.storage_path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = get_settings().data_dir / artifact.storage_path
    return FileResponse(path)

