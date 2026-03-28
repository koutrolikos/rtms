from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from rtms.server.app.models.entities import Host, Report, RunSession
from rtms.server.app.presentation import register_template_helpers
from rtms.server.app.services.parsing import emit_parser_output, merge_session_logs
from rtms.server.app.services.report_metrics import build_derived_metrics
from rtms.server.app.services.sessions import cleanup_terminal_artifact_bundles
from rtms.server.app.services.sessions import raw_artifacts as list_raw_artifacts
from rtms.server.app.services.sessions import register_raw_artifact, session_events, session_report, session_roles
from rtms.shared.enums import RawArtifactType, ReportStatus, SessionState
from rtms.shared.state_machine import transition_session
from rtms.shared.time_sync import utc_now


def generate_report(
    db: Session,
    *,
    session: RunSession,
    storage_root: Path,
    reports_dir: Path,
    template_dir: Path,
) -> Report:
    report = session_report(db, session.id)
    if report is None:
        report = Report(session_id=session.id, status=ReportStatus.PENDING.value)
        db.add(report)
        db.commit()
        db.refresh(report)
    merge = merge_session_logs(
        db,
        session=session,
        role_runs=session_roles(db, session.id),
        raw_items=list_raw_artifacts(db, session.id),
        storage_root=storage_root,
    )
    parser_output_path = reports_dir / session.id / "parser_output.json"
    parser_output_path.parent.mkdir(parents=True, exist_ok=True)
    parser_output_path.write_text(emit_parser_output(merge), encoding="utf-8")
    existing = (
        db.query(Report)
        .filter(Report.session_id == session.id)
        .one()
    )
    register_raw_artifact(
        db,
        session_id=session.id,
        role=None,
        artifact_type=RawArtifactType.PARSER_OUTPUT,
        storage_path=str(parser_output_path.relative_to(storage_root)),
        sha256="generated",
        size_bytes=parser_output_path.stat().st_size,
        metadata={"generated": True},
    )
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    register_template_helpers(environment)
    host_labels = {
        host.id: host.label or host.name
        for host in db.query(Host).order_by(Host.name.asc()).all()
    }
    derived_metrics = build_derived_metrics(merge)
    rendered = environment.get_template("report_fragment.html").render(
        {
            "session": session,
            "merge": merge,
            "derived_metrics": derived_metrics,
            "raw_artifacts": list_raw_artifacts(db, session.id),
            "events": session_events(db, session.id),
            "host_labels": host_labels,
        }
    )
    html_path = reports_dir / session.id / "report.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(rendered, encoding="utf-8")
    existing.status = merge.status.value
    existing.html_storage_path = str(html_path.relative_to(storage_root))
    existing.generated_at = utc_now()
    existing.diagnostics = merge.model_dump(mode="json")
    session.merge_status = "ready" if merge.status == ReportStatus.READY else "failed"
    session.report_status = merge.status.value
    if session.status == SessionState.MERGING.value:
        session.status = transition_session(SessionState(session.status), SessionState.REPORT_READY).value
    db.commit()
    cleanup_terminal_artifact_bundles(
        db,
        storage_root=storage_root,
        session_id=session.id,
    )
    db.refresh(existing)
    return existing


