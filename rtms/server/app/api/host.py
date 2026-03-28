from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from rtms.server.app.core.config import get_settings
from rtms.server.app.db.session import get_db
from rtms.server.app.models.entities import Artifact
from rtms.server.app.services.hosts import heartbeat_host, register_host
from rtms.server.app.services.jobs import job_to_envelope, poll_next_job
from rtms.server.app.services.sessions import (
    apply_artifact_upload,
    canonical_raw_artifact_storage_path,
    create_manual_artifact_record,
    get_session_or_404,
    handle_job_result,
    register_raw_artifact,
)
from rtms.server.app.services.storage import FileStorage
from rtms.shared.enums import HostStatus, ArtifactOriginType, RawArtifactType, Role
from rtms.shared.schemas import (
    HostHeartbeatRequest,
    HostHeartbeatResponse,
    HostPollRequest,
    HostPollResponse,
    HostRegistrationRequest,
    HostRegistrationResponse,
    ArtifactUploadResult,
    JobResult,
    RawArtifactUploadResult,
)
from rtms.shared.time_sync import utc_now

router = APIRouter(prefix="/api/host", tags=["host"])


def _storage() -> FileStorage:
    return FileStorage(get_settings().data_dir)


def _request_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        candidate = forwarded_for.split(",")[0].strip()
        if candidate:
            return candidate
    return request.client.host if request.client else None


def _parse_enum(enum_type, value: str, field_name: str):
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid {field_name}: {value}") from exc


def _parse_optional_role(role: str | None) -> Role | None:
    if not role:
        return None
    return _parse_enum(Role, role, "role")


def _parse_metadata_json(metadata: str | None) -> dict:
    if not metadata:
        return {}
    try:
        payload = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid metadata: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="metadata must decode to a JSON object")
    return payload


def _safe_upload_filename(file: UploadFile) -> str:
    raw = (file.filename or "").strip()
    candidate = PurePosixPath(raw.replace("\\", "/")).name
    if candidate in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="uploaded file must include a valid filename")
    return candidate


def _artifact_for_upload(
    db: Session,
    *,
    session_id: str,
    artifact_id: str | None,
    origin_type: str,
    producing_host_id: str | None,
    role_hint: str | None,
    source_repo: str | None,
    git_sha: str | None,
) -> Artifact:
    if artifact_id:
        artifact = db.get(Artifact, artifact_id)
        if artifact is None or artifact.session_id != session_id:
            raise HTTPException(status_code=404, detail="artifact not found")
        return artifact
    return create_manual_artifact_record(
        db,
        session_id=session_id,
        origin_type=_parse_enum(ArtifactOriginType, origin_type, "origin_type"),
        producing_host_id=producing_host_id,
        role_hint=_parse_optional_role(role_hint),
        source_repo=source_repo,
        git_sha=git_sha,
    )


def _stored_file_response(storage_path: str, *, not_found_detail: str) -> FileResponse:
    try:
        path = _storage().resolve(storage_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=not_found_detail) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(path)


@router.post("/register", response_model=HostRegistrationResponse)
def register(
    payload: HostRegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> HostRegistrationResponse:
    host = register_host(
        db,
        payload.model_copy(update={"ip_address": payload.ip_address or _request_ip(request)}),
    )
    return HostRegistrationResponse(host_id=host.id, server_time=utc_now())


@router.post("/heartbeat", response_model=HostHeartbeatResponse)
def heartbeat(
    payload: HostHeartbeatRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> HostHeartbeatResponse:
    heartbeat_host(
        db,
        payload.model_copy(update={"ip_address": payload.ip_address or _request_ip(request)}),
    )
    return HostHeartbeatResponse(server_time=utc_now())


@router.get("/time-sync")
def time_sync() -> dict[str, str]:
    return {"server_time": utc_now().isoformat()}


@router.post("/poll", response_model=HostPollResponse)
def poll(request: HostPollRequest, db: Session = Depends(get_db)) -> HostPollResponse:
    job = poll_next_job(db, request.host_id)
    return HostPollResponse(server_time=utc_now(), job=job_to_envelope(job) if job else None)


@router.post("/jobs/{job_id}/result")
def job_result(job_id: str, result: JobResult, db: Session = Depends(get_db)) -> dict[str, str]:
    handle_job_result(db, settings=get_settings(), job_id=job_id, result=result)
    return {"status": "ok"}


@router.post("/artifacts/upload", response_model=ArtifactUploadResult)
async def upload_artifact(
    artifact_bundle: UploadFile = File(...),
    session_id: str = Form(...),
    artifact_id: str | None = Form(None),
    origin_type: str = Form(ArtifactOriginType.LOCAL_HOST_BUILD.value),
    producing_host_id: str | None = Form(None),
    role_hint: str | None = Form(None),
    source_repo: str | None = Form(None),
    git_sha: str | None = Form(None),
    db: Session = Depends(get_db),
) -> ArtifactUploadResult:
    get_session_or_404(db, session_id)
    artifact = _artifact_for_upload(
        db,
        session_id=session_id,
        artifact_id=artifact_id,
        origin_type=origin_type,
        producing_host_id=producing_host_id,
        role_hint=role_hint,
        source_repo=source_repo,
        git_sha=git_sha,
    )
    storage = _storage()
    relative_path = Path("artifacts") / session_id / artifact.id / "bundle.zip"
    stored_path, sha256, _size = storage.save_upload(artifact_bundle.file, relative_path)
    try:
        manifest = storage.parse_bundle_manifest(stored_path)
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        storage.resolve(stored_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"invalid artifact bundle: {exc}") from exc
    if manifest.session_id and manifest.session_id != session_id:
        storage.resolve(stored_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="artifact bundle session_id does not match upload session")
    if manifest.artifact_id and manifest.artifact_id != artifact.id:
        storage.resolve(stored_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="artifact bundle artifact_id does not match target artifact")
    metadata = manifest.model_dump(mode="json")
    apply_artifact_upload(
        db,
        artifact_id=artifact.id,
        storage_path=stored_path,
        sha256=sha256,
        metadata=metadata,
        producing_host_id=producing_host_id,
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
    metadata: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RawArtifactUploadResult:
    get_session_or_404(db, session_id)
    storage = _storage()
    role_value = _parse_optional_role(role)
    artifact_type_value = _parse_enum(RawArtifactType, artifact_type, "artifact_type")
    metadata = _parse_metadata_json(metadata)
    filename = _safe_upload_filename(file)
    try:
        relative_path = canonical_raw_artifact_storage_path(
            session_id=session_id,
            artifact_type=artifact_type_value,
            role=role_value,
            metadata=metadata,
            filename=filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    stored_path, sha256, size = storage.save_upload(file.file, relative_path)
    raw = register_raw_artifact(
        db,
        session_id=session_id,
        role=role_value,
        artifact_type=artifact_type_value,
        storage_path=stored_path,
        sha256=sha256,
        size_bytes=size,
        metadata=metadata,
    )
    return RawArtifactUploadResult(
        raw_artifact_id=raw.id,
        storage_path=stored_path,
        sha256=sha256,
        size_bytes=size,
    )


@router.get("/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.storage_path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _stored_file_response(artifact.storage_path, not_found_detail="artifact not found")
