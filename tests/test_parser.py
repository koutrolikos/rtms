from __future__ import annotations

from pathlib import Path

from server.app.models.entities import Annotation, RawArtifact, Session as SessionModel, SessionRoleRun
from server.app.services.parsing import merge_session_logs
from shared.enums import RawArtifactType, Role
from shared.time_sync import utc_now


def test_merge_session_logs_correlates_sequences(db_session, tmp_path: Path) -> None:
    session = SessionModel(name="demo", status="capturing", stop_mode="fixed_duration", default_duration_minutes=5)
    db_session.add(session)
    db_session.commit()
    tx_role = SessionRoleRun(
        session_id=session.id,
        role=Role.TX.value,
        agent_id="agent-tx",
        status="completed",
        capture_started_at=utc_now(),
        diagnostics_json={"time_sync_samples": [{"estimated_offset_ms": 0.0}]},
    )
    rx_role = SessionRoleRun(
        session_id=session.id,
        role=Role.RX.value,
        agent_id="agent-rx",
        status="completed",
        capture_started_at=tx_role.capture_started_at,
        diagnostics_json={"time_sync_samples": [{"estimated_offset_ms": 5.0}]},
    )
    db_session.add_all([tx_role, rx_role])
    db_session.commit()

    tx_rel = Path("raw") / session.id / "TX.log"
    rx_rel = Path("raw") / session.id / "RX.log"
    (tmp_path / tx_rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / tx_rel).write_text("[0.000] event=tx_packet seq=1 rssi=-40 snr=12\n[1.000] event=tx_packet seq=2\n", encoding="utf-8")
    (tmp_path / rx_rel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / rx_rel).write_text("[0.100] event=rx_packet seq=1 rssi=-42 snr=10\n", encoding="utf-8")
    db_session.add_all(
        [
            RawArtifact(
                session_id=session.id,
                role=Role.TX.value,
                type=RawArtifactType.RTT_LOG.value,
                storage_path=str(tx_rel),
                hash_sha256="1",
                size_bytes=1,
            ),
            RawArtifact(
                session_id=session.id,
                role=Role.RX.value,
                type=RawArtifactType.RTT_LOG.value,
                storage_path=str(rx_rel),
                hash_sha256="2",
                size_bytes=1,
            ),
            Annotation(session_id=session.id, text="note"),
        ]
    )
    db_session.commit()
    report = merge_session_logs(
        db_session,
        session=session,
        role_runs=[tx_role, rx_role],
        raw_items=db_session.query(RawArtifact).all(),
        storage_root=tmp_path,
    )
    assert report.metrics["packet_tx_count"] == 2
    assert report.metrics["packet_rx_count"] == 1
    assert report.metrics["packet_correlated_count"] == 1
    assert report.metrics["annotation_count"] == 1
    assert report.metrics["packet_delivery_ratio"] == 0.5

