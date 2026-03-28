from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TimeSyncSample(BaseModel):
    local_send_at: datetime
    server_time_at_reply: datetime
    local_recv_at: datetime
    estimated_offset_ms: float
    round_trip_ms: float


def estimate_offset(
    local_send_at: datetime, server_time_at_reply: datetime, local_recv_at: datetime
) -> TimeSyncSample:
    midpoint = local_send_at + (local_recv_at - local_send_at) / 2
    offset = server_time_at_reply - midpoint
    round_trip = local_recv_at - local_send_at
    return TimeSyncSample(
        local_send_at=local_send_at,
        server_time_at_reply=server_time_at_reply,
        local_recv_at=local_recv_at,
        estimated_offset_ms=offset.total_seconds() * 1000.0,
        round_trip_ms=round_trip.total_seconds() * 1000.0,
    )


def apply_offset(ts: datetime, offset_ms: float) -> datetime:
    return ts + timedelta(milliseconds=offset_ms)


class TimeCorrection(BaseModel):
    offset_ms: float = 0.0
    sample_count: int = 0
    source: str = "none"
    diagnostics: dict[str, float | str] = Field(default_factory=dict)

