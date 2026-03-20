from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.db.session import get_db
from server.app.models.entities import AgentHost, Artifact, RawArtifact
from server.app.services.agents import visible_agent_status
from server.app.services.github import GitHubService
from server.app.services.parsing import merge_session_logs
from server.app.services.reporting import generate_report
from server.app.services.sessions import (
    add_annotation,
    annotations,
    assign_artifact,
    assign_hosts,
    get_session_or_404,
    list_sessions,
    raw_artifacts,
    request_build,
    session_artifacts,
    session_events,
    session_report,
    session_roles,
    start_session,
    stop_session,
    update_session_metadata,
    create_session,
)
from shared.enums import ArtifactStatus, Role
from shared.schemas import (
    AnnotationCreateRequest,
    AssignArtifactRequest,
    AssignHostsRequest,
    BuildRequest,
    SessionCreateRequest,
    SessionUpdateRequest,
)

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

router = APIRouter(tags=["operator"])


def _github() -> GitHubService:
    return GitHubService(get_settings())


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    sessions = list_sessions(db)
    if sessions:
        return RedirectResponse(url=f"/sessions/{sessions[0].id}", status_code=303)
    return RedirectResponse(url="/hosts", status_code=303)


@router.get("/hosts", response_class=HTMLResponse)
def hosts_overview(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    settings = get_settings()
    hosts = db.query(AgentHost).order_by(AgentHost.name.asc()).all()
    return templates.TemplateResponse(
        name="hosts.html",
        request=request,
        context={
            "hosts": hosts,
            "host_status": {host.id: visible_agent_status(host, settings) for host in hosts},
            "sessions": list_sessions(db),
        },
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
    session = get_session_or_404(db, session_id)
    report = session_report(db, session.id)
    github = _github()
    return templates.TemplateResponse(
        name="session_detail.html",
        request=request,
        context={
            "session": session,
            "hosts": db.query(AgentHost).order_by(AgentHost.name.asc()).all(),
            "artifacts": session_artifacts(db, session.id),
            "roles": session_roles(db, session.id),
            "annotations": annotations(db, session.id),
            "events": session_events(db, session.id),
            "raw_artifacts": raw_artifacts(db, session.id),
            "report": report,
            "repos": github.list_repos(),
        },
    )


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
    tx_agent_id: str = Form(...),
    rx_agent_id: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    assign_hosts(db, session_id, AssignHostsRequest(tx_agent_id=tx_agent_id, rx_agent_id=rx_agent_id))
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
    request: Request,
    session_id: str,
    repo_id: str = Form(...),
    git_sha: str = Form(...),
    build_agent_id: str = Form(...),
    role: str | None = Form(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    github = _github()
    repo = github.get_repo(repo_id)
    request_build(
        db,
        settings=get_settings(),
        request=BuildRequest(
            session_id=session_id,
            role=role,
            repo_id=repo_id,
            git_sha=git_sha,
            build_agent_id=build_agent_id,
        ),
        repo=repo,
    )
    return RedirectResponse(url=f"/sessions/{session_id}", status_code=303)


@router.post("/sessions/{session_id}/start")
def session_start_action(request: Request, session_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    base_url = str(request.base_url).rstrip("/")
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
    html = (get_settings().data_dir / report.html_storage_path).read_text(encoding="utf-8")
    return templates.TemplateResponse(
        name="report_page.html",
        request=request,
        context={"session": session, "report_html": html},
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
        "tx_agent_id": session.tx_agent_id,
        "rx_agent_id": session.rx_agent_id,
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
    repo = _github().get_repo(payload.repo_id)
    artifact, job = request_build(db, settings=get_settings(), request=payload, repo=repo)
    return {"artifact_id": artifact.id, "job_id": job.id}


@router.post("/api/sessions/{session_id}/start")
def start_session_json(request: Request, session_id: str, db: Session = Depends(get_db)) -> dict:
    session = start_session(
        db,
        settings=get_settings(),
        session_id=session_id,
        base_url=str(request.base_url).rstrip("/"),
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


@router.get("/api/sessions/{session_id}/artifacts")
def artifacts_json(session_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "id": artifact.id,
            "status": artifact.status,
            "origin_type": artifact.origin_type,
            "source_repo": artifact.source_repo,
            "git_sha": artifact.git_sha,
            "producing_agent_id": artifact.producing_agent_id,
            "metadata": artifact.metadata_json,
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
    if report is None or report.diagnostics_json == {}:
        report = generate_report(
            db,
            session=session,
            storage_root=get_settings().data_dir,
            reports_dir=get_settings().reports_dir,
            template_dir=Path(__file__).resolve().parent.parent / "templates",
        )
    return report.diagnostics_json


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
    return [event.model_dump(mode="json") for event in merge.merged_events]


@router.get("/api/repos")
def repos_json() -> list[dict]:
    github = _github()
    return [repo.model_dump(mode="json") for repo in github.list_repos()]


@router.get("/api/repos/{repo_id}/commits")
def commits_json(repo_id: str, q: str | None = None) -> list[dict]:
    return _github().browse_commits(repo_id, query=q)


@router.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.storage_path is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(get_settings().data_dir / artifact.storage_path)


@router.get("/api/raw-artifacts/{raw_artifact_id}/download")
def download_raw_artifact(raw_artifact_id: str, db: Session = Depends(get_db)) -> FileResponse:
    raw = db.get(RawArtifact, raw_artifact_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="raw artifact not found")
    return FileResponse(get_settings().data_dir / raw.storage_path)
