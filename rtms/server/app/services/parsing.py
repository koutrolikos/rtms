from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from rtms.server.app.models.entities import Artifact, RawArtifact, RunSession, SessionRoleRun
from rtms.server.app.services.sessions import annotations as list_annotations
from rtms.server.app.services.sessions import session_events as list_session_events
from rtms.shared.enums import EventSourceType, EventType, RawArtifactType, ReportStatus, Role
from rtms.shared.mlog import (
    MLOG_BUILD_LABELS,
    MLOG_DETAIL_LABELS,
    MLOG_DROP_REASON_LABELS,
    MLOG_EVENT_LABELS,
    MLOG_HEADER_SIZE,
    MLOG_KIND_EVT,
    MLOG_KIND_LABELS,
    MLOG_KIND_PKT,
    MLOG_KIND_RUN,
    MLOG_KIND_STAT,
    MLOG_MAGIC,
    MLOG_PACKET_ID_LABELS,
    MLOG_PROTOCOL_VERSION,
    MLOG_REASON_LABELS,
    MLOG_ROLE_FROM_CODE,
    MLOG_ROLE_CODES,
    MLOG_STATE_LABELS,
)
from rtms.shared.schemas import (
    MergeReport,
    MachineDecodeDiagnostic,
    MachineEventFrame,
    MachinePacketFrame,
    MachineRunFrame,
    MachineStatFrame,
    ReportAnnotation,
    RoleMachineReport,
    SessionEventRecord,
)


class DecodeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def emit_parser_output(report: MergeReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)


def merge_session_logs(
    db: Session,
    *,
    session: RunSession,
    role_runs: list[SessionRoleRun],
    raw_items: list[RawArtifact],
    storage_root: Path,
) -> MergeReport:
    roles = {
        Role.TX.value: RoleMachineReport(role=Role.TX),
        Role.RX.value: RoleMachineReport(role=Role.RX),
    }
    decode_diagnostics: list[MachineDecodeDiagnostic] = []
    human_log_policy = _resolve_human_log_policy(db, role_runs)

    for role in (Role.TX, Role.RX):
        role_report = roles[role.value]
        artifact = _latest_machine_artifact(raw_items, role)
        if artifact is None:
            decode_diagnostics.append(
                _diag(
                    role=role,
                    code="machine_artifact_missing",
                    message=f"No {role.value} machine RTT artifact was uploaded.",
                )
            )
            continue

        role_report.machine_artifact_id = artifact.id
        role_report.machine_artifact_path = artifact.storage_path
        role_report.machine_artifact_size_bytes = artifact.size_bytes

        path = storage_root / artifact.storage_path
        if not path.is_file():
            decode_diagnostics.append(
                _diag(
                    role=role,
                    artifact=artifact,
                    code="artifact_file_missing",
                    message=f"Machine RTT artifact is missing on disk: {artifact.storage_path}",
                )
            )
            continue

        _decode_machine_artifact(role_report, artifact, path.read_bytes(), decode_diagnostics)
        _finalize_role_report(role_report)
        _apply_human_log_policy_override(
            role_report=role_report,
            role=role,
            artifact=artifact,
            policy_enabled=human_log_policy.get(role.value),
            decode_diagnostics=decode_diagnostics,
        )

    if not any(_role_has_telemetry(item) for item in roles.values()):
        decode_diagnostics.append(
            _diag(
                code="machine_report_unavailable",
                message="No decodable machine telemetry was available for this session.",
            )
        )
        status = ReportStatus.FAILED
    else:
        status = ReportStatus.READY

    annotations = [
        ReportAnnotation(created_at=item.created_at, text=item.text, author=item.author)
        for item in list_annotations(db, session.id)
    ]
    session_events = [
        SessionEventRecord(
            source_type=EventSourceType(item.source_type),
            source_ref=item.source_ref,
            event_type=EventType(item.event_type),
            local_timestamp=item.local_timestamp,
            corrected_timestamp=item.corrected_timestamp,
            payload=item.payload or {},
        )
        for item in list_session_events(db, session.id)
    ]

    return MergeReport(
        roles=roles,
        decode_diagnostics=decode_diagnostics,
        annotations=annotations,
        session_events=session_events,
        status=status,
    )


def flatten_machine_timeline(report: MergeReport) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    kind_order = {"run": 0, "evt": 1, "stat": 2, "pkt": 3}
    for role_key in (Role.TX.value, Role.RX.value):
        role_report = report.roles.get(role_key)
        if role_report is None:
            continue
        frames: list[Any] = []
        if role_report.run is not None:
            frames.append(role_report.run)
        frames.extend(role_report.event_frames)
        frames.extend(role_report.stat_frames)
        frames.extend(role_report.packet_frames)
        frames.sort(key=lambda item: (item.t_ms, kind_order.get(item.kind, 99), item.offset))
        entries.extend(frame.model_dump(mode="json") for frame in frames)
    return entries


def _latest_machine_artifact(raw_items: list[RawArtifact], role: Role) -> RawArtifact | None:
    matches = [
        item
        for item in raw_items
        if item.type == RawArtifactType.RTT_MACHINE_LOG.value and item.role == role.value
    ]
    if not matches:
        return None
    return matches[-1]


def _decode_machine_artifact(
    role_report: RoleMachineReport,
    artifact: RawArtifact,
    data: bytes,
    decode_diagnostics: list[MachineDecodeDiagnostic],
) -> None:
    if data and MLOG_MAGIC not in data:
        decode_diagnostics.append(
            _diag(
                role=role_report.role,
                artifact=artifact,
                code="sync_not_found",
                message="No MLOG frame sync magic was found in the machine artifact.",
            )
        )
        return
    cursor = 0
    while True:
        offset = data.find(MLOG_MAGIC, cursor)
        if offset < 0:
            return
        if (len(data) - offset) < MLOG_HEADER_SIZE:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="truncated_header",
                    message="Truncated MLOG frame header.",
                )
            )
            return

        version = data[offset + 4]
        kind_code = data[offset + 5]
        role_code = data[offset + 6]
        flags = data[offset + 7]
        payload_len = struct.unpack_from("<H", data, offset + 8)[0]
        reserved = struct.unpack_from("<H", data, offset + 10)[0]
        t_ms = struct.unpack_from("<I", data, offset + 12)[0]
        frame_end = offset + MLOG_HEADER_SIZE + payload_len
        if frame_end > len(data):
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="truncated_frame",
                    message=f"Truncated MLOG frame with payload_len={payload_len}.",
                )
            )
            return

        payload = data[offset + MLOG_HEADER_SIZE:frame_end]
        cursor = frame_end

        if version != MLOG_PROTOCOL_VERSION:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="unsupported_version",
                    message=f"Unsupported MLOG version {version}.",
                )
            )
            continue
        if flags != 0:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="unexpected_flags",
                    message=f"Unexpected non-zero flags field {flags}.",
                )
            )
            continue
        if reserved != 0:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="reserved_mismatch",
                    message=f"Unexpected non-zero reserved field {reserved}.",
                )
            )
            continue

        decoded_role = MLOG_ROLE_FROM_CODE.get(role_code)
        if decoded_role is None:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="unknown_role",
                    message=f"Unknown MLOG role code {role_code}.",
                )
            )
            continue
        if decoded_role != role_report.role:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="artifact_role_mismatch",
                    message=(
                        f"Artifact role {role_report.role.value} does not match frame role "
                        f"{decoded_role.value}."
                    ),
                )
            )
            continue

        if kind_code == MLOG_KIND_RUN:
            decoder = _decode_run_frame
        elif kind_code == MLOG_KIND_STAT:
            decoder = _decode_stat_frame
        elif kind_code == MLOG_KIND_PKT:
            decoder = _decode_packet_frame
        elif kind_code == MLOG_KIND_EVT:
            decoder = _decode_event_frame
        else:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code="unknown_kind",
                    message=f"Unknown MLOG kind code {kind_code}.",
                )
            )
            continue

        try:
            frame = decoder(
                role=decoded_role,
                role_code=role_code,
                version=version,
                flags=flags,
                payload_len=payload_len,
                offset=offset,
                t_ms=t_ms,
                payload=payload,
            )
        except DecodeError as exc:
            decode_diagnostics.append(
                _diag(
                    role=role_report.role,
                    artifact=artifact,
                    offset=offset,
                    code=exc.code,
                    message=exc.message,
                )
            )
            continue

        if isinstance(frame, MachineRunFrame):
            if role_report.run is None or (frame.t_ms, frame.offset) >= (role_report.run.t_ms, role_report.run.offset):
                role_report.run = frame
        elif isinstance(frame, MachineStatFrame):
            role_report.stat_frames.append(frame)
        elif isinstance(frame, MachinePacketFrame):
            role_report.packet_frames.append(frame)
        elif isinstance(frame, MachineEventFrame):
            role_report.event_frames.append(frame)


def _decode_run_frame(
    *,
    role: Role,
    role_code: int,
    version: int,
    flags: int,
    payload_len: int,
    offset: int,
    t_ms: int,
    payload: bytes,
) -> MachineRunFrame:
    if role == Role.TX:
        _expect_payload_len(payload, 40, role, "run")
        (
            machine_detail_code,
            build_code,
            channel_state_code,
            human_log_level,
            human_log_enable,
            machine_log_enable,
            active_slot,
            backup_slot,
            active_freq_hz,
            backup_freq_hz,
            rf_bitrate_bps,
            machine_log_stat_period_ms,
            airtime_limit_us,
            telem_gps_period_ms,
            telem_imu_baro_period_ms,
            tx_complete_timeout_ms,
        ) = struct.unpack("<8B8I", payload)
        return MachineRunFrame(
            role=role,
            role_code=role_code,
            kind="run",
            kind_code=MLOG_KIND_RUN,
            t_ms=t_ms,
            version=version,
            flags=flags,
            payload_len=payload_len,
            offset=offset,
            machine_detail=_enum_label(MLOG_DETAIL_LABELS, machine_detail_code),
            machine_detail_code=machine_detail_code,
            build=_enum_label(MLOG_BUILD_LABELS, build_code),
            build_code=build_code,
            channel_state=_enum_label(MLOG_STATE_LABELS, channel_state_code),
            channel_state_code=channel_state_code,
            human_log_level=human_log_level,
            human_log_enable=bool(human_log_enable),
            machine_log_enable=bool(machine_log_enable),
            active_slot=active_slot,
            active_freq_hz=active_freq_hz,
            backup_slot=backup_slot,
            backup_freq_hz=backup_freq_hz,
            rf_bitrate_bps=rf_bitrate_bps,
            machine_log_stat_period_ms=machine_log_stat_period_ms,
            airtime_limit_us=airtime_limit_us,
            telem_gps_period_ms=telem_gps_period_ms,
            telem_imu_baro_period_ms=telem_imu_baro_period_ms,
            tx_complete_timeout_ms=tx_complete_timeout_ms,
        )

    _expect_payload_len(payload, 44, role, "run")
    (
        machine_detail_code,
        build_code,
        channel_state_code,
        human_log_level,
        human_log_enable,
        machine_log_enable,
        active_slot,
        backup_slot,
        active_freq_hz,
        backup_freq_hz,
        rf_bitrate_bps,
        machine_log_stat_period_ms,
        rx_thresh_enable,
        rx_min_rssi_dbm,
        rx_min_lqi,
        rx_poll_interval_ms,
        rx_host_bridge_budget_count,
    ) = struct.unpack("<8B4IB3xiIII", payload)
    return MachineRunFrame(
        role=role,
        role_code=role_code,
        kind="run",
        kind_code=MLOG_KIND_RUN,
        t_ms=t_ms,
        version=version,
        flags=flags,
        payload_len=payload_len,
        offset=offset,
        machine_detail=_enum_label(MLOG_DETAIL_LABELS, machine_detail_code),
        machine_detail_code=machine_detail_code,
        build=_enum_label(MLOG_BUILD_LABELS, build_code),
        build_code=build_code,
        channel_state=_enum_label(MLOG_STATE_LABELS, channel_state_code),
        channel_state_code=channel_state_code,
        human_log_level=human_log_level,
        human_log_enable=bool(human_log_enable),
        machine_log_enable=bool(machine_log_enable),
        active_slot=active_slot,
        active_freq_hz=active_freq_hz,
        backup_slot=backup_slot,
        backup_freq_hz=backup_freq_hz,
        rf_bitrate_bps=rf_bitrate_bps,
        machine_log_stat_period_ms=machine_log_stat_period_ms,
        rx_thresh_enable=bool(rx_thresh_enable),
        rx_min_rssi_dbm=rx_min_rssi_dbm,
        rx_min_lqi=rx_min_lqi,
        rx_poll_interval_ms=rx_poll_interval_ms,
        rx_host_bridge_budget_count=rx_host_bridge_budget_count,
    )


def _decode_stat_frame(
    *,
    role: Role,
    role_code: int,
    version: int,
    flags: int,
    payload_len: int,
    offset: int,
    t_ms: int,
    payload: bytes,
) -> MachineStatFrame:
    base = {
        "role": role,
        "role_code": role_code,
        "kind": "stat",
        "kind_code": MLOG_KIND_STAT,
        "t_ms": t_ms,
        "version": version,
        "flags": flags,
        "payload_len": payload_len,
        "offset": offset,
    }
    if role == Role.TX:
        _expect_payload_len(payload, 72, role, "stat")
        values = struct.unpack("<18I", payload)
        return MachineStatFrame(
            **base,
            attempt_count=values[0],
            queued_count=values[1],
            completed_count=values[2],
            gps_queued_count=values[3],
            gps_completed_count=values[4],
            imu_baro_queued_count=values[5],
            imu_baro_completed_count=values[6],
            other_queued_count=values[7],
            other_completed_count=values[8],
            busy_count=values[9],
            airtime_reject_count=values[10],
            send_fail_count=values[11],
            timeout_count=values[12],
            max_complete_latency_ms=values[13],
            last_complete_latency_ms=values[14],
            max_schedule_lag_ms=values[15],
            airtime_used_us=values[16],
            airtime_limit_us=values[17],
        )

    _expect_payload_len(payload, 64, role, "stat")
    values = struct.unpack("<16I", payload)
    return MachineStatFrame(
        **base,
        rx_ok_count=values[0],
        accepted_count=values[1],
        rejected_count=values[2],
        rx_crc_fail_count=values[3],
        rx_partial_count=values[4],
        rx_overflow_count=values[5],
        filtered_total_count=values[6],
        filtered_rssi_only_count=values[7],
        filtered_lqi_only_count=values[8],
        filtered_both_count=values[9],
        poll_recovery_count=values[10],
        spi_backpressure_count=values[11],
        rx_fifo_overwrite_count=values[12],
        rx_fifo_depth_count=values[13],
        spi_queue_depth_count=values[14],
        rx_fifo_hwm=values[15],
    )


def _decode_packet_frame(
    *,
    role: Role,
    role_code: int,
    version: int,
    flags: int,
    payload_len: int,
    offset: int,
    t_ms: int,
    payload: bytes,
) -> MachinePacketFrame:
    base = {
        "role": role,
        "role_code": role_code,
        "kind": "pkt",
        "kind_code": MLOG_KIND_PKT,
        "t_ms": t_ms,
        "version": version,
        "flags": flags,
        "payload_len": payload_len,
        "offset": offset,
    }
    if role == Role.TX:
        _expect_payload_len(payload, 12, role, "pkt")
        stream_id_code, type_id_code, seq, length, complete_latency_ms, schedule_lag_ms = struct.unpack(
            "<4B2I", payload
        )
        return MachinePacketFrame(
            **base,
            stream_id=_enum_label(MLOG_PACKET_ID_LABELS, stream_id_code),
            stream_id_code=stream_id_code,
            type_id=_enum_label(MLOG_PACKET_ID_LABELS, type_id_code),
            type_id_code=type_id_code,
            seq=seq,
            length=length,
            complete_latency_ms=complete_latency_ms,
            schedule_lag_ms=schedule_lag_ms,
        )

    _expect_payload_len(payload, 9, role, "pkt")
    stream_id_code, type_id_code, seq, length, accepted, drop_reason_code, rssi_dbm, lqi, crc = struct.unpack(
        "<6BbBB", payload
    )
    return MachinePacketFrame(
        **base,
        stream_id=_enum_label(MLOG_PACKET_ID_LABELS, stream_id_code),
        stream_id_code=stream_id_code,
        type_id=_enum_label(MLOG_PACKET_ID_LABELS, type_id_code),
        type_id_code=type_id_code,
        seq=seq,
        length=length,
        accepted=bool(accepted),
        drop_reason=_enum_label(MLOG_DROP_REASON_LABELS, drop_reason_code),
        drop_reason_code=drop_reason_code,
        rssi_dbm=rssi_dbm,
        lqi=lqi,
        crc=bool(crc),
    )


def _decode_event_frame(
    *,
    role: Role,
    role_code: int,
    version: int,
    flags: int,
    payload_len: int,
    offset: int,
    t_ms: int,
    payload: bytes,
) -> MachineEventFrame:
    if len(payload) < 4:
        raise DecodeError(
            "payload_size_mismatch",
            f"Expected at least 4 payload bytes for {role.value} evt, got {len(payload)}.",
        )
    event_id_code, state_code, reason_code, _padding = struct.unpack("<4B", payload[:4])
    event_id = MLOG_EVENT_LABELS.get(event_id_code)
    if event_id is None:
        raise DecodeError("unknown_event_id", f"Unknown MLOG event id {event_id_code}.")

    base = {
        "role": role,
        "role_code": role_code,
        "kind": "evt",
        "kind_code": MLOG_KIND_EVT,
        "t_ms": t_ms,
        "version": version,
        "flags": flags,
        "payload_len": payload_len,
        "offset": offset,
        "event_id": event_id,
        "event_id_code": event_id_code,
        "state": _enum_label(MLOG_STATE_LABELS, state_code),
        "state_code": state_code,
        "reason": _enum_label(MLOG_REASON_LABELS, reason_code),
        "reason_code": reason_code,
    }
    if event_id_code in {1, 2}:
        _expect_payload_len(payload, 16, role, "evt")
        active_slot, backup_slot, _reserved, active_freq_hz, backup_freq_hz = struct.unpack(
            "<BBHII", payload[4:]
        )
        return MachineEventFrame(
            **base,
            active_slot=active_slot,
            active_freq_hz=active_freq_hz,
            backup_slot=backup_slot,
            backup_freq_hz=backup_freq_hz,
        )
    if event_id_code == 3:
        _expect_payload_len(payload, 12, role, "evt")
        stream_id_code, type_id_code, seq, length, elapsed_ms = struct.unpack("<4BI", payload[4:])
        return MachineEventFrame(
            **base,
            stream_id=_enum_label(MLOG_PACKET_ID_LABELS, stream_id_code),
            stream_id_code=stream_id_code,
            type_id=_enum_label(MLOG_PACKET_ID_LABELS, type_id_code),
            type_id_code=type_id_code,
            seq=seq,
            length=length,
            elapsed_ms=elapsed_ms,
        )

    _expect_payload_len(payload, 4, role, "evt")
    return MachineEventFrame(**base)


def _expect_payload_len(payload: bytes, expected: int, role: Role, kind: str) -> None:
    if len(payload) != expected:
        raise DecodeError(
            "payload_size_mismatch",
            f"Expected {expected} payload bytes for {role.value} {kind}, got {len(payload)}.",
        )


def _finalize_role_report(role_report: RoleMachineReport) -> None:
    role_report.stat_frames.sort(key=lambda item: (item.t_ms, item.offset))
    role_report.packet_frames.sort(key=lambda item: (item.t_ms, item.offset))
    role_report.event_frames.sort(key=lambda item: (item.t_ms, item.offset))
    if role_report.stat_frames:
        role_report.final_stat = role_report.stat_frames[-1]


def _resolve_human_log_policy(db: Session, role_runs: list[SessionRoleRun]) -> dict[str, bool | None]:
    policy_by_role: dict[str, bool | None] = {}
    for role_run in role_runs:
        role_value = role_run.role
        if role_value in policy_by_role:
            continue
        if not role_run.artifact_id:
            policy_by_role[role_value] = None
            continue
        artifact = db.get(Artifact, role_run.artifact_id)
        if artifact is None:
            policy_by_role[role_value] = None
            continue
        policy_by_role[role_value] = _human_log_policy_from_artifact_metadata(
            artifact.metadata_payload or {}
        )
    return policy_by_role


def _human_log_policy_from_artifact_metadata(metadata: dict[str, Any]) -> bool | None:
    build_metadata = metadata.get("build_metadata")
    if not isinstance(build_metadata, dict):
        manifest = metadata.get("manifest")
        if isinstance(manifest, dict):
            build_metadata = manifest.get("build_metadata")
    if not isinstance(build_metadata, dict):
        return None
    cdefs = build_metadata.get("resolved_cdefs_extra")
    if not isinstance(cdefs, list):
        return None
    for item in cdefs:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized == "-DAPP_HUMAN_LOG_ENABLE=0":
            return False
        if normalized == "-DAPP_HUMAN_LOG_ENABLE=1":
            return True
    return None


def _apply_human_log_policy_override(
    *,
    role_report: RoleMachineReport,
    role: Role,
    artifact: RawArtifact,
    policy_enabled: bool | None,
    decode_diagnostics: list[MachineDecodeDiagnostic],
) -> None:
    if policy_enabled is not False or role_report.run is None:
        return
    if role_report.run.human_log_enable is False and role_report.run.human_log_level == 0:
        return
    role_report.run.human_log_enable = False
    role_report.run.human_log_level = 0
    decode_diagnostics.append(
        _diag(
            role=role,
            artifact=artifact,
            code="human_log_policy_override",
            message=(
                "Build flags disable human logs (APP_HUMAN_LOG_ENABLE=0); "
                "overrode decoded run snapshot fields."
            ),
        )
    )


def _role_has_telemetry(role_report: RoleMachineReport) -> bool:
    return any(
        [
            role_report.run is not None,
            bool(role_report.stat_frames),
            bool(role_report.packet_frames),
            bool(role_report.event_frames),
        ]
    )


def _enum_label(labels: dict[int, str], code: int) -> str:
    return labels.get(code, "unknown")


def _diag(
    *,
    code: str,
    message: str,
    role: Role | None = None,
    artifact: RawArtifact | None = None,
    offset: int | None = None,
) -> MachineDecodeDiagnostic:
    return MachineDecodeDiagnostic(
        role=role,
        artifact_id=artifact.id if artifact else None,
        artifact_path=artifact.storage_path if artifact else None,
        offset=offset,
        code=code,
        message=message,
    )
