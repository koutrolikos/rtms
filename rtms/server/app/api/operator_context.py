from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from rtms.server.app.core.config import get_settings
from rtms.server.app.models.entities import Host, RawArtifact, Report, RunSession
from rtms.server.app.services.hosts import visible_host_status
from rtms.server.app.services.live_updates import hosts_change_token, session_change_token
from rtms.server.app.services.sessions import (
    annotations,
    get_session_or_404,
    is_terminal_session_status,
    list_sessions,
    raw_artifacts,
    session_artifacts,
    session_events,
    session_jobs,
    session_report,
    session_roles,
)
from rtms.shared.enums import SessionState
from rtms.shared.schemas import ConfiguredRepo


def _list_hosts(db: Session) -> list[Host]:
    return db.query(Host).order_by(Host.name.asc()).all()


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


def _start_blockers(session: RunSession) -> list[str]:
    blockers: list[str] = []
    if not session.tx_host_id:
        blockers.append("Assign a TX host.")
    if not session.rx_host_id:
        blockers.append("Assign an RX host.")
    if not session.tx_artifact_id:
        blockers.append("Assign or build a TX artifact.")
    if not session.rx_artifact_id:
        blockers.append("Assign or build an RX artifact.")
    return blockers


def _next_action_for_session(session: RunSession, report: Report | None) -> dict[str, str]:
    report_ready = bool(report and report.html_storage_path)
    setup_ready = bool(session.tx_host_id and session.rx_host_id)
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
    session: RunSession,
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
        "tx_host_id": session.tx_host_id,
        "rx_host_id": session.rx_host_id,
        "tx_host_label": host_labels.get(session.tx_host_id, "Unassigned") if session.tx_host_id else "Unassigned",
        "rx_host_label": host_labels.get(session.rx_host_id, "Unassigned") if session.rx_host_id else "Unassigned",
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


def build_hosts_context(db: Session) -> dict[str, object]:
    settings = get_settings()
    hosts = _list_hosts(db)
    host_status = {host.id: visible_host_status(host, settings) for host in hosts}
    host_labels = {host.id: host.label or host.name for host in hosts}
    session_summaries = _build_session_summaries(db, host_labels)
    host_session_links: dict[str, dict[str, object]] = {}
    host_sessions: dict[str, list[dict[str, object]]] = defaultdict(list)
    for summary in session_summaries:
        for host_id in [summary.get("tx_host_id"), summary.get("rx_host_id")]:
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
        [host.updated_at for host in hosts]
        + [summary["updated_at"] for summary in session_summaries if summary.get("updated_at")],
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


def build_sessions_page_context(db: Session) -> dict[str, object]:
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


def build_session_detail_context(
    db: Session,
    session_id: str,
    *,
    repos: list[ConfiguredRepo],
) -> tuple[RunSession, dict[str, object]]:
    session = get_session_or_404(db, session_id)
    report = session_report(db, session.id)
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
        "repos": repos,
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
