from __future__ import annotations

from rtms.shared.enums import Role
from rtms.shared.high_altitude_cc import build_high_altitude_cc_cdefs, parse_high_altitude_cc_build_config


APP_CONFIG_SAMPLE = """
#ifndef APP_HUMAN_LOG_ENABLE
#ifdef APP_DEBUG_ENABLE
#define APP_HUMAN_LOG_ENABLE (APP_DEBUG_ENABLE)
#else
#define APP_HUMAN_LOG_ENABLE (1)
#endif
#endif

#ifndef APP_MACHINE_LOG_DETAIL_SUMMARY
#define APP_MACHINE_LOG_DETAIL_SUMMARY (0)
#endif

#ifndef APP_MACHINE_LOG_DETAIL_PACKET
#define APP_MACHINE_LOG_DETAIL_PACKET (1)
#endif

#ifndef APP_MACHINE_LOG_ENABLE
#ifdef APP_REPORT_ENABLE
#define APP_MACHINE_LOG_ENABLE (APP_REPORT_ENABLE)
#else
#define APP_MACHINE_LOG_ENABLE (APP_HUMAN_LOG_ENABLE)
#endif
#endif

#ifndef APP_MACHINE_LOG_DETAIL
#ifdef APP_REPORT_DETAIL
#define APP_MACHINE_LOG_DETAIL (APP_REPORT_DETAIL)
#else
#define APP_MACHINE_LOG_DETAIL (APP_MACHINE_LOG_DETAIL_SUMMARY)
#endif
#endif

#ifndef APP_MACHINE_LOG_STAT_PERIOD_MS
#ifdef APP_REPORT_STAT_PERIOD_MS
#define APP_MACHINE_LOG_STAT_PERIOD_MS (APP_REPORT_STAT_PERIOD_MS)
#else
#define APP_MACHINE_LOG_STAT_PERIOD_MS (2500U)
#endif
#endif
#ifndef APP_RX_THRESH_ENABLE
#define APP_RX_THRESH_ENABLE (1)
#endif
#ifndef APP_RX_MIN_RSSI_DBM
#define APP_RX_MIN_RSSI_DBM (-95)
#endif
#ifndef APP_RX_MIN_LQI
#define APP_RX_MIN_LQI (40U)
#endif
#ifndef APP_RX_THRESH_LOG_EVERY
#define APP_RX_THRESH_LOG_EVERY (1000U)
#endif
#ifndef APP_RX_POLL_INTERVAL_MS
#define APP_RX_POLL_INTERVAL_MS (2U)
#endif
#ifndef APP_TX_COMPLETE_TIMEOUT_MS
#define APP_TX_COMPLETE_TIMEOUT_MS (20U)
#endif
#ifndef APP_RX_HOST_BRIDGE_BUDGET
#define APP_RX_HOST_BRIDGE_BUDGET (32U)
#endif
#ifndef APP_TELEM_GPS_PERIOD_MS
#define APP_TELEM_GPS_PERIOD_MS (200U)
#endif
#ifndef APP_TELEM_IMU_BARO_PERIOD_MS
#define APP_TELEM_IMU_BARO_PERIOD_MS (50U)
#endif
#ifndef APP_RF_BITRATE_BPS
#define APP_RF_BITRATE_BPS (76760UL)
#endif
#ifndef APP_RF_RX_BW_HZ
#define APP_RF_RX_BW_HZ (203102UL)
#endif
#ifndef APP_RF_DEVIATION_HZ
#define APP_RF_DEVIATION_HZ (31735UL)
#endif
#ifndef APP_RF_PREAMBLE_BYTES
#define APP_RF_PREAMBLE_BYTES (4U)
#endif
#ifndef APP_RF_SYNC_WORD
#define APP_RF_SYNC_WORD (0xD391UL)
#endif
#ifndef APP_RF_PA_TABLE_LIST
#define APP_RF_PA_TABLE_LIST 0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U
#endif
#ifndef APP_AIRTIME_LIMIT_US_PER_HOUR
#define APP_AIRTIME_LIMIT_US_PER_HOUR (306000000UL)
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
#ifndef APP_CHSEL_GUARD_BAND_HZ
#define APP_CHSEL_GUARD_BAND_HZ (30000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK_COUNT
#define APP_CHSEL_EXCLUSION_MASK_COUNT (1U)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (433500000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (25000UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ (0UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ (0UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK2_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK2_CENTER_HZ (0UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK2_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK2_HALF_BW_HZ (0UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK3_CENTER_HZ
#define APP_CHSEL_EXCLUSION_MASK3_CENTER_HZ (0UL)
#endif
#ifndef APP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ
#define APP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ (0UL)
#endif
#ifndef APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS
#define APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS (15000U)
#endif
""".strip()


def test_parse_high_altitude_cc_build_config_extracts_machine_log_defaults() -> None:
    build_config = parse_high_altitude_cc_build_config(APP_CONFIG_SAMPLE)

    assert build_config.machine_log_detail == 0
    assert build_config.machine_log_stat_period_ms == 2500
    assert build_config.rf_bitrate_bps == 76760
    assert build_config.rf_sync_word == 0xD391
    assert build_config.rf_pa_table == [0xC2] * 8
    assert build_config.allowlist_hz == [433200000, 434600000]
    assert [mask.model_dump(mode="json") for mask in build_config.exclusion_masks] == [
        {"center_hz": 433500000, "half_bw_hz": 25000}
    ]
    assert build_config.rx_min_rssi_dbm == -95
    assert build_config.airtime_limit_us_per_hour == 306000000


def test_build_high_altitude_cc_cdefs_override_only_role_and_machine_logging() -> None:
    build_config = parse_high_altitude_cc_build_config(APP_CONFIG_SAMPLE)

    cdefs = build_high_altitude_cc_cdefs(Role.RX, build_config)

    assert "-DAPP_ROLE_MODE=APP_ROLE_MODE_RX" in cdefs
    assert "-DAPP_HUMAN_LOG_ENABLE=0" in cdefs
    assert "-DAPP_MACHINE_LOG_ENABLE=1" in cdefs
    assert "-DAPP_MACHINE_LOG_DETAIL=1" in cdefs
    assert "-DAPP_MACHINE_LOG_STAT_PERIOD_MS=2500U" in cdefs
    assert "-DAPP_RF_SYNC_WORD=0xD391UL" in cdefs
    assert "-DAPP_CHSEL_ALLOWLIST_HZ_LIST=433200000UL,434600000UL" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK0_CENTER_HZ=433500000UL" in cdefs
