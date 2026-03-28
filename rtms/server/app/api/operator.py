from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from rtms.server.app.api.operator_context import (
    build_hosts_context,
    build_session_detail_context,
    build_sessions_page_context,
)
from rtms.server.app.core.config import get_settings
from rtms.server.app.db.session import get_db
from rtms.server.app.models.entities import Artifact, RawArtifact
from rtms.server.app.presentation import register_template_helpers
from rtms.server.app.services.github import GitHubService
from rtms.server.app.services.live_updates import hosts_change_token, session_change_token
from rtms.server.app.services.parsing import flatten_machine_timeline, merge_session_logs
from rtms.server.app.services.reporting import generate_report
from rtms.server.app.services.sessions import (
    add_annotation,
    assign_artifact,
    assign_hosts,
    delete_terminal_session,
    get_session_or_404,
    is_terminal_session_status,
    list_sessions,
    raw_artifacts,
    request_build,
    session_artifacts,
    session_jobs,
    session_report,
    session_roles,
    start_session,
    stop_session,
    update_session_metadata,
    create_session,
)
from rtms.server.app.services.storage import FileStorage
from rtms.shared.high_altitude_cc import (
    HIGH_ALTITUDE_CC_APP_CONFIG_PATH,
    HIGH_ALTITUDE_CC_REPO_ID,
    high_altitude_cc_build_constraints,
    parse_high_altitude_cc_build_config,
)
from rtms.shared.enums import ArtifactStatus, ReportStatus, Role, SessionState
from rtms.shared.schemas import (
    AnnotationCreateRequest,
    AssignArtifactRequest,
    AssignHostsRequest,
    BuildRequest,
    HighAltitudeCCBuildConfig,
    RepoBuildConfigResponse,
    SessionCreateRequest,
    SessionUpdateRequest,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
register_template_helpers(templates.env)

router = APIRouter(tags=["operator"])


def _github() -> GitHubService:
    return GitHubService(get_settings())


def _storage() -> FileStorage:
    return FileStorage(get_settings().data_dir)


def _get_repo_or_404(repo_id: str, github: GitHubService | None = None):
    github_service = github or _github()
    try:
        return github_service.get_repo(repo_id)
    except KeyError as exc:
        detail = exc.args[0] if exc.args else f"unknown repo_id {repo_id}"
        raise HTTPException(status_code=404, detail=detail) from exc


def _stored_file_response(storage_path: str, *, not_found_detail: str) -> FileResponse:
    try:
        path = _storage().resolve(storage_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=not_found_detail) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail=not_found_detail)
    return FileResponse(path)


def _parse_build_config_json(build_config_json: str | None) -> HighAltitudeCCBuildConfig | None:
    if not build_config_json:
        return None
    try:
        return HighAltitudeCCBuildConfig.model_validate(json.loads(build_config_json))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid build_config_json: {exc}") from exc


def _coerce_operator_build_config(
    repo_id: str,
    build_config: HighAltitudeCCBuildConfig | None,
) -> HighAltitudeCCBuildConfig | None:
    if repo_id != HIGH_ALTITUDE_CC_REPO_ID or build_config is None:
        return build_config
    if build_config.machine_log_detail == 1:
        return build_config
    return build_config.model_copy(update={"machine_log_detail": 1})


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/hosts", response_class=HTMLResponse)
def dashboard_overview(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        name="hosts.html",
        request=request,
        context=build_hosts_context(db),
    )


@router.get("/dashboard/fragment", response_class=HTMLResponse)
@router.get("/hosts/fragment", response_class=HTMLResponse)
def dashboard_fragment(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        name="hosts_fragment.html",
        request=request,
        context=build_hosts_context(db),
    )


@router.get("/sessions", response_class=HTMLResponse)
def sessions_index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        name="sessions.html",
        request=request,
        context=build_sessions_page_context(db),
    )


@router.get("/sessions/new", response_class=HTMLResponse)
def new_session_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        name="session_new.html",
        request=request,
        context={"default_duration": get_settings().default_duration_minutes},
    )


@router.post("/sessions")
def create_session_action(
    name: str = Form(...),
    stop_mode: str = Form("default_duration"),
    selected_duration_minutes: int | None = Form(None),
    initial_notes: str | None = Form(None),
    location_mode: str = Form("none"),
    location_text: str | None = Form(None),
    location_lat: float | None = Form(None),
    location_lon: float | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    session = create_session(
        db,
        get_settings(),
        SessionCreateRequest(
            name=name,
            stop_mode=stop_mode,
            selected_duration_minutes=selected_duration_minutes,
            initial_notes=initial_notes,
            location_mode=location_mode,
            location_text=location_text,
            location_lat=location_lat,
            location_lon=location_lon,
        ),
    )
    return RedirectResponse(url=f"/sessions/{session.id}", status_code=303)


@router.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_detail(request: Request, session_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    _session, context = build_session_detail_context(db, session_id, repos=_github().list_repos())
    return templates.TemplateResponse(
        name="session_detail.html",
        request=request,
        context=context,
    )


@router.get("/sessions/{session_id}/fragment", response_class=HTMLResponse)
def session_detail_fragment(request: Request, session_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    _session, context = build_session_detail_context(db, session_id, repos=_github().list_repos())
    return templates.TemplateResponse(
        name="session_detail_fragment.html",
        request=request,
        context=context,
    )


@router.get("/dashboard/live")
@router.get("/hosts/live")
def dashboard_live(db: Session = Depends(get_db)) -> dict[str, str]:
    return {"version": hosts_change_token(db)}


@router.get("/sessions/{session_id}/live")
def session_live(session_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    session = get_session_or_404(db, session_id)
    return {"version": session_change_token(db, session)}


@router.post("/sessions/{session_id}/metadata")
def session_metadata_action(
    session_id: str,
    name: str | None = Form(None),
    initial_notes: str | None = Form(None),
    final_notes: str | None = Form(None),
    location_mode: str | None = Form(None),
    location_text: str | None = Form(None),
    location_lat: float | None = Form(None),
    location_lon: float | None = Form(None),
    selected_duration_minutes: int | None = Form(None),
    stop_mode: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    update_session_metadata(
        db,
        session_id,
        SessionUpdateRequest(
            name=name,
            initial_notes=initial_notes,
            final_notes=final_notes,
            location_mode=location_mode,
            location_text=location_text,
            location_lat=location_lat,
            location_lon=location_lon,
            selected_duration_minutes=selected_duration_minutes,
            stop_mode=stop_mode,
        ),
    )
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/hosts")
def session_hosts_action(
    session_id: str,
    tx_host_id: str = Form(...),
    rx_host_id: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    assign_hosts(db, session_id, AssignHostsRequest(tx_host_id=tx_host_id, rx_host_id=rx_host_id))
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/artifacts/assign")
def session_assign_artifact_action(
    session_id: str,
    role: str = Form(...),
    artifact_id: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    assign_artifact(db, session_id, AssignArtifactRequest(role=role, artifact_id=artifact_id))
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/builds")
def session_build_action(
    session_id: str,
    repo_id: str = Form(...),
    git_sha: str = Form(...),
    build_host_id: str = Form(...),
    role: str = Form(...),
    build_config_json: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    github = _github()
    repo = _get_repo_or_404(repo_id, github)
    build_config = _coerce_operator_build_config(repo.id, _parse_build_config_json(build_config_json))
    request_build(
        db,
        settings=get_settings(),
        request=BuildRequest(
            session_id=session_id,
            role=role,
            repo_id=repo_id,
            git_sha=git_sha,
            build_host_id=build_host_id,
            build_config=build_config,
        ),
        repo=repo,
    )
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/start")
def session_start_action(request: Request, session_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    base_url = get_settings().effective_public_base_url
    start_session(db, settings=get_settings(), session_id=session_id, base_url=base_url)
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/stop")
def session_stop_action(session_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    stop_session(db, session_id)
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/annotations")
def session_annotation_action(
    session_id: str, text: str = Form(...), db: Session = Depends(get_db)
) -> RedirectResponse:
    add_annotation(db, session_id, AnnotationCreateRequest(text=text))
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/delete")
def session_delete_action(session_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    delete_terminal_session(db, settings=get_settings(), session_id=session_id)
    return RedirectResponse(url="/sessions", status_code=303)


@router.get("/sessions/{session_id}/report", response_class=HTMLResponse)
def report_page(request: Request, session_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    session = get_session_or_404(db, session_id)
    report = session_report(db, session_id)
    if report is None or report.html_storage_path is None:
        report = generate_report(
            db,
            session=session,
            storage_root=get_settings().data_dir,
            reports_dir=get_settings().reports_dir,
            template_dir=Path(__file__).resolve().parent.parent / "templates",
        )
    try:
        html = _storage().read_text(report.html_storage_path)
    except (FileNotFoundError, ValueError):
        report = generate_report(
            db,
            session=session,
            storage_root=get_settings().data_dir,
            reports_dir=get_settings().reports_dir,
            template_dir=Path(__file__).resolve().parent.parent / "templates",
        )
        html = _storage().read_text(report.html_storage_path)
    return templates.TemplateResponse(
        name="report_page.html",
        request=request,
        context={
            "session": session,
            "report_html": html,
            "report_json_url": f"/api/sessions/{session.id}/report/json",
            "raw_artifacts_page_url": f"/sessions/{session.id}/artifacts",
        },
    )


@router.post("/sessions/{session_id}/report/generate")
def report_generate_action(session_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    session = get_session_or_404(db, session_id)
    generate_report(
        db,
        session=session,
        storage_root=get_settings().data_dir,
        reports_dir=get_settings().reports_dir,
        template_dir=Path(__file__).resolve().parent.parent / "templates",
    )
    return RedirectResponse(url=f"/sessions/{session_id}/report", status_code=303)


@router.get("/sessions/{session_id}/artifacts", response_class=HTMLResponse)
def session_raw_artifacts_page(request: Request, session_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    session = get_session_or_404(db, session_id)
    return templates.TemplateResponse(
        name="raw_artifacts.html",
        request=request,
        context={"session": session, "raw_artifacts": raw_artifacts(db, session.id)},
    )


@router.get("/api/sessions")
def sessions_json(db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "id": session.id,
            "name": session.name,
            "status": session.status,
            "started_at": session.started_at,
            "ended_at": session.ended_at,
        }
        for session in list_sessions(db)
    ]


@router.get("/api/sessions/{session_id}")
def session_json(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, session_id)
    return {
        "id": session.id,
        "name": session.name,
        "status": session.status,
        "stop_mode": session.stop_mode,
        "selected_duration_minutes": session.selected_duration_minutes,
        "initial_notes": session.initial_notes,
        "final_notes": session.final_notes,
        "tx_host_id": session.tx_host_id,
        "rx_host_id": session.rx_host_id,
        "tx_artifact_id": session.tx_artifact_id,
        "rx_artifact_id": session.rx_artifact_id,
    }


@router.post("/api/sessions")
def create_session_json(payload: SessionCreateRequest, db: Session = Depends(get_db)) -> dict:
    session = create_session(db, get_settings(), payload)
    return {"id": session.id, "status": session.status}


@router.patch("/api/sessions/{session_id}")
def update_session_json(
    session_id: str, payload: SessionUpdateRequest, db: Session = Depends(get_db)
) -> dict:
    session = update_session_metadata(db, session_id, payload)
    return {"id": session.id, "status": session.status}


@router.post("/api/sessions/{session_id}/hosts")
def assign_hosts_json(
    session_id: str, payload: AssignHostsRequest, db: Session = Depends(get_db)
) -> dict:
    session = assign_hosts(db, session_id, payload)
    return {"id": session.id, "status": session.status}


@router.post("/api/sessions/{session_id}/artifacts/assign")
def assign_artifact_json(
    session_id: str, payload: AssignArtifactRequest, db: Session = Depends(get_db)
) -> dict:
    session = assign_artifact(db, session_id, payload)
    return {"id": session.id, "status": session.status}


@router.post("/api/sessions/{session_id}/builds")
def request_build_json(
    session_id: str, payload: BuildRequest, db: Session = Depends(get_db)
) -> dict:
    if payload.session_id != session_id:
        raise HTTPException(status_code=400, detail="payload session_id must match path session_id")
    github = _github()
    repo = _get_repo_or_404(payload.repo_id, github)
    artifact, job = request_build(db, settings=get_settings(), request=payload, repo=repo)
    return {"artifact_id": artifact.id, "job_id": job.id}


@router.post("/api/sessions/{session_id}/start")
def start_session_json(request: Request, session_id: str, db: Session = Depends(get_db)) -> dict:
    session = start_session(
        db,
        settings=get_settings(),
        session_id=session_id,
        base_url=get_settings().effective_public_base_url,
    )
    return {"id": session.id, "status": session.status}


@router.post("/api/sessions/{session_id}/stop")
def stop_session_json(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = stop_session(db, session_id)
    return {"id": session.id, "status": session.status}


@router.post("/api/sessions/{session_id}/annotations")
def annotation_json(
    session_id: str, payload: AnnotationCreateRequest, db: Session = Depends(get_db)
) -> dict:
    annotation = add_annotation(db, session_id, payload)
    return {"id": annotation.id, "created_at": annotation.created_at}


@router.delete("/api/sessions/{session_id}")
def delete_session_json(session_id: str, db: Session = Depends(get_db)) -> dict:
    delete_terminal_session(db, settings=get_settings(), session_id=session_id)
    return {"status": "deleted", "id": session_id}


@router.get("/api/sessions/{session_id}/artifacts")
def artifacts_json(session_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "id": artifact.id,
            "status": artifact.status,
            "origin_type": artifact.origin_type,
            "source_repo": artifact.source_repo,
            "git_sha": artifact.git_sha,
            "producing_host_id": artifact.producing_host_id,
            "metadata": artifact.metadata_payload,
        }
        for artifact in session_artifacts(db, session_id)
    ]


@router.get("/api/sessions/{session_id}/raw-artifacts")
def raw_artifacts_json(session_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "id": item.id,
            "type": item.type,
            "role": item.role,
            "storage_path": item.storage_path,
            "size_bytes": item.size_bytes,
        }
        for item in raw_artifacts(db, session_id)
    ]


@router.get("/api/sessions/{session_id}/report/json")
def report_json(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = get_session_or_404(db, session_id)
    report = session_report(db, session_id)
    if report is None or report.diagnostics == {}:
        report = generate_report(
            db,
            session=session,
            storage_root=get_settings().data_dir,
            reports_dir=get_settings().reports_dir,
            template_dir=Path(__file__).resolve().parent.parent / "templates",
        )
    return report.diagnostics


@router.get("/api/sessions/{session_id}/timeline")
def timeline_json(session_id: str, db: Session = Depends(get_db)) -> list[dict]:
    session = get_session_or_404(db, session_id)
    merge = merge_session_logs(
        db,
        session=session,
        role_runs=session_roles(db, session.id),
        raw_items=raw_artifacts(db, session.id),
        storage_root=get_settings().data_dir,
    )
    return flatten_machine_timeline(merge)


@router.get("/api/repos")
def repos_json() -> list[dict]:
    github = _github()
    return [repo.model_dump(mode="json") for repo in github.list_repos()]


@router.get("/api/repos/{repo_id}/build-config")
def repo_build_config_json(repo_id: str, git_sha: str) -> dict:
    github = _github()
    repo = _get_repo_or_404(repo_id, github)
    if repo.id != HIGH_ALTITUDE_CC_REPO_ID:
        raise HTTPException(status_code=404, detail=f"build-config endpoint not supported for {repo_id}")
    try:
        source = github.fetch_file_at_ref(repo.id, HIGH_ALTITUDE_CC_APP_CONFIG_PATH, git_sha)
        build_config = parse_high_altitude_cc_build_config(source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RepoBuildConfigResponse(
        repo_id=repo.id,
        git_sha=git_sha,
        build_config=build_config,
        constraints=high_altitude_cc_build_constraints(),
    ).model_dump(mode="json")


@router.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "public_base_url": settings.effective_public_base_url,
        "listen_host": settings.host,
        "port": settings.port,
    }


@router.get("/api/repos/{repo_id}/commits")
def commits_json(repo_id: str, q: str | None = None) -> list[dict]:
    github = _github()
    _get_repo_or_404(repo_id, github)
    try:
        return github.browse_commits(repo_id, query=q)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.storage_path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _stored_file_response(artifact.storage_path, not_found_detail="artifact not found")


@router.get("/api/raw-artifacts/{raw_artifact_id}/download")
def download_raw_artifact(raw_artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    raw = db.get(RawArtifact, raw_artifact_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="raw artifact not found")
    return _stored_file_response(raw.storage_path, not_found_detail="raw artifact not found")
