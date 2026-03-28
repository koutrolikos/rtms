from __future__ import annotations

import struct

from rtms.shared.enums import Role

MLOG_MAGIC = b"MLOG"
MLOG_PROTOCOL_VERSION = 1
MLOG_HEADER_SIZE = 16

MLOG_ROLE_CODES = {
    Role.RX: 0,
    Role.TX: 1,
}
MLOG_ROLE_FROM_CODE = {code: role for role, code in MLOG_ROLE_CODES.items()}

MLOG_KIND_RUN = 1
MLOG_KIND_STAT = 2
MLOG_KIND_PKT = 3
MLOG_KIND_EVT = 4

MLOG_KIND_LABELS = {
    MLOG_KIND_RUN: "run",
    MLOG_KIND_STAT: "stat",
    MLOG_KIND_PKT: "pkt",
    MLOG_KIND_EVT: "evt",
}

MLOG_DETAIL_LABELS = {
    0: "summary",
    1: "packet",
}

MLOG_BUILD_LABELS = {
    0: "release",
    1: "debug",
}

MLOG_STATE_LABELS = {
    0: "unknown",
    1: "inhibited",
    2: "armed",
    3: "control",
    4: "data",
    5: "recovered",
}

MLOG_EVENT_LABELS = {
    1: "channel_state",
    2: "rx_mode",
    3: "tx_timeout_recover",
    4: "rx_recovery",
}

MLOG_REASON_LABELS = {
    0: "unknown",
    1: "startup",
    2: "runtime",
    3: "runtime_control",
    4: "irq_timeout",
    5: "overflow",
    6: "timeout",
}

MLOG_PACKET_ID_LABELS = {
    0x01: "gps",
    0x02: "imu_baro",
    0xFF: "unknown",
}

MLOG_DROP_REASON_LABELS = {
    0: "none",
    1: "control",
    2: "rssi",
    3: "lqi",
    4: "both",
}


def build_mlog_frame(
    *,
    kind_code: int,
    role: Role,
    t_ms: int,
    payload: bytes,
    version: int = MLOG_PROTOCOL_VERSION,
    flags: int = 0,
    reserved: int = 0,
) -> bytes:
    return b"".join(
        [
            MLOG_MAGIC,
            bytes([version, kind_code, MLOG_ROLE_CODES[role], flags]),
            struct.pack("<H", len(payload)),
            struct.pack("<H", reserved),
            struct.pack("<I", t_ms),
            payload,
        ]
    )
