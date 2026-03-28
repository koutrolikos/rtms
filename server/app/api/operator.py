from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.app.core.config import get_settings
from server.app.db.session import get_db
from server.app.models.entities import AgentHost, Artifact, RawArtifact, Report, Session as SessionModel
from server.app.presentation import register_template_helpers
from server.app.services.agents import visible_agent_status
from server.app.services.github import GitHubService
from server.app.services.live_updates import hosts_change_token, session_change_token
from server.app.services.parsing import flatten_machine_timeline, merge_session_logs
from server.app.services.reporting import generate_report
from server.app.services.sessions import (
    add_annotation,
    annotations,
    assign_artifact,
    assign_hosts,
    delete_terminal_session,
    get_session_or_404,
    is_terminal_session_status,
    list_sessions,
    raw_artifacts,
    request_build,
    session_artifacts,
    session_events,
    session_jobs,
    session_report,
    session_roles,
    start_session,
    stop_session,
    update_session_metadata,
    create_session,
)
from server.app.services.storage import FileStorage
from shared.high_altitude_cc import (
    HIGH_ALTITUDE_CC_APP_CONFIG_PATH,
    HIGH_ALTITUDE_CC_REPO_ID,
    high_altitude_cc_build_constraints,
    parse_high_altitude_cc_build_config,
)
from shared.enums import ArtifactStatus, ReportStatus, Role, SessionState
from shared.schemas import (
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


def _list_hosts(db: Session) -> list[AgentHost]:
    return db.query(AgentHost).order_by(AgentHost.name.asc()).all()


def _report_and_raw_maps(db: Session, session_ids: list[str]) -> tuple[dict[str, Report], dict[str, int]]:
    if not session_ids:
        return {}, {}
    reports = {
        report.session_id: report
        for report in db.query(Report).filter(Report.session_id.in_(session_ids)).all()
    }
    raw_counts = {
        session_id: count
        for session_id, count in (
            db.query(RawArtifact.session_id, func.count(RawArtifact.id))
            .filter(RawArtifact.session_id.in_(session_ids))
            .group_by(RawArtifact.session_id)
            .all()
        )
    }
    return reports, raw_counts


def _session_group(status: str) -> str:
    if status in {SessionState.FAILED.value, SessionState.CANCELLED.value}:
        return "needs_attention"
    if status == SessionState.REPORT_READY.value:
        return "completed"
    return "active"


def _start_blockers(session: SessionModel) -> list[str]:
    blockers: list[str] = []
    if not session.tx_agent_id:
        blockers.append("Assign a TX host.")
    if not session.rx_agent_id:
        blockers.append("Assign an RX host.")
    if not session.tx_artifact_id:
        blockers.append("Assign or build a TX artifact.")
    if not session.rx_artifact_id:
        blockers.append("Assign or build an RX artifact.")
    return blockers


def _next_action_for_session(session: SessionModel, report: Report | None) -> dict[str, str]:
    report_ready = bool(report and report.html_storage_path)
    setup_ready = bool(session.tx_agent_id and session.rx_agent_id)
    artifacts_ready = bool(session.tx_artifact_id and session.rx_artifact_id)
    session_url = f"/sessions/{session.id}"

    if session.status in {SessionState.FAILED.value, SessionState.CANCELLED.value}:
        return {
            "title": "Review the failure state",
            "detail": "Inspect jobs, event log, and role runs before retrying or replacing this session.",
            "action_label": "Open diagnostics",
            "href": f"{session_url}#stage-run",
            "kind": "danger",
        }
    if report_ready or session.status == SessionState.REPORT_READY.value:
        return {
            "title": "Outputs are ready",
            "detail": "The report is available and raw artifacts can be reviewed or downloaded.",
            "action_label": "Open report",
            "href": f"{session_url}/report",
            "kind": "success",
        }
    if session.status == SessionState.CAPTURING.value:
        return {
            "title": "Capture is live",
            "detail": "Monitor host state and record operator annotations while the link is active.",
            "action_label": "Open run controls",
            "href": f"{session_url}#stage-run",
            "kind": "live",
        }
    if not setup_ready:
        return {
            "title": "Assign both hosts",
            "detail": "Choose TX and RX hosts before artifact work and run controls can progress.",
            "action_label": "Open configure stage",
            "href": f"{session_url}#stage-configure",
            "kind": "pending",
        }
    if not artifacts_ready:
        missing_roles = []
        if not session.tx_artifact_id:
            missing_roles.append("TX")
        if not session.rx_artifact_id:
            missing_roles.append("RX")
        slots = " and ".join(missing_roles) if missing_roles else "required"
        return {
            "title": "Build or assign the remaining artifacts",
            "detail": f"The {slots} slot still needs a ready artifact before the run can start.",
            "action_label": "Open artifacts stage",
            "href": f"{session_url}#stage-artifacts",
            "kind": "pending",
        }
    return {
        "title": "Start the session",
        "detail": "Hosts and artifacts are assigned. Move to run controls and begin the capture.",
        "action_label": "Open run controls",
        "href": f"{session_url}#stage-run",
        "kind": "primary",
    }


def _session_summary(
    session: SessionModel,
    *,
    report: Report | None,
    raw_artifact_count: int,
    host_labels: dict[str, str],
) -> dict[str, object]:
    return {
        "id": session.id,
        "name": session.name,
        "status": session.status,
        "group": _session_group(session.status),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "location_text": session.location_text,
        "tx_agent_id": session.tx_agent_id,
        "rx_agent_id": session.rx_agent_id,
        "tx_host_label": host_labels.get(session.tx_agent_id, "Unassigned") if session.tx_agent_id else "Unassigned",
        "rx_host_label": host_labels.get(session.rx_agent_id, "Unassigned") if session.rx_agent_id else "Unassigned",
        "has_report": bool(report and report.html_storage_path),
        "report_status": report.status if report else session.report_status,
        "raw_artifact_count": raw_artifact_count,
        "next_action": _next_action_for_session(session, report),
        "can_delete": is_terminal_session_status(session.status),
    }


def _build_session_summaries(db: Session, host_labels: dict[str, str]) -> list[dict[str, object]]:
    sessions = list_sessions(db)
    reports_by_session, raw_counts = _report_and_raw_maps(db, [session.id for session in sessions])
    return [
        _session_summary(
            session,
            report=reports_by_session.get(session.id),
            raw_artifact_count=raw_counts.get(session.id, 0),
            host_labels=host_labels,
        )
        for session in sessions
    ]


def _grouped_sessions(session_summaries: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = {
        "active": [],
        "needs_attention": [],
        "completed": [],
    }
    for summary in session_summaries:
        groups[str(summary["group"])].append(summary)
    return groups


def _build_hosts_context(db: Session) -> dict[str, object]:
    settings = get_settings()
    hosts = _list_hosts(db)
    host_status = {host.id: visible_agent_status(host, settings) for host in hosts}
    host_labels = {host.id: host.label or host.name for host in hosts}
    session_summaries = _build_session_summaries(db, host_labels)
    host_session_links: dict[str, dict[str, object]] = {}
    host_sessions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for summary in session_summaries:
        for host_id in [summary.get("tx_agent_id"), summary.get("rx_agent_id")]:
            if host_id:
                host_sessions[str(host_id)].append(summary)
    for host in hosts:
        summaries = host_sessions.get(host.id, [])
        active_summary = next((item for item in summaries if item["group"] == "active"), None)
        if active_summary:
            host_session_links[host.id] = active_summary
        elif summaries:
            host_session_links[host.id] = summaries[0]
    last_updated_at = max(
        [host.updated_at for host in hosts] + [summary["updated_at"] for summary in session_summaries if summary.get("updated_at")],
        default=None,
    )
    return {
        "hosts": hosts,
        "host_status": host_status,
        "host_labels": host_labels,
        "host_session_links": host_session_links,
        "sessions": session_summaries[:8],
        "all_session_summaries": session_summaries,
        "settings": settings,
        "live_version": hosts_change_token(db),
        "last_updated_at": last_updated_at,
    }


def _build_sessions_page_context(db: Session) -> dict[str, object]:
    hosts = _list_hosts(db)
    host_labels = {host.id: host.label or host.name for host in hosts}
    session_summaries = _build_session_summaries(db, host_labels)
    grouped = _grouped_sessions(session_summaries)
    return {
        "session_groups": grouped,
        "session_count": len(session_summaries),
        "active_count": len(grouped["active"]),
        "needs_attention_count": len(grouped["needs_attention"]),
        "completed_count": len(grouped["completed"]),
    }


def _build_session_detail_context(db: Session, session_id: str) -> tuple[SessionModel, dict[str, object]]:
    session = get_session_or_404(db, session_id)
    report = session_report(db, session.id)
    github = _github()
    hosts = _list_hosts(db)
    host_labels = {host.id: host.label or host.name for host in hosts}
    raw_items = raw_artifacts(db, session.id)
    context = {
        "session": session,
        "hosts": hosts,
        "host_labels": host_labels,
        "artifacts": session_artifacts(db, session.id),
        "roles": session_roles(db, session.id),
        "annotations": annotations(db, session.id),
        "events": session_events(db, session.id),
        "jobs": session_jobs(db, session.id),
        "raw_artifacts": raw_items,
        "report": report,
        "repos": github.list_repos(),
        "live_version": session_change_token(db, session),
        "next_action": _next_action_for_session(session, report),
        "start_blockers": _start_blockers(session),
        "last_updated_at": session.updated_at,
        "raw_artifacts_page_url": f"/sessions/{session.id}/artifacts",
        "report_json_url": f"/api/sessions/{session.id}/report/json",
        "timeline_json_url": f"/api/sessions/{session.id}/timeline",
        "can_delete_session": is_terminal_session_status(session.status),
    }
    return session, context


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return RedirectResponse(url="/sessions", status_code=303)


@router.get("/hosts", response_class=HTMLResponse)
def hosts_overview(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        name="hosts.html",
        request=request,
        context=_build_hosts_context(db),
    )


@router.get("/hosts/fragment", response_class=HTMLResponse)
def hosts_fragment(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        name="hosts_fragment.html",
        request=request,
        context=_build_hosts_context(db),
    )


@router.get("/sessions", response_class=HTMLResponse)
def sessions_index(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        name="sessions.html",
        request=request,
        context=_build_sessions_page_context(db),
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
    _session, context = _build_session_detail_context(db, session_id)
    return templates.TemplateResponse(
        name="session_detail.html",
        request=request,
        context=context,
    )


@router.get("/sessions/{session_id}/fragment", response_class=HTMLResponse)
def session_detail_fragment(request: Request, session_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    _session, context = _build_session_detail_context(db, session_id)
    return templates.TemplateResponse(
        name="session_detail_fragment.html",
        request=request,
        context=context,
    )


@router.get("/hosts/live")
def hosts_live(db: Session = Depends(get_db)) -> dict[str, str]:
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
    session_id: str,
    repo_id: str = Form(...),
    git_sha: str = Form(...),
    build_agent_id: str = Form(...),
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
            build_agent_id=build_agent_id,
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
