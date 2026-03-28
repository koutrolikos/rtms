from __future__ import annotations

import math
from collections import Counter
from typing import Any

from rtms.shared.enums import ReportStatus
from rtms.shared.schemas import MergeReport


def build_derived_metrics(merge: MergeReport) -> dict[str, Any]:
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
                "stream_id": frame.stream_id,
                "type_id": frame.type_id,
                "seq": frame.seq,
                "length": frame.length,
                "accepted": bool(frame.accepted) if frame.accepted is not None else False,
                "rssi_dbm": frame.rssi_dbm,
                "lqi": frame.lqi,
                "drop_reason": frame.drop_reason,
                "crc": frame.crc,
            }
        )

    points_with_rssi = [point for point in rx_points if point["rssi_dbm"] is not None]
    points_with_lqi = [point for point in rx_points if point["lqi"] is not None]
    accepted_points = [point for point in rx_points if point["accepted"]]
    rejected_points = [point for point in rx_points if not point["accepted"]]

    rssi_relationship = _build_rssi_relationship(points_with_rssi)
    link_time_bins = _build_link_time_bins(tx_frames, rx_points)
    worst_link_bins = _select_worst_link_bins(link_time_bins)
    rolling_reliability = _build_rolling_reliability(link_time_bins, window_size=5)
    inter_arrival_points, inter_arrival_histogram = _build_inter_arrival(accepted_points)
    rssi_distribution = _build_rssi_distribution(points_with_rssi)
    throughput_by_time = _build_throughput_by_time(link_time_bins)
    channel_snapshot = _build_channel_snapshot(tx_report, rx_report)
    signal_summary = _build_signal_summary(
        accepted_points=accepted_points,
        rejected_points=rejected_points,
        rx_min_rssi_dbm=(rx_report.run.rx_min_rssi_dbm if rx_report and rx_report.run else None),
        rx_min_lqi=(rx_report.run.rx_min_lqi if rx_report and rx_report.run else None),
    )
    drop_reason_breakdown = _build_drop_reason_breakdown(rejected_points)
    tx_health = _build_tx_health_summary(tx_report)
    rx_health = _build_rx_health_summary(rx_report)
    overview = _build_link_overview(
        tx_frames=tx_frames,
        rx_points=rx_points,
        tx_health=tx_health,
        rx_health=rx_health,
        signal_summary=signal_summary,
        drop_reason_breakdown=drop_reason_breakdown,
        session_span_ms=_compute_session_span_ms(tx_report, rx_report),
    )
    quality_bars = _build_quality_bars(overview, rx_health)
    packet_type_breakdown = _build_packet_type_breakdown(tx_frames, rx_points)
    tx_timing = _build_tx_timing_summary(tx_frames, tx_report)
    channel_events = _build_channel_events(merge)

    return {
        "rx_packet_count": len(rx_points),
        "rx_packets_with_rssi": len(points_with_rssi),
        "rx_packets_with_lqi": len(points_with_lqi),
        "window_size_packets": 25,
        "link_overview": overview,
        "quality_bars": quality_bars,
        "channel_snapshot": channel_snapshot,
        "signal_summary": signal_summary,
        "drop_reason_breakdown": drop_reason_breakdown,
        "packet_type_breakdown": packet_type_breakdown,
        "link_time_bins": link_time_bins,
        "worst_link_bins": worst_link_bins,
        "tx_health": tx_health,
        "rx_health": rx_health,
        "tx_timing_summary": tx_timing,
        "channel_events": channel_events,
        "pdr_per_relationship_by_rssi": rssi_relationship,
        "temporal_quality_by_time": link_time_bins,
        "rolling_reliability_by_time": rolling_reliability,
        "inter_arrival_points": inter_arrival_points,
        "inter_arrival_histogram": inter_arrival_histogram,
        "rssi_distribution": rssi_distribution,
        "throughput_by_time": throughput_by_time,
    }


def _compute_session_span_ms(tx_report: Any, rx_report: Any) -> int | None:
    endpoints: list[int] = []
    for role_report in [tx_report, rx_report]:
        if role_report is None:
            continue
        if role_report.run is not None:
            endpoints.append(role_report.run.t_ms)
        if role_report.final_stat is not None:
            endpoints.append(role_report.final_stat.t_ms)
        endpoints.extend(frame.t_ms for frame in role_report.packet_frames)
        endpoints.extend(frame.t_ms for frame in role_report.event_frames)
    if not endpoints:
        return None
    return max(endpoints) - min(endpoints)


def _build_link_overview(
    *,
    tx_frames: list[Any],
    rx_points: list[dict[str, Any]],
    tx_health: dict[str, Any],
    rx_health: dict[str, Any],
    signal_summary: dict[str, Any],
    drop_reason_breakdown: list[dict[str, Any]],
    session_span_ms: int | None,
) -> dict[str, Any]:
    tx_packets = len(tx_frames)
    rx_seen_packets = len(rx_points)
    rx_accepted_packets = sum(1 for point in rx_points if point["accepted"])
    rx_rejected_packets = rx_seen_packets - rx_accepted_packets

    delivery_ratio = _safe_ratio(rx_accepted_packets, tx_packets)
    visibility_ratio = _safe_ratio(rx_seen_packets, tx_packets)
    acceptance_ratio = _safe_ratio(rx_accepted_packets, rx_seen_packets)
    crc_clean_ratio = rx_health.get("crc_clean_ratio")
    dominant_drop_reason = drop_reason_breakdown[0]["drop_reason"] if drop_reason_breakdown else None

    headline = "Insufficient telemetry"
    reason = "No TX or RX packet detail was decoded for this session."

    if tx_packets == 0 and rx_seen_packets > 0:
        headline = "RX-only telemetry"
        reason = (
            f"RX observed {rx_seen_packets} packets and accepted {rx_accepted_packets}, "
            "but there is no TX packet timeline to estimate end-to-end delivery."
        )
    elif tx_packets > 0 and rx_seen_packets == 0:
        headline = "No RX visibility"
        reason = f"TX logged {tx_packets} packets, but RX reported no packet-level observations."
    elif tx_packets > 0:
        headline = _classify_link(delivery_ratio, visibility_ratio, acceptance_ratio, crc_clean_ratio, rx_health)
        if visibility_ratio is not None and acceptance_ratio is not None and visibility_ratio + 0.08 < acceptance_ratio:
            issue = "Most loss happens before RX acceptance, so the weak point is visibility rather than RX filtering."
        elif acceptance_ratio is not None and visibility_ratio is not None and acceptance_ratio + 0.08 < visibility_ratio:
            drop_note = f", dominated by {dominant_drop_reason}" if dominant_drop_reason else ""
            issue = f"RX sees the traffic but rejects a meaningful share{drop_note}."
        elif crc_clean_ratio is not None and crc_clean_ratio < 0.97:
            issue = "CRC failures are a visible part of the loss budget."
        elif (rx_health.get("rx_overflow_count") or 0) > 0:
            issue = "Receiver overflow counters are non-zero, so host or FIFO pressure may contribute."
        else:
            issue = "RX accepts nearly everything it sees."
        reason = (
            f"RX accepted {rx_accepted_packets} of {tx_packets} TX packets "
            f"({(delivery_ratio or 0.0) * 100:.1f}%). "
            f"RX saw {(visibility_ratio or 0.0) * 100:.1f}% of TX traffic and accepted "
            f"{(acceptance_ratio or 0.0) * 100:.1f}% of what it saw. {issue}"
        )

    accepted_rssi = signal_summary.get("accepted_rssi", {})
    accepted_lqi = signal_summary.get("accepted_lqi", {})
    return {
        "headline": headline,
        "reason": reason,
        "tx_packets": tx_packets,
        "rx_seen_packets": rx_seen_packets,
        "rx_accepted_packets": rx_accepted_packets,
        "rx_rejected_packets": rx_rejected_packets,
        "delivery_ratio": delivery_ratio,
        "visibility_ratio": visibility_ratio,
        "acceptance_ratio": acceptance_ratio,
        "session_span_ms": session_span_ms,
        "accepted_rssi_p50_dbm": accepted_rssi.get("p50"),
        "accepted_rssi_p10_dbm": accepted_rssi.get("p10"),
        "accepted_lqi_p50": accepted_lqi.get("p50"),
        "dominant_drop_reason": dominant_drop_reason,
        "crc_fail_count": rx_health.get("rx_crc_fail_count"),
        "crc_clean_ratio": crc_clean_ratio,
        "overflow_count": rx_health.get("rx_overflow_count"),
        "tx_completed_count": tx_health.get("completed_count"),
    }


def _classify_link(
    delivery_ratio: float | None,
    visibility_ratio: float | None,
    acceptance_ratio: float | None,
    crc_clean_ratio: float | None,
    rx_health: dict[str, Any],
) -> str:
    if delivery_ratio is None:
        return "Partial telemetry"
    if delivery_ratio >= 0.95 and (crc_clean_ratio is None or crc_clean_ratio >= 0.99) and (rx_health.get("rx_overflow_count") or 0) == 0:
        return "Strong link"
    if delivery_ratio >= 0.8 and (acceptance_ratio is None or acceptance_ratio >= 0.9):
        return "Usable link"
    if visibility_ratio is not None and visibility_ratio < 0.6:
        return "Visibility-limited link"
    if acceptance_ratio is not None and acceptance_ratio < 0.85:
        return "Filter-limited link"
    return "Degraded link"


def _build_quality_bars(overview: dict[str, Any], rx_health: dict[str, Any]) -> list[dict[str, Any]]:
    bars = [
        _quality_bar(
            "TX -> RX accepted",
            overview.get("delivery_ratio"),
            "Best end-to-end view available from current packet detail.",
        ),
        _quality_bar(
            "TX -> RX seen",
            overview.get("visibility_ratio"),
            "How much TX traffic RX decoded enough to log at all.",
        ),
        _quality_bar(
            "RX seen -> accepted",
            overview.get("acceptance_ratio"),
            "How often RX-kept packets passed thresholding and filtering.",
        ),
    ]
    if rx_health.get("crc_clean_ratio") is not None:
        bars.append(
            _quality_bar(
                "CRC-clean reception",
                rx_health.get("crc_clean_ratio"),
                "Share of RX visibility that was not lost to CRC failure.",
            )
        )
    return bars


def _quality_bar(label: str, ratio: float | None, detail: str) -> dict[str, Any]:
    tone = "ok" if ratio is not None and ratio >= 0.9 else "warn"
    return {
        "label": label,
        "ratio": ratio,
        "percent": 0.0 if ratio is None else max(0.0, min(100.0, ratio * 100.0)),
        "value_text": "-" if ratio is None else f"{ratio * 100:.1f}%",
        "detail": detail,
        "tone": tone,
    }


def _build_channel_snapshot(tx_report: Any, rx_report: Any) -> dict[str, Any]:
    tx_run = tx_report.run if tx_report and tx_report.run else None
    rx_run = rx_report.run if rx_report and rx_report.run else None
    run = tx_run or rx_run
    return {
        "active_freq_hz": run.active_freq_hz if run else None,
        "backup_freq_hz": run.backup_freq_hz if run else None,
        "rf_bitrate_bps": run.rf_bitrate_bps if run else None,
        "machine_log_stat_period_ms": run.machine_log_stat_period_ms if run else None,
        "tx_complete_timeout_ms": tx_run.tx_complete_timeout_ms if tx_run else None,
        "airtime_limit_us": tx_run.airtime_limit_us if tx_run else None,
        "rx_thresh_enable": rx_run.rx_thresh_enable if rx_run else None,
        "rx_min_rssi_dbm": rx_run.rx_min_rssi_dbm if rx_run else None,
        "rx_min_lqi": rx_run.rx_min_lqi if rx_run else None,
        "rx_poll_interval_ms": rx_run.rx_poll_interval_ms if rx_run else None,
        "rx_host_bridge_budget_count": rx_run.rx_host_bridge_budget_count if rx_run else None,
    }


def _build_packet_type_breakdown(
    tx_frames: list[Any],
    rx_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for frame in tx_frames:
        key = (frame.stream_id or "unknown", frame.type_id or "unknown")
        entry = grouped.setdefault(
            key,
            {
                "stream_id": key[0],
                "type_id": key[1],
                "label": _packet_label(*key),
                "tx_packets": 0,
                "tx_bytes": 0,
                "rx_seen_packets": 0,
                "rx_accepted_packets": 0,
                "rx_accepted_bytes": 0,
                "accepted_rssi_values": [],
                "accepted_lqi_values": [],
                "drop_reasons": Counter(),
            },
        )
        entry["tx_packets"] += 1
        entry["tx_bytes"] += frame.length

    for point in rx_points:
        key = (point["stream_id"] or "unknown", point["type_id"] or "unknown")
        entry = grouped.setdefault(
            key,
            {
                "stream_id": key[0],
                "type_id": key[1],
                "label": _packet_label(*key),
                "tx_packets": 0,
                "tx_bytes": 0,
                "rx_seen_packets": 0,
                "rx_accepted_packets": 0,
                "rx_accepted_bytes": 0,
                "accepted_rssi_values": [],
                "accepted_lqi_values": [],
                "drop_reasons": Counter(),
            },
        )
        entry["rx_seen_packets"] += 1
        if point["accepted"]:
            entry["rx_accepted_packets"] += 1
            entry["rx_accepted_bytes"] += point["length"]
            if point["rssi_dbm"] is not None:
                entry["accepted_rssi_values"].append(point["rssi_dbm"])
            if point["lqi"] is not None:
                entry["accepted_lqi_values"].append(point["lqi"])
        else:
            entry["drop_reasons"][point["drop_reason"] or "unknown"] += 1

    rows: list[dict[str, Any]] = []
    for entry in grouped.values():
        accepted_rssi = _summarize_numeric_series(entry["accepted_rssi_values"])
        accepted_lqi = _summarize_numeric_series(entry["accepted_lqi_values"])
        dominant_drop_reason = None
        if entry["drop_reasons"]:
            dominant_drop_reason = entry["drop_reasons"].most_common(1)[0][0]
        rows.append(
            {
                "label": entry["label"],
                "stream_id": entry["stream_id"],
                "type_id": entry["type_id"],
                "tx_packets": entry["tx_packets"],
                "rx_seen_packets": entry["rx_seen_packets"],
                "rx_accepted_packets": entry["rx_accepted_packets"],
                "rx_rejected_packets": entry["rx_seen_packets"] - entry["rx_accepted_packets"],
                "delivery_ratio": _safe_ratio(entry["rx_accepted_packets"], entry["tx_packets"]),
                "visibility_ratio": _safe_ratio(entry["rx_seen_packets"], entry["tx_packets"]),
                "acceptance_ratio": _safe_ratio(entry["rx_accepted_packets"], entry["rx_seen_packets"]),
                "accepted_rssi_p50_dbm": accepted_rssi.get("p50"),
                "accepted_lqi_p50": accepted_lqi.get("p50"),
                "dominant_drop_reason": dominant_drop_reason,
            }
        )

    rows.sort(key=lambda item: (-item["tx_packets"], -item["rx_seen_packets"], item["label"]))
    return rows


def _packet_label(stream_id: str, type_id: str) -> str:
    return type_id if stream_id == type_id else f"{stream_id} / {type_id}"


def _build_link_time_bins(
    tx_frames: list[Any],
    rx_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not tx_frames and not rx_points:
        return []

    all_times = [frame.t_ms for frame in tx_frames] + [point["t_ms"] for point in rx_points]
    start_ms = min(all_times)
    end_ms = max(all_times)
    bin_ms = _choose_bin_ms(start_ms, end_ms)

    results: list[dict[str, Any]] = []
    bin_start = start_ms
    while bin_start <= end_ms:
        bin_end = bin_start + bin_ms
        tx_bucket = [frame for frame in tx_frames if bin_start <= frame.t_ms < bin_end]
        rx_bucket = [point for point in rx_points if bin_start <= point["t_ms"] < bin_end]
        if tx_bucket or rx_bucket:
            accepted_bucket = [point for point in rx_bucket if point["accepted"]]
            rssi_values = [point["rssi_dbm"] for point in accepted_bucket if point["rssi_dbm"] is not None]
            lqi_values = [point["lqi"] for point in accepted_bucket if point["lqi"] is not None]
            tx_bytes = sum(frame.length for frame in tx_bucket)
            accepted_bytes = sum(point["length"] for point in accepted_bucket)
            results.append(
                {
                    "start_ms": bin_start,
                    "end_ms": bin_end,
                    "tx_packets": len(tx_bucket),
                    "rx_seen_packets": len(rx_bucket),
                    "rx_accepted_packets": len(accepted_bucket),
                    "rx_rejected_packets": len(rx_bucket) - len(accepted_bucket),
                    "delivery_ratio": _safe_ratio(len(accepted_bucket), len(tx_bucket)),
                    "visibility_ratio": _safe_ratio(len(rx_bucket), len(tx_bucket)),
                    "acceptance_ratio": _safe_ratio(len(accepted_bucket), len(rx_bucket)),
                    "avg_rssi_dbm": (sum(rssi_values) / len(rssi_values)) if rssi_values else None,
                    "avg_lqi": (sum(lqi_values) / len(lqi_values)) if lqi_values else None,
                    "tx_bytes": tx_bytes,
                    "rx_accepted_bytes": accepted_bytes,
                    "offered_bits_per_sec": (tx_bytes * 8_000.0) / bin_ms,
                    "delivered_bits_per_sec": (accepted_bytes * 8_000.0) / bin_ms,
                }
            )
        bin_start = bin_end
    return results


def _select_worst_link_bins(link_time_bins: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    candidates = [item for item in link_time_bins if item["tx_packets"] > 0]
    candidates.sort(
        key=lambda item: (
            item["delivery_ratio"] if item["delivery_ratio"] is not None else 2.0,
            -item["tx_packets"],
            item["start_ms"],
        )
    )
    return candidates[:limit]


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
                "acceptance_ratio": pdr,
                "rejection_ratio": 1.0 - pdr,
                "avg_lqi": (sum(lqi_values) / len(lqi_values)) if lqi_values else None,
            }
        )
    return bins


def _build_rolling_reliability(link_time_bins: list[dict[str, Any]], *, window_size: int) -> list[dict[str, Any]]:
    bins_with_tx = [item for item in link_time_bins if item["tx_packets"] > 0]
    if len(bins_with_tx) < window_size:
        return []
    results: list[dict[str, Any]] = []
    for index in range(window_size - 1, len(bins_with_tx)):
        window = bins_with_tx[index - window_size + 1:index + 1]
        tx_packets = sum(item["tx_packets"] for item in window)
        accepted_packets = sum(item["rx_accepted_packets"] for item in window)
        delivery_ratio = _safe_ratio(accepted_packets, tx_packets)
        results.append(
            {
                "t_ms": window[-1]["end_ms"],
                "window_bins": window_size,
                "tx_packets": tx_packets,
                "rx_accepted_packets": accepted_packets,
                "delivery_ratio": delivery_ratio,
                "loss_ratio": None if delivery_ratio is None else 1.0 - delivery_ratio,
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


def _build_throughput_by_time(link_time_bins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    throughput: list[dict[str, Any]] = []
    for item in link_time_bins:
        throughput.append(
            {
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
                "tx_packets": item["tx_packets"],
                "tx_bytes": item["tx_bytes"],
                "offered_bits_per_sec": item["offered_bits_per_sec"],
                "rx_accepted_packets": item["rx_accepted_packets"],
                "rx_accepted_bytes": item["rx_accepted_bytes"],
                "delivered_bits_per_sec": item["delivered_bits_per_sec"],
            }
        )
    return throughput


def _build_drop_reason_breakdown(rejected_points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(point["drop_reason"] or "unknown" for point in rejected_points)
    total = sum(counts.values())
    return [
        {
            "drop_reason": reason,
            "count": count,
            "share": _safe_ratio(count, total),
        }
        for reason, count in counts.most_common()
    ]


def _build_signal_summary(
    *,
    accepted_points: list[dict[str, Any]],
    rejected_points: list[dict[str, Any]],
    rx_min_rssi_dbm: int | None,
    rx_min_lqi: int | None,
) -> dict[str, Any]:
    accepted_rssi = _summarize_numeric_series([point["rssi_dbm"] for point in accepted_points if point["rssi_dbm"] is not None])
    accepted_lqi = _summarize_numeric_series([point["lqi"] for point in accepted_points if point["lqi"] is not None])
    rejected_rssi = _summarize_numeric_series([point["rssi_dbm"] for point in rejected_points if point["rssi_dbm"] is not None])
    rejected_lqi = _summarize_numeric_series([point["lqi"] for point in rejected_points if point["lqi"] is not None])
    return {
        "accepted_rssi": accepted_rssi,
        "accepted_lqi": accepted_lqi,
        "rejected_rssi": rejected_rssi,
        "rejected_lqi": rejected_lqi,
        "accepted_rssi_margin_p10_db": (
            accepted_rssi.get("p10") - rx_min_rssi_dbm
            if accepted_rssi.get("p10") is not None and rx_min_rssi_dbm is not None
            else None
        ),
        "accepted_lqi_margin_p10": (
            accepted_lqi.get("p10") - rx_min_lqi
            if accepted_lqi.get("p10") is not None and rx_min_lqi is not None
            else None
        ),
    }


def _build_tx_timing_summary(tx_frames: list[Any], tx_report: Any) -> dict[str, Any]:
    complete_latency = _summarize_numeric_series(
        [frame.complete_latency_ms for frame in tx_frames if frame.complete_latency_ms is not None]
    )
    schedule_lag = _summarize_numeric_series(
        [frame.schedule_lag_ms for frame in tx_frames if frame.schedule_lag_ms is not None]
    )
    final_stat = tx_report.final_stat if tx_report else None
    return {
        "complete_latency_ms": complete_latency,
        "schedule_lag_ms": schedule_lag,
        "max_complete_latency_ms": (
            final_stat.max_complete_latency_ms if final_stat and final_stat.max_complete_latency_ms is not None else complete_latency.get("max")
        ),
        "max_schedule_lag_ms": (
            final_stat.max_schedule_lag_ms if final_stat and final_stat.max_schedule_lag_ms is not None else schedule_lag.get("max")
        ),
    }


def _build_tx_health_summary(tx_report: Any) -> dict[str, Any]:
    final_stat = tx_report.final_stat if tx_report else None
    if final_stat is None:
        return {}
    return {
        "attempt_count": final_stat.attempt_count,
        "queued_count": final_stat.queued_count,
        "completed_count": final_stat.completed_count,
        "busy_count": final_stat.busy_count,
        "airtime_reject_count": final_stat.airtime_reject_count,
        "send_fail_count": final_stat.send_fail_count,
        "timeout_count": final_stat.timeout_count,
        "airtime_used_us": final_stat.airtime_used_us,
        "airtime_limit_us": final_stat.airtime_limit_us,
        "airtime_utilization_ratio": _safe_ratio(final_stat.airtime_used_us, final_stat.airtime_limit_us),
    }


def _build_rx_health_summary(rx_report: Any) -> dict[str, Any]:
    final_stat = rx_report.final_stat if rx_report else None
    if final_stat is None:
        return {}
    rx_visible_total = (final_stat.rx_ok_count or 0) + (final_stat.rx_crc_fail_count or 0)
    return {
        "rx_ok_count": final_stat.rx_ok_count,
        "accepted_count": final_stat.accepted_count,
        "rejected_count": final_stat.rejected_count,
        "rx_crc_fail_count": final_stat.rx_crc_fail_count,
        "rx_partial_count": final_stat.rx_partial_count,
        "rx_overflow_count": final_stat.rx_overflow_count,
        "filtered_total_count": final_stat.filtered_total_count,
        "filtered_rssi_only_count": final_stat.filtered_rssi_only_count,
        "filtered_lqi_only_count": final_stat.filtered_lqi_only_count,
        "filtered_both_count": final_stat.filtered_both_count,
        "poll_recovery_count": final_stat.poll_recovery_count,
        "spi_backpressure_count": final_stat.spi_backpressure_count,
        "rx_fifo_overwrite_count": final_stat.rx_fifo_overwrite_count,
        "rx_fifo_depth_count": final_stat.rx_fifo_depth_count,
        "spi_queue_depth_count": final_stat.spi_queue_depth_count,
        "rx_fifo_hwm": final_stat.rx_fifo_hwm,
        "crc_clean_ratio": _safe_ratio(final_stat.rx_ok_count, rx_visible_total),
    }


def _build_channel_events(merge: MergeReport) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for role_key in ["TX", "RX"]:
        role_report = merge.roles.get(role_key)
        if role_report is None:
            continue
        for frame in role_report.event_frames:
            if frame.event_id not in {"channel_state", "rx_mode"}:
                continue
            events.append(
                {
                    "role": role_key,
                    "t_ms": frame.t_ms,
                    "event_id": frame.event_id,
                    "state": frame.state,
                    "reason": frame.reason,
                    "active_freq_hz": frame.active_freq_hz,
                    "backup_freq_hz": frame.backup_freq_hz,
                }
            )
    events.sort(key=lambda item: (item["t_ms"], item["role"], item["event_id"]))
    return events


def _summarize_numeric_series(values: list[int | float]) -> dict[str, Any]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": _percentile(ordered, 0.10),
        "p50": _percentile(ordered, 0.50),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
        "avg": sum(ordered) / len(ordered),
    }


def _percentile(values: list[int | float], fraction: float) -> int | float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, int(math.floor((len(values) - 1) * fraction))))
    return values[index]


def _choose_bin_ms(start_ms: int, end_ms: int, *, target_bins: int = 20) -> int:
    span_ms = max(1, end_ms - start_ms)
    raw_bin_ms = span_ms / target_bins
    return int(min(10_000, max(500, math.ceil(raw_bin_ms / 100.0) * 100)))


def _safe_ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)
