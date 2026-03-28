from __future__ import annotations

import re
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from rtms.server.app.models.entities import Annotation, Artifact, RawArtifact, RunSession, SessionRoleRun
from rtms.server.app.presentation import register_template_helpers
from rtms.server.app.services.parsing import flatten_machine_timeline, merge_session_logs
from rtms.server.app.services.reporting import generate_report
from rtms.shared.enums import RawArtifactType, ReportStatus, Role
from rtms.shared.mlog import MLOG_KIND_EVT, MLOG_KIND_PKT, MLOG_KIND_RUN, MLOG_KIND_STAT, build_mlog_frame

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "rtms" / "server" / "app" / "templates"


def test_merge_session_logs_decodes_machine_frames_for_both_roles(db_session, tmp_path: Path) -> None:
    session = _create_session(db_session)
    _register_machine_artifact(db_session, tmp_path, session.id, Role.TX, _tx_machine_stream())
    _register_machine_artifact(db_session, tmp_path, session.id, Role.RX, _rx_machine_stream())
    db_session.add(Annotation(session_id=session.id, text="note"))
    db_session.commit()

    report = merge_session_logs(
        db_session,
        session=session,
        role_runs=[],
        raw_items=db_session.query(RawArtifact).all(),
        storage_root=tmp_path,
    )

    assert report.status == ReportStatus.READY
    assert report.roles["TX"].run is not None
    assert report.roles["TX"].run.machine_detail == "packet"
    assert report.roles["TX"].run.airtime_limit_us == 306_000_000
    assert report.roles["TX"].final_stat is not None
    assert report.roles["TX"].final_stat.attempt_count == 9
    assert report.roles["TX"].packet_frames[0].complete_latency_ms == 17
    assert report.roles["TX"].event_frames[0].event_id == "channel_state"
    assert report.roles["RX"].run is not None
    assert report.roles["RX"].run.rx_min_rssi_dbm == -92
    assert report.roles["RX"].final_stat is not None
    assert report.roles["RX"].final_stat.accepted_count == 7
    assert report.roles["RX"].packet_frames[0].drop_reason == "lqi"
    assert report.roles["RX"].event_frames[0].event_id == "rx_mode"
    assert report.annotations[0].text == "note"

    dumped = report.model_dump(mode="json")
    assert "metrics" not in dumped
    assert flatten_machine_timeline(report)[0]["kind"] == "run"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("bad_magic", "sync_not_found"),
        ("unsupported_version", "unsupported_version"),
        ("truncated_frame", "truncated_frame"),
        ("short_payload", "payload_size_mismatch"),
        ("reserved_mismatch", "reserved_mismatch"),
        ("unknown_kind", "unknown_kind"),
        ("unknown_event", "unknown_event_id"),
    ],
)
def test_merge_session_logs_reports_decode_diagnostics_for_invalid_machine_frames(
    db_session,
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    session = _create_session(db_session)
    _register_machine_artifact(
        db_session,
        tmp_path,
        session.id,
        Role.TX,
        _invalid_machine_payload(case),
        name="invalid.rttbin",
    )

    report = merge_session_logs(
        db_session,
        session=session,
        role_runs=[],
        raw_items=db_session.query(RawArtifact).all(),
        storage_root=tmp_path,
    )

    codes = {item.code for item in report.decode_diagnostics}
    assert expected_code in codes
    assert report.status == ReportStatus.FAILED


def test_generate_report_renders_partial_machine_report_and_excludes_legacy_metrics(db_session, tmp_path: Path) -> None:
    session = _create_session(db_session)
    _register_machine_artifact(db_session, tmp_path, session.id, Role.TX, _tx_machine_stream())

    report = generate_report(
        db_session,
        session=session,
        storage_root=tmp_path,
        reports_dir=tmp_path / "reports",
        template_dir=TEMPLATE_DIR,
    )

    assert report.status == ReportStatus.READY.value
    assert "metrics" not in report.diagnostics
    assert "packet_delivery_ratio" not in report.diagnostics
    assert report.diagnostics["roles"]["TX"]["run"]["machine_detail"] == "packet"
    assert report.diagnostics["roles"]["RX"]["run"] is None

    html = (tmp_path / report.html_storage_path).read_text(encoding="utf-8")
    assert "RF Link Verdict" in html
    assert "Packet Type Breakdown" in html
    assert "Loss Hotspots Over Time" in html
    assert "packet delivery ratio" not in html


def test_generate_report_fails_when_no_machine_artifacts_exist(db_session, tmp_path: Path) -> None:
    session = _create_session(db_session)

    report = generate_report(
        db_session,
        session=session,
        storage_root=tmp_path,
        reports_dir=tmp_path / "reports",
        template_dir=TEMPLATE_DIR,
    )

    assert report.status == ReportStatus.FAILED.value
    assert any(item["code"] == "machine_report_unavailable" for item in report.diagnostics["decode_diagnostics"])
    html = (tmp_path / report.html_storage_path).read_text(encoding="utf-8")
    assert "RF Link Verdict" in html
    assert "No RX packet RSSI/LQI samples available." in html


def test_report_fragment_clamps_svg_coordinates_and_caps_large_tables() -> None:
    temporal = [
        {
            "start_ms": index * 10,
            "end_ms": index * 10 + 9,
            "tx_packets": 10,
            "rx_seen_packets": 8,
            "rx_accepted_packets": 7,
            "delivery_ratio": 1.4 if index % 2 == 0 else -0.3,
            "visibility_ratio": 1.2 if index % 3 == 0 else -0.2,
            "acceptance_ratio": 1.6 if index % 5 == 0 else -0.1,
            "avg_rssi_dbm": -55.0,
            "avg_lqi": 110.0,
        }
        for index in range(205)
    ]
    rolling = [
        {
            "t_ms": index * 10,
            "window_bins": 5,
            "tx_packets": 10,
            "rx_accepted_packets": 7,
            "delivery_ratio": 1.2 if index % 2 == 0 else -0.2,
            "loss_ratio": 1.1 if index % 3 == 0 else -0.1,
        }
        for index in range(205)
    ]
    throughput = [
        {
            "start_ms": index * 10,
            "end_ms": index * 10 + 9,
            "tx_packets": 10,
            "tx_bytes": 240,
            "offered_bits_per_sec": -120.0 if index == 0 else 1200.0 + index,
            "rx_accepted_packets": 7,
            "rx_accepted_bytes": 168,
            "delivered_bits_per_sec": -80.0 if index == 1 else 900.0 + index,
        }
        for index in range(205)
    ]
    relationship = [
        {
            "rssi_start_dbm": -80 + index,
            "rssi_end_dbm": -79 + index,
            "total_packets": 20,
            "accepted_packets": 10,
            "acceptance_ratio": 1.3 if index == 0 else -0.25,
            "rejection_ratio": 0.5,
            "avg_lqi": 100.0,
        }
        for index in range(2)
    ]
    channel_events = [
        {
            "t_ms": index * 10,
            "role": "TX" if index % 2 == 0 else "RX",
            "event_id": "channel_state",
            "state": "active",
            "reason": "none",
            "active_freq_hz": 433_200_000,
            "backup_freq_hz": 434_600_000,
        }
        for index in range(205)
    ]
    inter_arrival_points = [{"t_ms": index * 10, "delta_ms": 10 + index} for index in range(205)]

    rendered = _render_report_fragment(
        derived_metrics={
            "link_overview": {
                "headline": "Operator-safe rendering",
                "reason": "Synthetic outlier coverage for charts and raw-data caps.",
                "tx_packets": 2050,
                "rx_seen_packets": 1640,
                "rx_accepted_packets": 1435,
                "session_span_ms": 2050,
                "delivery_ratio": 1.4,
                "visibility_ratio": -0.2,
                "acceptance_ratio": 1.3,
                "accepted_rssi_p50_dbm": -54.0,
            },
            "quality_bars": [
                {
                    "label": "Delivery confidence",
                    "value_text": "Clamped",
                    "percent": 82.0,
                    "detail": "Outlier values should stay inside bounds.",
                    "tone": "good",
                }
            ],
            "channel_snapshot": {
                "rf_bitrate_bps": 4800,
                "rx_thresh_enable": True,
                "active_freq_hz": 433_200_000,
                "backup_freq_hz": 434_600_000,
                "machine_log_stat_period_ms": 5000,
                "rx_min_rssi_dbm": -92,
                "rx_min_lqi": 8,
                "rx_poll_interval_ms": 110,
                "rx_host_bridge_budget_count": 25,
                "tx_complete_timeout_ms": 200,
            },
            "signal_summary": {},
            "tx_health": {"completed_count": 2050},
            "rx_health": {"filtered_total_count": 0},
            "tx_timing_summary": {},
            "packet_type_breakdown": [],
            "drop_reason_breakdown": [{"drop_reason": "lqi", "count": 3, "share": 1.0}],
            "link_time_bins": temporal,
            "worst_link_bins": [temporal[0]],
            "pdr_per_relationship_by_rssi": relationship,
            "rolling_reliability_by_time": rolling,
            "inter_arrival_points": inter_arrival_points,
            "inter_arrival_histogram": [{"delta_start_ms": 0, "delta_end_ms": 10, "count": 5}],
            "rssi_distribution": [],
            "throughput_by_time": throughput,
            "channel_events": channel_events,
        }
    )

    assert rendered.count("Showing first 200 of 205 rows.") >= 4
    assert "[1990, 1999)" in rendered
    assert "[2000, 2009)" not in rendered
    assert "/api/sessions/demo-session/report/json" in rendered
    assert "/api/sessions/demo-session/timeline" in rendered

    polyline_points = re.findall(r'<polyline points="([^"]+)"', rendered)
    assert polyline_points
    for polyline in polyline_points:
        for point in polyline.split():
            x_text, y_text = point.split(",")
            x = float(x_text)
            y = float(y_text)
            assert 0.0 <= x <= 760.0
            assert 0.0 <= y <= 220.0

    rects = re.findall(r'<rect x="([^"]+)" y="([^"]+)" width="([^"]+)" height="([^"]+)"', rendered)
    assert rects
    for x_text, y_text, width_text, height_text in rects:
        x = float(x_text)
        y = float(y_text)
        width = float(width_text)
        height = float(height_text)
        assert 0.0 <= x <= 760.0
        assert 0.0 <= y <= 220.0
        assert width >= 0.0
        assert height >= 0.0
        assert x + width <= 760.01
        assert y + height <= 220.01


def test_merge_session_logs_overrides_human_log_fields_when_build_policy_disables_them(db_session, tmp_path: Path) -> None:
    session = _create_session(db_session)
    _register_machine_artifact(db_session, tmp_path, session.id, Role.TX, _tx_machine_stream())

    artifact = Artifact(
        session_id=session.id,
        status="ready",
        origin_type="github_build",
        metadata_payload={
            "build_metadata": {
                "resolved_cdefs_extra": [
                    "-DAPP_ROLE_MODE=APP_ROLE_MODE_TX",
                    "-DAPP_HUMAN_LOG_ENABLE=0",
                    "-DAPP_MACHINE_LOG_ENABLE=1",
                ]
            }
        },
    )
    db_session.add(artifact)
    db_session.commit()

    role_run = SessionRoleRun(
        session_id=session.id,
        role=Role.TX.value,
        host_id="host-1",
        artifact_id=artifact.id,
        status="completed",
    )
    db_session.add(role_run)
    db_session.commit()

    report = merge_session_logs(
        db_session,
        session=session,
        role_runs=[role_run],
        raw_items=db_session.query(RawArtifact).all(),
        storage_root=tmp_path,
    )

    assert report.roles["TX"].run is not None
    assert report.roles["TX"].run.human_log_enable is False
    assert report.roles["TX"].run.human_log_level == 0
    assert any(item.code == "human_log_policy_override" for item in report.decode_diagnostics)


def _create_session(db_session) -> RunSession:
    session = RunSession(name="demo", status="merging", stop_mode="fixed_duration", default_duration_minutes=5)
    db_session.add(session)
    db_session.commit()
    return session


def _render_report_fragment(*, derived_metrics: dict) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    register_template_helpers(environment)
    return environment.get_template("report_fragment.html").render(
        {
            "session": SimpleNamespace(id="demo-session"),
            "derived_metrics": derived_metrics,
            "raw_artifacts": [],
            "events": [],
            "host_labels": {},
        }
    )


def _register_machine_artifact(
    db_session,
    tmp_path: Path,
    session_id: str,
    role: Role,
    data: bytes,
    *,
    name: str = "capture.rttbin",
) -> None:
    relative_path = Path("raw") / session_id / role.value / name
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    db_session.add(
        RawArtifact(
            session_id=session_id,
            role=role.value,
            type=RawArtifactType.RTT_MACHINE_LOG.value,
            storage_path=str(relative_path),
            hash_sha256="hash",
            size_bytes=len(data),
        )
    )
    db_session.commit()


def _tx_machine_stream() -> bytes:
    return b"".join(
        [
            _valid_tx_run_frame(),
            build_mlog_frame(
                kind_code=MLOG_KIND_STAT,
                role=Role.TX,
                t_ms=1000,
                payload=struct.pack(
                    "<18I",
                    9,
                    9,
                    8,
                    4,
                    4,
                    5,
                    4,
                    0,
                    0,
                    1,
                    0,
                    0,
                    1,
                    18,
                    17,
                    4,
                    10_800,
                    306_000_000,
                ),
            ),
            build_mlog_frame(
                kind_code=MLOG_KIND_PKT,
                role=Role.TX,
                t_ms=1200,
                payload=struct.pack("<4B2I", 0x01, 0x01, 7, 24, 17, 4),
            ),
            build_mlog_frame(
                kind_code=MLOG_KIND_EVT,
                role=Role.TX,
                t_ms=1300,
                payload=struct.pack("<4B", 1, 4, 1, 0) + struct.pack("<BBHII", 2, 3, 0, 433_200_000, 434_600_000),
            ),
        ]
    )


def _rx_machine_stream() -> bytes:
    return b"".join(
        [
            build_mlog_frame(
                kind_code=MLOG_KIND_RUN,
                role=Role.RX,
                t_ms=0,
                payload=b"".join(
                    [
                        struct.pack("<8B", 1, 1, 4, 2, 1, 1, 2, 3),
                        struct.pack("<4I", 433_200_000, 434_600_000, 76_760, 5_000),
                        struct.pack("<B", 1),
                        b"\x00\x00\x00",
                        struct.pack("<i", -92),
                        struct.pack("<3I", 110, 25, 8),
                    ]
                ),
            ),
            build_mlog_frame(
                kind_code=MLOG_KIND_STAT,
                role=Role.RX,
                t_ms=1000,
                payload=struct.pack("<16I", 9, 7, 2, 1, 0, 0, 2, 0, 2, 0, 0, 1, 0, 4, 2, 4),
            ),
            build_mlog_frame(
                kind_code=MLOG_KIND_PKT,
                role=Role.RX,
                t_ms=1200,
                payload=struct.pack("<6BbBB", 0x01, 0x01, 7, 24, 0, 3, -47, 109, 1),
            ),
            build_mlog_frame(
                kind_code=MLOG_KIND_EVT,
                role=Role.RX,
                t_ms=1300,
                payload=struct.pack("<4B", 2, 4, 1, 0) + struct.pack("<BBHII", 2, 3, 0, 433_200_000, 434_600_000),
            ),
        ]
    )


def _valid_tx_run_frame() -> bytes:
    return build_mlog_frame(
        kind_code=MLOG_KIND_RUN,
        role=Role.TX,
        t_ms=0,
        payload=struct.pack(
            "<8B8I",
            1,
            1,
            4,
            2,
            1,
            1,
            2,
            3,
            433_200_000,
            434_600_000,
            76_760,
            5_000,
            306_000_000,
            200,
            50,
            20,
        ),
    )


def _invalid_machine_payload(case: str) -> bytes:
    if case == "bad_magic":
        return b"NOPE"
    if case == "unsupported_version":
        return build_mlog_frame(kind_code=MLOG_KIND_RUN, role=Role.TX, t_ms=0, payload=b"\x00" * 40, version=2)
    if case == "truncated_frame":
        return _valid_tx_run_frame()[:-1]
    if case == "short_payload":
        return build_mlog_frame(kind_code=MLOG_KIND_RUN, role=Role.TX, t_ms=0, payload=b"\x00" * 39)
    if case == "reserved_mismatch":
        return build_mlog_frame(kind_code=MLOG_KIND_RUN, role=Role.TX, t_ms=0, payload=b"\x00" * 40, reserved=1)
    if case == "unknown_kind":
        return build_mlog_frame(kind_code=99, role=Role.TX, t_ms=0, payload=b"")
    if case == "unknown_event":
        return build_mlog_frame(
            kind_code=MLOG_KIND_EVT,
            role=Role.TX,
            t_ms=0,
            payload=struct.pack("<4B", 99, 4, 1, 0),
        )
    raise AssertionError(f"unknown invalid machine payload case: {case}")
