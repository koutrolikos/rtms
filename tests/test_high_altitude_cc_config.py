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
""".strip()


def test_parse_high_altitude_cc_build_config_extracts_machine_log_defaults() -> None:
    build_config = parse_high_altitude_cc_build_config(APP_CONFIG_SAMPLE)

    assert build_config.machine_log_detail == 0
    assert build_config.machine_log_stat_period_ms == 2500


def test_build_high_altitude_cc_cdefs_override_only_role_and_machine_logging() -> None:
    build_config = parse_high_altitude_cc_build_config(APP_CONFIG_SAMPLE)

    cdefs = build_high_altitude_cc_cdefs(Role.RX, build_config)

    assert "-DAPP_ROLE_MODE=APP_ROLE_MODE_RX" in cdefs
    assert "-DAPP_HUMAN_LOG_ENABLE=0" in cdefs
    assert "-DAPP_MACHINE_LOG_ENABLE=1" in cdefs
    assert "-DAPP_MACHINE_LOG_DETAIL=0" in cdefs
    assert "-DAPP_MACHINE_LOG_STAT_PERIOD_MS=2500U" in cdefs
