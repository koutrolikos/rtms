from __future__ import annotations

import re

from shared.enums import Role
from shared.schemas import (
    HighAltitudeCCBuildConfig,
    HighAltitudeCCBuildConfigConstraints,
    HighAltitudeCCChannelSelectionConfig,
    HighAltitudeCCExclusionMask,
)


HIGH_ALTITUDE_CC_REPO_ID = "high-altitude-cc"
HIGH_ALTITUDE_CC_APP_CONFIG_PATH = "Core/Inc/app_config.h"
HIGH_ALTITUDE_CC_MAX_ALLOWLIST_COUNT = 2
HIGH_ALTITUDE_CC_MAX_EXCLUSION_MASKS = 4
HIGH_ALTITUDE_CC_ROLE_MACROS = {
    Role.TX: "APP_ROLE_MODE_TX",
    Role.RX: "APP_ROLE_MODE_RX",
}


def _extract_macro_value(source: str, macro: str) -> str:
    pattern = re.compile(
        rf"^\s*#ifndef\s+{re.escape(macro)}\s*$\n^\s*#define\s+{re.escape(macro)}\s+(?P<value>.+?)\s*$",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"missing default macro definition for {macro}")
    value = match.group("value")
    value = re.sub(r"/\*.*?\*/", "", value)
    value = re.sub(r"//.*$", "", value)
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    return value


def _parse_int(value: str) -> int:
    normalized = re.sub(r"(?<=\d)[uUlL]+$", "", value.strip())
    return int(normalized, 10)


def _parse_hz_list(value: str) -> list[int]:
    return [_parse_int(item.strip()) for item in value.split(",") if item.strip()]


def high_altitude_cc_build_constraints() -> HighAltitudeCCBuildConfigConstraints:
    return HighAltitudeCCBuildConfigConstraints()


def parse_high_altitude_cc_build_config(source: str) -> HighAltitudeCCBuildConfig:
    allowlist_count = _parse_int(_extract_macro_value(source, "APP_CHSEL_ALLOWLIST_COUNT"))
    if allowlist_count < 1 or allowlist_count > HIGH_ALTITUDE_CC_MAX_ALLOWLIST_COUNT:
        raise ValueError(f"unsupported APP_CHSEL_ALLOWLIST_COUNT {allowlist_count}")

    allowlist_hz = _parse_hz_list(_extract_macro_value(source, "APP_CHSEL_ALLOWLIST_HZ_LIST"))
    if allowlist_count > len(allowlist_hz):
        raise ValueError("APP_CHSEL_ALLOWLIST_COUNT exceeds parsed allowlist values")

    exclusion_mask_count = _parse_int(_extract_macro_value(source, "APP_CHSEL_EXCLUSION_MASK_COUNT"))
    if exclusion_mask_count < 0 or exclusion_mask_count > HIGH_ALTITUDE_CC_MAX_EXCLUSION_MASKS:
        raise ValueError(f"unsupported APP_CHSEL_EXCLUSION_MASK_COUNT {exclusion_mask_count}")

    exclusion_masks = []
    for index in range(exclusion_mask_count):
        exclusion_masks.append(
            HighAltitudeCCExclusionMask(
                center_hz=_parse_int(
                    _extract_macro_value(source, f"APP_CHSEL_EXCLUSION_MASK{index}_CENTER_HZ")
                ),
                half_bw_hz=_parse_int(
                    _extract_macro_value(source, f"APP_CHSEL_EXCLUSION_MASK{index}_HALF_BW_HZ")
                ),
            )
        )

    return HighAltitudeCCBuildConfig(
        app_debug_enable=_parse_int(_extract_macro_value(source, "APP_DEBUG_ENABLE")),
        app_log_level=_parse_int(_extract_macro_value(source, "APP_LOG_LEVEL")),
        chsel=HighAltitudeCCChannelSelectionConfig(
            allowlist_hz=allowlist_hz[:allowlist_count],
            band_min_hz=_parse_int(_extract_macro_value(source, "APP_CHSEL_BAND_MIN_HZ")),
            band_max_hz=_parse_int(_extract_macro_value(source, "APP_CHSEL_BAND_MAX_HZ")),
            our_half_bw_hz=_parse_int(_extract_macro_value(source, "APP_CHSEL_OUR_HALF_BW_HZ")),
            guard_band_hz=_parse_int(_extract_macro_value(source, "APP_CHSEL_GUARD_BAND_HZ")),
            exclusion_masks=exclusion_masks,
            backup_failover_holdoff_ms=_parse_int(
                _extract_macro_value(source, "APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS")
            ),
        ),
    )


def build_high_altitude_cc_cdefs(role: Role, build_config: HighAltitudeCCBuildConfig) -> list[str]:
    cdefs = [
        f"-DAPP_ROLE_MODE={HIGH_ALTITUDE_CC_ROLE_MACROS[role]}",
        f"-DAPP_DEBUG_ENABLE={build_config.app_debug_enable}",
        f"-DAPP_LOG_LEVEL={build_config.app_log_level}",
        f"-DAPP_CHSEL_ALLOWLIST_COUNT={len(build_config.chsel.allowlist_hz)}U",
        "-DAPP_CHSEL_ALLOWLIST_HZ_LIST="
        + ",".join(f"{value}UL" for value in build_config.chsel.allowlist_hz),
        f"-DAPP_CHSEL_BAND_MIN_HZ={build_config.chsel.band_min_hz}UL",
        f"-DAPP_CHSEL_BAND_MAX_HZ={build_config.chsel.band_max_hz}UL",
        f"-DAPP_CHSEL_OUR_HALF_BW_HZ={build_config.chsel.our_half_bw_hz}UL",
        f"-DAPP_CHSEL_GUARD_BAND_HZ={build_config.chsel.guard_band_hz}UL",
        f"-DAPP_CHSEL_EXCLUSION_MASK_COUNT={len(build_config.chsel.exclusion_masks)}U",
        f"-DAPP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS={build_config.chsel.backup_failover_holdoff_ms}U",
    ]
    for index in range(HIGH_ALTITUDE_CC_MAX_EXCLUSION_MASKS):
        if index < len(build_config.chsel.exclusion_masks):
            mask = build_config.chsel.exclusion_masks[index]
        else:
            mask = HighAltitudeCCExclusionMask(center_hz=0, half_bw_hz=0)
        cdefs.extend(
            [
                f"-DAPP_CHSEL_EXCLUSION_MASK{index}_CENTER_HZ={mask.center_hz}UL",
                f"-DAPP_CHSEL_EXCLUSION_MASK{index}_HALF_BW_HZ={mask.half_bw_hz}UL",
            ]
        )
    return cdefs
