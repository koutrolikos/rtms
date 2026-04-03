from __future__ import annotations

import re
from pathlib import Path

from rtms.shared.enums import Role
from rtms.shared.schemas import (
    HighAltitudeCCBuildConfig,
    HighAltitudeCCBuildConfigConstraints,
)


HIGH_ALTITUDE_CC_REPO_ID = "high-altitude-cc"
HIGH_ALTITUDE_CC_APP_CONFIG_PATH = "Core/Inc/app_config.h"
HIGH_ALTITUDE_CC_DEFAULT_BUILD_DIR = "build/debug"
HIGH_ALTITUDE_CC_TARGET_NAME = "High-Altitude-CC"
HIGH_ALTITUDE_CC_TARGET_FILENAME = f"{HIGH_ALTITUDE_CC_TARGET_NAME}.elf"
HIGH_ALTITUDE_CC_ARTIFACT_EXTENSIONS = ("elf", "hex", "bin", "map")
HIGH_ALTITUDE_CC_MACHINE_LOG_DETAIL_SUMMARY = 0
HIGH_ALTITUDE_CC_MACHINE_LOG_DETAIL_PACKET = 1
HIGH_ALTITUDE_CC_ROLE_MACROS = {
    Role.TX: "APP_ROLE_MODE_TX",
    Role.RX: "APP_ROLE_MODE_RX",
}
_GUARDED_DEFINE_RE = re.compile(r"^\s*#\s*ifndef\s+(?P<macro>[A-Za-z_][A-Za-z0-9_]*)\s*$")
_NESTED_IF_RE = re.compile(r"^\s*#\s*(?P<directive>ifdef|ifndef)\s+(?P<macro>[A-Za-z_][A-Za-z0-9_]*)\s*$")
_ANY_IF_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
_ELSE_RE = re.compile(r"^\s*#\s*else\b")
_ENDIF_RE = re.compile(r"^\s*#\s*endif\b")
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(?P<macro>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<value>.+?)\s*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def high_altitude_cc_artifact_globs(build_dir: str = HIGH_ALTITUDE_CC_DEFAULT_BUILD_DIR) -> list[str]:
    return [f"{build_dir}/{HIGH_ALTITUDE_CC_TARGET_NAME}.{suffix}" for suffix in HIGH_ALTITUDE_CC_ARTIFACT_EXTENSIONS]


def high_altitude_cc_elf_glob(build_dir: str = HIGH_ALTITUDE_CC_DEFAULT_BUILD_DIR) -> str:
    return f"{build_dir}/{HIGH_ALTITUDE_CC_TARGET_FILENAME}"


def high_altitude_cc_cmake_build_type(build_dir: str | Path) -> str:
    build_dir_name = Path(build_dir).name.lower()
    if build_dir_name == "debug":
        return "Debug"
    if build_dir_name == "release":
        return "Release"
    raise ValueError(
        "High-Altitude-CC build_dir must end with 'debug' or 'release' "
        f"to derive the CMake build type: {build_dir}"
    )


def _strip_macro_value(value: str) -> str:
    value = re.sub(r"/\*.*?\*/", "", value)
    value = re.sub(r"//.*$", "", value)
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    return value


def _find_guarded_block(lines: list[str], macro: str) -> tuple[int, int]:
    start = None
    for index, line in enumerate(lines):
        match = _GUARDED_DEFINE_RE.match(line)
        if match and match.group("macro") == macro:
            start = index
            break
    if start is None:
        raise ValueError(f"missing default macro definition for {macro}")

    depth = 0
    for index in range(start, len(lines)):
        line = lines[index]
        if _ANY_IF_RE.match(line):
            depth += 1
            continue
        if _ENDIF_RE.match(line):
            depth -= 1
            if depth == 0:
                return start, index
    raise ValueError(f"unterminated default macro definition for {macro}")


def _extract_effective_macro_value(source: str, macro: str) -> str:
    lines = source.splitlines()
    start, end = _find_guarded_block(lines, macro)
    active_stack = [True]
    branch_stack: list[tuple[bool, bool]] = []

    for line in lines[start + 1 : end]:
        match = _NESTED_IF_RE.match(line)
        if match:
            parent_active = active_stack[-1]
            condition = False if match.group("directive") == "ifdef" else True
            branch_stack.append((parent_active, condition))
            active_stack.append(parent_active and condition)
            continue
        if _ELSE_RE.match(line):
            if not branch_stack:
                raise ValueError(f"unexpected #else while parsing {macro}")
            parent_active, condition = branch_stack[-1]
            active_stack[-1] = parent_active and not condition
            continue
        if _ENDIF_RE.match(line):
            if not branch_stack:
                raise ValueError(f"unexpected #endif while parsing {macro}")
            branch_stack.pop()
            active_stack.pop()
            continue
        if not active_stack[-1]:
            continue
        match = _DEFINE_RE.match(line)
        if match and match.group("macro") == macro:
            return _strip_macro_value(match.group("value"))

    raise ValueError(f"missing active default value for {macro}")


def _parse_int(value: str) -> int:
    normalized = re.sub(r"[uUlL]+$", "", value.strip())
    return int(normalized, 0)


def _parse_int_list(value: str) -> list[int]:
    parts = [item.strip() for item in value.split(",")]
    return [_parse_int(item) for item in parts if item]


def _resolve_macro_value(source: str, macro: str, *, seen: set[str] | None = None) -> str:
    path = seen or set()
    if macro in path:
        chain = " -> ".join([*sorted(path), macro])
        raise ValueError(f"cyclic macro resolution detected: {chain}")
    value = _extract_effective_macro_value(source, macro)
    if _IDENTIFIER_RE.match(value):
        return _resolve_macro_value(source, value, seen=path | {macro})
    return value


def _resolve_macro_int(source: str, macro: str, *, seen: set[str] | None = None) -> int:
    try:
        return _parse_int(_resolve_macro_value(source, macro, seen=seen))
    except ValueError:
        value = _resolve_macro_value(source, macro, seen=seen)
        raise ValueError(f"unsupported macro value for {macro}: {value}") from None


def _format_hex(value: int, *, width: int | None = None, suffix: str = "") -> str:
    digits = f"{value:X}" if width is None else f"{value:0{width}X}"
    return f"0x{digits}{suffix}"


def _format_int_list(values: list[int], *, suffix: str = "") -> str:
    return ",".join(f"{value}{suffix}" for value in values)


def _format_hex_list(values: list[int], *, width: int = 2, suffix: str = "") -> str:
    return ",".join(_format_hex(value, width=width, suffix=suffix) for value in values)


def normalize_high_altitude_cc_build_config(build_config: HighAltitudeCCBuildConfig) -> HighAltitudeCCBuildConfig:
    if build_config.machine_log_detail == HIGH_ALTITUDE_CC_MACHINE_LOG_DETAIL_PACKET:
        return build_config
    return build_config.model_copy(update={"machine_log_detail": HIGH_ALTITUDE_CC_MACHINE_LOG_DETAIL_PACKET})


def high_altitude_cc_build_constraints() -> HighAltitudeCCBuildConfigConstraints:
    return HighAltitudeCCBuildConfigConstraints()


def parse_high_altitude_cc_build_config(source: str) -> HighAltitudeCCBuildConfig:
    allowlist_count = _resolve_macro_int(source, "APP_CHSEL_ALLOWLIST_COUNT")
    allowlist_values = _parse_int_list(_resolve_macro_value(source, "APP_CHSEL_ALLOWLIST_HZ_LIST"))
    if allowlist_count > len(allowlist_values):
        raise ValueError("APP_CHSEL_ALLOWLIST_COUNT exceeds APP_CHSEL_ALLOWLIST_HZ_LIST entries")

    exclusion_mask_count = _resolve_macro_int(source, "APP_CHSEL_EXCLUSION_MASK_COUNT")
    exclusion_masks = [
        {
            "center_hz": _resolve_macro_int(source, f"APP_CHSEL_EXCLUSION_MASK{index}_CENTER_HZ"),
            "half_bw_hz": _resolve_macro_int(source, f"APP_CHSEL_EXCLUSION_MASK{index}_HALF_BW_HZ"),
        }
        for index in range(exclusion_mask_count)
    ]

    return HighAltitudeCCBuildConfig(
        machine_log_detail=_resolve_macro_int(source, "APP_MACHINE_LOG_DETAIL"),
        machine_log_stat_period_ms=_resolve_macro_int(source, "APP_MACHINE_LOG_STAT_PERIOD_MS"),
        rf_bitrate_bps=_resolve_macro_int(source, "APP_RF_BITRATE_BPS"),
        rf_rx_bw_hz=_resolve_macro_int(source, "APP_RF_RX_BW_HZ"),
        rf_deviation_hz=_resolve_macro_int(source, "APP_RF_DEVIATION_HZ"),
        rf_preamble_bytes=_resolve_macro_int(source, "APP_RF_PREAMBLE_BYTES"),
        rf_sync_word=_resolve_macro_int(source, "APP_RF_SYNC_WORD"),
        rf_pa_table=_parse_int_list(_resolve_macro_value(source, "APP_RF_PA_TABLE_LIST")),
        allowlist_hz=allowlist_values[:allowlist_count],
        band_min_hz=_resolve_macro_int(source, "APP_CHSEL_BAND_MIN_HZ"),
        band_max_hz=_resolve_macro_int(source, "APP_CHSEL_BAND_MAX_HZ"),
        guard_band_hz=_resolve_macro_int(source, "APP_CHSEL_GUARD_BAND_HZ"),
        exclusion_masks=exclusion_masks,
        backup_failover_holdoff_ms=_resolve_macro_int(source, "APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS"),
        rx_thresh_enable=_resolve_macro_int(source, "APP_RX_THRESH_ENABLE"),
        rx_min_rssi_dbm=_resolve_macro_int(source, "APP_RX_MIN_RSSI_DBM"),
        rx_min_lqi=_resolve_macro_int(source, "APP_RX_MIN_LQI"),
        rx_thresh_log_every=_resolve_macro_int(source, "APP_RX_THRESH_LOG_EVERY"),
        rx_poll_interval_ms=_resolve_macro_int(source, "APP_RX_POLL_INTERVAL_MS"),
        tx_complete_timeout_ms=_resolve_macro_int(source, "APP_TX_COMPLETE_TIMEOUT_MS"),
        rx_host_bridge_budget=_resolve_macro_int(source, "APP_RX_HOST_BRIDGE_BUDGET"),
        telem_gps_period_ms=_resolve_macro_int(source, "APP_TELEM_GPS_PERIOD_MS"),
        telem_imu_baro_period_ms=_resolve_macro_int(source, "APP_TELEM_IMU_BARO_PERIOD_MS"),
        airtime_limit_us_per_hour=_resolve_macro_int(source, "APP_AIRTIME_LIMIT_US_PER_HOUR"),
    )


def build_high_altitude_cc_cdefs(role: Role, build_config: HighAltitudeCCBuildConfig) -> list[str]:
    config = normalize_high_altitude_cc_build_config(build_config)
    exclusion_masks = list(config.exclusion_masks)
    return [
        f"-DAPP_ROLE_MODE={HIGH_ALTITUDE_CC_ROLE_MACROS[role]}",
        "-DAPP_HUMAN_LOG_ENABLE=0",
        "-DAPP_MACHINE_LOG_ENABLE=1",
        f"-DAPP_MACHINE_LOG_DETAIL={config.machine_log_detail}",
        f"-DAPP_MACHINE_LOG_STAT_PERIOD_MS={config.machine_log_stat_period_ms}U",
        f"-DAPP_RF_BITRATE_BPS={config.rf_bitrate_bps}UL",
        f"-DAPP_RF_RX_BW_HZ={config.rf_rx_bw_hz}UL",
        f"-DAPP_RF_DEVIATION_HZ={config.rf_deviation_hz}UL",
        f"-DAPP_RF_PREAMBLE_BYTES={config.rf_preamble_bytes}U",
        f"-DAPP_RF_SYNC_WORD={_format_hex(config.rf_sync_word, width=4, suffix='UL')}",
        f"-DAPP_RF_PA_TABLE_LIST={_format_hex_list(config.rf_pa_table, suffix='U')}",
        f"-DAPP_CHSEL_ALLOWLIST_COUNT={len(config.allowlist_hz)}U",
        f"-DAPP_CHSEL_ALLOWLIST_HZ_LIST={_format_int_list(config.allowlist_hz, suffix='UL')}",
        f"-DAPP_CHSEL_BAND_MIN_HZ={config.band_min_hz}UL",
        f"-DAPP_CHSEL_BAND_MAX_HZ={config.band_max_hz}UL",
        f"-DAPP_CHSEL_GUARD_BAND_HZ={config.guard_band_hz}UL",
        f"-DAPP_CHSEL_EXCLUSION_MASK_COUNT={len(exclusion_masks)}U",
        *[
            f"-DAPP_CHSEL_EXCLUSION_MASK{index}_CENTER_HZ="
            f"{(exclusion_masks[index].center_hz if index < len(exclusion_masks) else 0)}UL"
            for index in range(4)
        ],
        *[
            f"-DAPP_CHSEL_EXCLUSION_MASK{index}_HALF_BW_HZ="
            f"{(exclusion_masks[index].half_bw_hz if index < len(exclusion_masks) else 0)}UL"
            for index in range(4)
        ],
        f"-DAPP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS={config.backup_failover_holdoff_ms}U",
        f"-DAPP_RX_THRESH_ENABLE={config.rx_thresh_enable}",
        f"-DAPP_RX_MIN_RSSI_DBM={config.rx_min_rssi_dbm}",
        f"-DAPP_RX_MIN_LQI={config.rx_min_lqi}U",
        f"-DAPP_RX_THRESH_LOG_EVERY={config.rx_thresh_log_every}U",
        f"-DAPP_RX_POLL_INTERVAL_MS={config.rx_poll_interval_ms}U",
        f"-DAPP_TX_COMPLETE_TIMEOUT_MS={config.tx_complete_timeout_ms}U",
        f"-DAPP_RX_HOST_BRIDGE_BUDGET={config.rx_host_bridge_budget}U",
        f"-DAPP_TELEM_GPS_PERIOD_MS={config.telem_gps_period_ms}U",
        f"-DAPP_TELEM_IMU_BARO_PERIOD_MS={config.telem_imu_baro_period_ms}U",
        f"-DAPP_AIRTIME_LIMIT_US_PER_HOUR={config.airtime_limit_us_per_hour}UL",
    ]
