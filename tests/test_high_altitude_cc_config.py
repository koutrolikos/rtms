from __future__ import annotations

from shared.enums import Role
from shared.high_altitude_cc import build_high_altitude_cc_cdefs, parse_high_altitude_cc_build_config


APP_CONFIG_SAMPLE = """
#ifndef APP_DEBUG_ENABLE
#define APP_DEBUG_ENABLE (0)
#endif
#ifndef APP_LOG_LEVEL
#define APP_LOG_LEVEL (3)
#endif
#ifndef APP_CHSEL_ALLOWLIST_COUNT
#define APP_CHSEL_ALLOWLIST_COUNT (2U)
#endif
#ifndef APP_CHSEL_ALLOWLIST_HZ_LIST
#define APP_CHSEL_ALLOWLIST_HZ_LIST 433200000UL,434600000UL
#endif
#ifndef APP_CHSEL_BAND_MIN_HZ
#define APP_CHSEL_BAND_MIN_HZ (433050000UL)
#endif
#ifndef APP_CHSEL_BAND_MAX_HZ
#define APP_CHSEL_BAND_MAX_HZ (434790000UL)
#endif
#ifndef APP_CHSEL_OUR_HALF_BW_HZ
#define APP_CHSEL_OUR_HALF_BW_HZ (108500UL)
#endif
#ifndef APP_CHSEL_GUARD_BAND_HZ
#define APP_CHSEL_GUARD_BAND_HZ (30000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK_COUNT
#define APP_CHSEL_EXCLUSION_MASK_COUNT (2U)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (433920000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (25000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ (434120000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ (30000UL)
#endif
#ifndef APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS
#define APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS (15000U)
#endif
""".strip()


def test_parse_high_altitude_cc_build_config_extracts_session_defaults() -> None:
    build_config = parse_high_altitude_cc_build_config(APP_CONFIG_SAMPLE)

    assert build_config.app_debug_enable == 0
    assert build_config.app_log_level == 3
    assert build_config.chsel.allowlist_hz == [433200000, 434600000]
    assert build_config.chsel.band_min_hz == 433050000
    assert build_config.chsel.band_max_hz == 434790000
    assert build_config.chsel.our_half_bw_hz == 108500
    assert build_config.chsel.guard_band_hz == 30000
    assert build_config.chsel.backup_failover_holdoff_ms == 15000
    assert build_config.chsel.exclusion_masks[0].center_hz == 433920000
    assert build_config.chsel.exclusion_masks[1].half_bw_hz == 30000


def test_build_high_altitude_cc_cdefs_derives_counts_and_zero_fills_masks() -> None:
    build_config = parse_high_altitude_cc_build_config(APP_CONFIG_SAMPLE)

    cdefs = build_high_altitude_cc_cdefs(Role.RX, build_config)

    assert "-DAPP_ROLE_MODE=APP_ROLE_MODE_RX" in cdefs
    assert "-DAPP_DEBUG_ENABLE=0" in cdefs
    assert "-DAPP_LOG_LEVEL=3" in cdefs
    assert "-DAPP_CHSEL_ALLOWLIST_COUNT=2U" in cdefs
    assert "-DAPP_CHSEL_ALLOWLIST_HZ_LIST=433200000UL,434600000UL" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK_COUNT=2U" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK0_CENTER_HZ=433920000UL" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ=30000UL" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK2_CENTER_HZ=0UL" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ=0UL" in cdefs
