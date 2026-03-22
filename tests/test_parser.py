from __future__ import annotations

import struct
from pathlib import Path

import pytest

from server.app.models.entities import Annotation, RawArtifact, Session as SessionModel
from server.app.services.parsing import flatten_machine_timeline, merge_session_logs
from server.app.services.reporting import generate_report
from shared.enums import RawArtifactType, ReportStatus, Role
from shared.mlog import MLOG_KIND_EVT, MLOG_KIND_PKT, MLOG_KIND_RUN, MLOG_KIND_STAT, build_mlog_frame


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
        template_dir=Path(__file__).resolve().parent.parent / "server" / "app" / "templates",
    )

    assert report.status == ReportStatus.READY.value
    assert "metrics" not in report.diagnostics_json
    assert "packet_delivery_ratio" not in report.diagnostics_json
    assert report.diagnostics_json["roles"]["TX"]["run"]["machine_detail"] == "packet"
    assert report.diagnostics_json["roles"]["RX"]["run"] is None

    html = (tmp_path / report.html_storage_path).read_text(encoding="utf-8")
    assert "TX Run Snapshot" in html
    assert "No decoded run snapshot for RX." in html
    assert "packet delivery ratio" not in html


def test_generate_report_fails_when_no_machine_artifacts_exist(db_session, tmp_path: Path) -> None:
    session = _create_session(db_session)

    report = generate_report(
        db_session,
        session=session,
        storage_root=tmp_path,
        reports_dir=tmp_path / "reports",
        template_dir=Path(__file__).resolve().parent.parent / "server" / "app" / "templates",
    )

    assert report.status == ReportStatus.FAILED.value
    assert any(item["code"] == "machine_report_unavailable" for item in report.diagnostics_json["decode_diagnostics"])
    html = (tmp_path / report.html_storage_path).read_text(encoding="utf-8")
    assert "No decodable machine telemetry was available for this session." in html


def _create_session(db_session) -> SessionModel:
    session = SessionModel(name="demo", status="merging", stop_mode="fixed_duration", default_duration_minutes=5)
    db_session.add(session)
    db_session.commit()
    return session


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
