from datetime import datetime, timedelta, timezone

from rtms.shared.time_sync import apply_offset, estimate_offset


def test_estimate_offset_uses_midpoint() -> None:
    send = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
    recv = send + timedelta(milliseconds=40)
    server = send + timedelta(milliseconds=70)
    sample = estimate_offset(send, server, recv)
    assert round(sample.estimated_offset_ms, 3) == 50.0
    corrected = apply_offset(send, sample.estimated_offset_ms)
    assert corrected == send + timedelta(milliseconds=50)

