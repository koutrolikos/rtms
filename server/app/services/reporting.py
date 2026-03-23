from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from server.app.models.entities import AgentHost, Report, Session as SessionModel
from server.app.presentation import register_template_helpers
from server.app.services.parsing import emit_parser_output, merge_session_logs
from server.app.services.sessions import raw_artifacts as list_raw_artifacts
from server.app.services.sessions import register_raw_artifact, session_events, session_report, session_roles
from shared.enums import RawArtifactType, ReportStatus, SessionState
from shared.schemas import MergeReport
from shared.state_machine import transition_session
from shared.time_sync import utc_now


def generate_report(
    db: Session,
    *,
    session: SessionModel,
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
        for host in db.query(AgentHost).order_by(AgentHost.name.asc()).all()
    }
    derived_metrics = _build_derived_metrics(merge)
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
    existing.diagnostics_json = merge.model_dump(mode="json")
    session.merge_status = "ready" if merge.status == ReportStatus.READY else "failed"
    session.report_status = merge.status.value
    if session.status == SessionState.MERGING.value:
        session.status = transition_session(SessionState(session.status), SessionState.REPORT_READY).value
    db.commit()
    db.refresh(existing)
    return existing


def _build_derived_metrics(merge: MergeReport) -> dict[str, Any]:
    rx_report = merge.roles.get("RX")
    tx_report = merge.roles.get("TX")
    rx_frames = sorted(
        (rx_report.packet_frames if rx_report else []),
        key=lambda frame: (frame.t_ms, frame.offset),
    )
    tx_frames = sorted(
        (tx_report.packet_frames if tx_report else []),
        key=lambda frame: (frame.t_ms, frame.offset),
    )

    rx_points: list[dict[str, Any]] = []
    for frame in rx_frames:
        rx_points.append(
            {
                "t_ms": frame.t_ms,
                "seq": frame.seq,
                "length": frame.length,
                "accepted": bool(frame.accepted) if frame.accepted is not None else False,
                "rssi_dbm": frame.rssi_dbm,
                "lqi": frame.lqi,
            }
        )

    points_with_rssi = [point for point in rx_points if point["rssi_dbm"] is not None]
    points_with_lqi = [point for point in rx_points if point["lqi"] is not None]
    accepted_points = [point for point in rx_points if point["accepted"]]

    rssi_relationship = _build_rssi_relationship(points_with_rssi)
    temporal_bins = _build_temporal_quality(points_with_rssi)
    rolling_reliability = _build_rolling_reliability(points_with_rssi, window_size=25)
    inter_arrival_points, inter_arrival_histogram = _build_inter_arrival(accepted_points)
    rssi_distribution = _build_rssi_distribution(points_with_rssi)
    throughput_by_time = _build_throughput_by_time(tx_frames, accepted_points)

    return {
        "rx_packet_count": len(rx_points),
        "rx_packets_with_rssi": len(points_with_rssi),
        "rx_packets_with_lqi": len(points_with_lqi),
        "window_size_packets": 25,
        "pdr_per_relationship_by_rssi": rssi_relationship,
        "temporal_quality_by_time": temporal_bins,
        "rolling_reliability_by_time": rolling_reliability,
        "inter_arrival_points": inter_arrival_points,
        "inter_arrival_histogram": inter_arrival_histogram,
        "rssi_distribution": rssi_distribution,
        "throughput_by_time": throughput_by_time,
    }


def _build_rssi_relationship(points_with_rssi: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points_with_rssi:
        return []
    min_rssi = min(point["rssi_dbm"] for point in points_with_rssi)
    max_rssi = max(point["rssi_dbm"] for point in points_with_rssi)
    step = 5
    lower = int(math.floor(min_rssi / step) * step)
    upper = int(math.ceil((max_rssi + 1) / step) * step)

    bins: list[dict[str, Any]] = []
    for start in range(lower, upper, step):
        end = start + step
        bucket = [point for point in points_with_rssi if start <= point["rssi_dbm"] < end]
        if not bucket:
            continue
        accepted = sum(1 for point in bucket if point["accepted"])
        lqi_values = [point["lqi"] for point in bucket if point["lqi"] is not None]
        pdr = accepted / len(bucket)
        bins.append(
            {
                "rssi_start_dbm": start,
                "rssi_end_dbm": end,
                "total_packets": len(bucket),
                "accepted_packets": accepted,
                "pdr": pdr,
                "per": 1.0 - pdr,
                "avg_lqi": (sum(lqi_values) / len(lqi_values)) if lqi_values else None,
            }
        )
    return bins


def _build_temporal_quality(points_with_rssi: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points_with_rssi:
        return []
    start_ms = points_with_rssi[0]["t_ms"]
    end_ms = points_with_rssi[-1]["t_ms"]
    span_ms = max(1, end_ms - start_ms)
    target_bins = 20
    raw_bin_ms = span_ms / target_bins
    bin_ms = int(min(10_000, max(500, math.ceil(raw_bin_ms / 100.0) * 100)))

    results: list[dict[str, Any]] = []
    bin_start = start_ms
    while bin_start <= end_ms:
        bin_end = bin_start + bin_ms
        bucket = [point for point in points_with_rssi if bin_start <= point["t_ms"] < bin_end]
        if bucket:
            accepted = sum(1 for point in bucket if point["accepted"])
            lqi_values = [point["lqi"] for point in bucket if point["lqi"] is not None]
            pdr = accepted / len(bucket)
            results.append(
                {
                    "start_ms": bin_start,
                    "end_ms": bin_end,
                    "total_packets": len(bucket),
                    "accepted_packets": accepted,
                    "avg_rssi_dbm": sum(point["rssi_dbm"] for point in bucket) / len(bucket),
                    "avg_lqi": (sum(lqi_values) / len(lqi_values)) if lqi_values else None,
                    "pdr": pdr,
                    "per": 1.0 - pdr,
                }
            )
        bin_start = bin_end
    return results


def _build_rolling_reliability(points_with_rssi: list[dict[str, Any]], *, window_size: int) -> list[dict[str, Any]]:
    if len(points_with_rssi) < window_size:
        return []
    results: list[dict[str, Any]] = []
    for index in range(window_size - 1, len(points_with_rssi)):
        window = points_with_rssi[index - window_size + 1:index + 1]
        accepted = sum(1 for point in window if point["accepted"])
        pdr = accepted / window_size
        results.append(
            {
                "t_ms": points_with_rssi[index]["t_ms"],
                "window_packets": window_size,
                "pdr": pdr,
                "per": 1.0 - pdr,
            }
        )
    return results


def _build_inter_arrival(accepted_points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(accepted_points) < 2:
        return [], []

    deltas: list[dict[str, Any]] = []
    for index in range(1, len(accepted_points)):
        current = accepted_points[index]
        previous = accepted_points[index - 1]
        delta = current["t_ms"] - previous["t_ms"]
        if delta < 0:
            continue
        deltas.append({"t_ms": current["t_ms"], "delta_ms": delta})

    if not deltas:
        return [], []

    max_delta = max(item["delta_ms"] for item in deltas)
    step = 50
    upper = int(math.ceil((max_delta + 1) / step) * step)

    histogram: list[dict[str, Any]] = []
    for start in range(0, upper, step):
        end = start + step
        count = sum(1 for item in deltas if start <= item["delta_ms"] < end)
        if count == 0:
            continue
        histogram.append({"delta_start_ms": start, "delta_end_ms": end, "count": count})
    return deltas, histogram


def _build_rssi_distribution(points_with_rssi: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not points_with_rssi:
        return []
    min_rssi = min(point["rssi_dbm"] for point in points_with_rssi)
    max_rssi = max(point["rssi_dbm"] for point in points_with_rssi)
    step = 5
    lower = int(math.floor(min_rssi / step) * step)
    upper = int(math.ceil((max_rssi + 1) / step) * step)

    histogram: list[dict[str, Any]] = []
    for start in range(lower, upper, step):
        end = start + step
        count = sum(1 for point in points_with_rssi if start <= point["rssi_dbm"] < end)
        if count == 0:
            continue
        histogram.append({"rssi_start_dbm": start, "rssi_end_dbm": end, "count": count})
    return histogram


def _build_throughput_by_time(
    tx_frames: list[Any], accepted_points: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    source = "tx_packet_frames"
    for frame in tx_frames:
        samples.append({"t_ms": frame.t_ms, "length": frame.length})
    if not samples:
        source = "rx_accepted_packet_frames"
        for point in accepted_points:
            samples.append({"t_ms": point["t_ms"], "length": point["length"]})
    if not samples:
        return []

    samples.sort(key=lambda item: item["t_ms"])
    start_ms = samples[0]["t_ms"]
    end_ms = samples[-1]["t_ms"]
    span_ms = max(1, end_ms - start_ms)
    target_bins = 20
    raw_bin_ms = span_ms / target_bins
    bin_ms = int(min(10_000, max(500, math.ceil(raw_bin_ms / 100.0) * 100)))

    throughput: list[dict[str, Any]] = []
    bin_start = start_ms
    while bin_start <= end_ms:
        bin_end = bin_start + bin_ms
        bucket = [sample for sample in samples if bin_start <= sample["t_ms"] < bin_end]
        if bucket:
            bytes_total = sum(sample["length"] for sample in bucket)
            throughput.append(
                {
                    "start_ms": bin_start,
                    "end_ms": bin_end,
                    "packet_count": len(bucket),
                    "bytes": bytes_total,
                    "bits_per_sec": (bytes_total * 8_000.0) / bin_ms,
                    "source": source,
                }
            )
        bin_start = bin_end
    return throughput
