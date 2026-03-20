from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from server.app.models.entities import Artifact, RawArtifact, Session as SessionModel, SessionRoleRun
from server.app.services.sessions import annotations as list_annotations
from server.app.services.sessions import raw_artifacts as list_raw_artifacts
from shared.enums import RawArtifactType, Role, TimestampKind
from shared.enums import ReportStatus
from shared.schemas import MergeReport, ParsedEvent
from shared.time_sync import TimeCorrection

ISO_RE = re.compile(r"(?P<iso>\d{4}-\d{2}-\d{2}[T ][0-9:\.\+\-Z]+)")
KV_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_\-]*)=(?P<value>[^\s]+)")
REL_RE = re.compile(r"^\[(?P<seconds>\d+(?:\.\d+)?)\]")
SEQ_KEYS = {"seq", "packet_seq", "pkt_seq", "sequence"}
EVENT_KEYS = {"event", "type", "tag", "class"}
RSSI_KEYS = {"rssi", "avg_rssi"}
SNR_KEYS = {"snr", "avg_snr"}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True)
class ParsedTimestamp:
    kind: TimestampKind
    host_timestamp: datetime | None = None
    relative_seconds: float | None = None


def _parse_value(raw: str) -> Any:
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw)
    try:
        return float(raw)
    except ValueError:
        return raw


def _parse_timestamp(line: str) -> ParsedTimestamp:
    iso_match = ISO_RE.search(line)
    if iso_match:
        value = iso_match.group("iso").replace("Z", "+00:00")
        return ParsedTimestamp(
            TimestampKind.ABSOLUTE,
            host_timestamp=_as_utc(datetime.fromisoformat(value)),
        )
    rel_match = REL_RE.search(line.strip())
    if rel_match:
        return ParsedTimestamp(TimestampKind.RELATIVE, relative_seconds=float(rel_match.group("seconds")))
    if "ts=" in line:
        kv = dict(KV_RE.findall(line))
        raw = kv.get("ts") or kv.get("timestamp")
        if raw:
            if raw.endswith("Z"):
                return ParsedTimestamp(
                    TimestampKind.ABSOLUTE,
                    host_timestamp=datetime.fromisoformat(raw.replace("Z", "+00:00")),
                )
            try:
                epoch = float(raw)
                return ParsedTimestamp(
                    TimestampKind.ABSOLUTE,
                    host_timestamp=_as_utc(datetime.fromtimestamp(epoch, tz=timezone.utc)),
                )
            except ValueError:
                pass
    return ParsedTimestamp(TimestampKind.NONE)


def _event_name(fields: dict[str, Any], line: str) -> str:
    for key in EVENT_KEYS:
        if key in fields:
            return str(fields[key])
    tokens = line.split()
    for token in tokens:
        upper = token.strip(":[]")
        if upper in {"INFO", "WARN", "ERROR", "DEBUG"}:
            continue
        if upper.isupper() and len(upper) > 2:
            return upper.lower()
    return "log_line"


def _level(line: str) -> str | None:
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        if level in line:
            return level.lower()
    return None


def _packet_sequence(fields: dict[str, Any]) -> int | None:
    for key in SEQ_KEYS:
        value = fields.get(key)
        if isinstance(value, int):
            return value
    return None


def _median_offset_ms(role_run: SessionRoleRun) -> TimeCorrection:
    samples = (role_run.diagnostics_json or {}).get("time_sync_samples") or []
    if not samples:
        return TimeCorrection()
    offsets = [float(item["estimated_offset_ms"]) for item in samples if "estimated_offset_ms" in item]
    if not offsets:
        return TimeCorrection()
    return TimeCorrection(
        offset_ms=statistics.median(offsets),
        sample_count=len(offsets),
        source="agent_time_samples",
        diagnostics={"median_offset_ms": statistics.median(offsets)},
    )


def _correct_timestamp(
    parsed_ts: ParsedTimestamp,
    *,
    role_run: SessionRoleRun,
    line_number: int,
    correction: TimeCorrection,
) -> datetime | None:
    if parsed_ts.kind == TimestampKind.ABSOLUTE and parsed_ts.host_timestamp is not None:
        return parsed_ts.host_timestamp + timedelta(milliseconds=correction.offset_ms)
    if parsed_ts.kind == TimestampKind.RELATIVE and parsed_ts.relative_seconds is not None:
        base = _as_utc(role_run.capture_started_at or role_run.flash_finished_at)
        if base is not None:
            return base + timedelta(seconds=parsed_ts.relative_seconds)
    base = _as_utc(role_run.capture_started_at or role_run.flash_finished_at)
    if base is not None:
        return base + timedelta(milliseconds=line_number)
    return None


def parse_log_text(role_run: SessionRoleRun, role: Role, text: str) -> tuple[list[ParsedEvent], list[str], TimeCorrection]:
    events: list[ParsedEvent] = []
    parse_errors: list[str] = []
    correction = _median_offset_ms(role_run)
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        parsed_ts = _parse_timestamp(raw_line)
        fields = {key: _parse_value(value) for key, value in KV_RE.findall(raw_line)}
        event_name = _event_name(fields, raw_line)
        corrected = _correct_timestamp(
            parsed_ts,
            role_run=role_run,
            line_number=line_number,
            correction=correction,
        )
        parse_error = None
        if not raw_line.strip():
            parse_error = "blank_line"
        event = ParsedEvent(
            role=role,
            raw_line=raw_line,
            line_number=line_number,
            event_name=event_name,
            level=_level(raw_line),
            timestamp_kind=parsed_ts.kind,
            host_timestamp=parsed_ts.host_timestamp,
            relative_seconds=parsed_ts.relative_seconds,
            corrected_timestamp=corrected,
            packet_sequence=_packet_sequence(fields),
            fields=fields,
            parse_error=parse_error,
        )
        if parse_error:
            parse_errors.append(f"{role.value}:{line_number}:{parse_error}")
        events.append(event)
    return events, parse_errors, correction


def merge_session_logs(
    db: Session,
    *,
    session: SessionModel,
    role_runs: list[SessionRoleRun],
    raw_items: list[RawArtifact],
    storage_root: Path,
) -> MergeReport:
    role_run_map = {Role(item.role): item for item in role_runs}
    merged_events: list[ParsedEvent] = []
    parse_errors: list[str] = []
    correction_diagnostics: dict[str, Any] = {}
    for role in (Role.TX, Role.RX):
        role_raw = [
            item
            for item in raw_items
            if item.type == RawArtifactType.RTT_LOG.value and item.role == role.value
        ]
        if not role_raw:
            continue
        latest_log = role_raw[-1]
        text = (storage_root / latest_log.storage_path).read_text(encoding="utf-8")
        events, role_errors, correction = parse_log_text(role_run_map[role], role, text)
        merged_events.extend(events)
        parse_errors.extend(role_errors)
        correction_diagnostics[role.value] = correction.model_dump()
    for annotation in list_annotations(db, session.id):
        merged_events.append(
            ParsedEvent(
                role=None,
                raw_line=annotation.text,
                line_number=0,
                event_name="annotation",
                corrected_timestamp=_as_utc(annotation.created_at),
                fields={"text": annotation.text},
            )
        )
    merged_events.sort(
        key=lambda item: (
            _as_utc(item.corrected_timestamp) or datetime.min.replace(tzinfo=timezone.utc),
            item.role.value if item.role else "ZZ",
            item.line_number,
        )
    )
    metrics, anomalies = _compute_metrics(merged_events)
    return MergeReport(
        metrics=metrics,
        anomalies=anomalies,
        merged_events=merged_events,
        parse_errors=parse_errors,
        correction_diagnostics=correction_diagnostics,
        status=ReportStatus.READY,
    )


def _values(events: list[ParsedEvent], keys: set[str]) -> list[float]:
    output: list[float] = []
    for event in events:
        for key in keys:
            value = event.fields.get(key)
            if isinstance(value, (int, float)) and not math.isnan(float(value)):
                output.append(float(value))
    return output


def _compute_metrics(events: list[ParsedEvent]) -> tuple[dict[str, Any], list[str]]:
    tx_packets = {event.packet_sequence: event for event in events if event.role == Role.TX and event.packet_sequence is not None}
    rx_packets = {event.packet_sequence: event for event in events if event.role == Role.RX and event.packet_sequence is not None}
    shared_sequences = sorted(set(tx_packets) & set(rx_packets))
    latencies_ms: list[float] = []
    for seq in shared_sequences:
        tx_event = tx_packets[seq]
        rx_event = rx_packets[seq]
        if tx_event.corrected_timestamp and rx_event.corrected_timestamp:
            delta = rx_event.corrected_timestamp - tx_event.corrected_timestamp
            latencies_ms.append(delta.total_seconds() * 1000.0)
    metrics: dict[str, Any] = {
        "event_count": len(events),
        "packet_tx_count": len(tx_packets),
        "packet_rx_count": len(rx_packets),
        "packet_correlated_count": len(shared_sequences),
        "packet_delivery_ratio": round(len(shared_sequences) / len(tx_packets), 4) if tx_packets else None,
        "annotation_count": sum(1 for item in events if item.event_name == "annotation"),
        "parse_error_count": sum(1 for item in events if item.parse_error),
    }
    if latencies_ms:
        metrics["packet_latency_ms_median"] = round(statistics.median(latencies_ms), 3)
        metrics["packet_latency_ms_max"] = round(max(latencies_ms), 3)
    rssi_values = _values(events, RSSI_KEYS)
    snr_values = _values(events, SNR_KEYS)
    if rssi_values:
        metrics["rssi_avg"] = round(statistics.mean(rssi_values), 3)
        metrics["rssi_min"] = round(min(rssi_values), 3)
        metrics["rssi_max"] = round(max(rssi_values), 3)
    if snr_values:
        metrics["snr_avg"] = round(statistics.mean(snr_values), 3)
        metrics["snr_min"] = round(min(snr_values), 3)
        metrics["snr_max"] = round(max(snr_values), 3)
    anomalies: list[str] = []
    if tx_packets and len(shared_sequences) < len(tx_packets):
        anomalies.append(
            f"Only {len(shared_sequences)} of {len(tx_packets)} TX packet sequences were observed on RX."
        )
    if any(latency < 0 for latency in latencies_ms):
        anomalies.append("Negative TX->RX latency detected after time correction; inspect time-sync samples.")
    return metrics, anomalies


def emit_parser_output(report: MergeReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)
