from __future__ import annotations

from pathlib import Path

import pytest

from rtms.host.app.services.high_altitude_cc_build import (
    HighAltitudeCCBuildError,
    build_high_altitude_cc,
    patch_app_config_defaults,
)
from rtms.shared.schemas import HighAltitudeCCBuildConfig


def _full_build_config() -> HighAltitudeCCBuildConfig:
    return HighAltitudeCCBuildConfig(
        machine_log_detail=0,
        machine_log_stat_period_ms=2000,
        rf_bitrate_bps=76760,
        rf_rx_bw_hz=203102,
        rf_deviation_hz=31735,
        rf_preamble_bytes=4,
        rf_sync_word=0xD391,
        rf_pa_table=[0xC2] * 8,
        allowlist_hz=[433200000, 434600000],
        band_min_hz=433050000,
        band_max_hz=434790000,
        guard_band_hz=30000,
        exclusion_masks=[{"center_hz": 433500000, "half_bw_hz": 25000}],
        backup_failover_holdoff_ms=15000,
        rx_thresh_enable=1,
        rx_min_rssi_dbm=-95,
        rx_min_lqi=40,
        rx_thresh_log_every=1000,
        rx_poll_interval_ms=2,
        tx_complete_timeout_ms=20,
        rx_host_bridge_budget=32,
        telem_gps_period_ms=200,
        telem_imu_baro_period_ms=50,
        airtime_limit_us_per_hour=306000000,
    )


def test_patch_app_config_defaults_updates_role_and_debug(tmp_path: Path) -> None:
    app_config = tmp_path / "app_config.h"
    app_config.write_text(
        "#ifndef APP_ROLE_MODE\n"
        "#define APP_ROLE_MODE (APP_ROLE_MODE_TX_CW)\n"
        "#endif\n\n"
        "#ifndef APP_DEBUG_ENABLE\n"
        "#define APP_DEBUG_ENABLE (1)\n"
        "#endif\n",
        encoding="utf-8",
    )

    patch_app_config_defaults(
        app_config,
        role_macro="APP_ROLE_MODE_RX",
        app_debug_enable=0,
    )

    updated = app_config.read_text(encoding="utf-8")
    assert "#define APP_ROLE_MODE (APP_ROLE_MODE_RX)" in updated
    assert "#define APP_DEBUG_ENABLE (0)" in updated


def test_patch_app_config_defaults_raises_when_expected_block_missing(tmp_path: Path) -> None:
    app_config = tmp_path / "app_config.h"
    app_config.write_text("#define SOMETHING_ELSE 1\n", encoding="utf-8")

    with pytest.raises(HighAltitudeCCBuildError):
        patch_app_config_defaults(
            app_config,
            role_macro="APP_ROLE_MODE_TX",
            app_debug_enable=1,
        )


def test_patch_app_config_defaults_updates_full_build_config(tmp_path: Path) -> None:
    app_config = tmp_path / "app_config.h"
    app_config.write_text(
        "#ifndef APP_ROLE_MODE\n#define APP_ROLE_MODE (APP_ROLE_MODE_TX)\n#endif\n"
        "#ifndef APP_HUMAN_LOG_ENABLE\n"
        "#ifdef APP_DEBUG_ENABLE\n"
        "#define APP_HUMAN_LOG_ENABLE (APP_DEBUG_ENABLE)\n"
        "#else\n"
        "#define APP_HUMAN_LOG_ENABLE (1)\n"
        "#endif\n"
        "#endif\n"
        "#ifndef APP_MACHINE_LOG_ENABLE\n"
        "#ifdef APP_REPORT_ENABLE\n"
        "#define APP_MACHINE_LOG_ENABLE (APP_REPORT_ENABLE)\n"
        "#else\n"
        "#define APP_MACHINE_LOG_ENABLE (APP_HUMAN_LOG_ENABLE)\n"
        "#endif\n"
        "#endif\n"
        "#ifndef APP_MACHINE_LOG_DETAIL_SUMMARY\n#define APP_MACHINE_LOG_DETAIL_SUMMARY (0)\n#endif\n"
        "#ifndef APP_MACHINE_LOG_DETAIL_PACKET\n#define APP_MACHINE_LOG_DETAIL_PACKET (1)\n#endif\n"
        "#ifndef APP_MACHINE_LOG_DETAIL\n"
        "#ifdef APP_REPORT_DETAIL\n"
        "#define APP_MACHINE_LOG_DETAIL (APP_REPORT_DETAIL)\n"
        "#else\n"
        "#define APP_MACHINE_LOG_DETAIL (APP_MACHINE_LOG_DETAIL_SUMMARY)\n"
        "#endif\n"
        "#endif\n"
        "#ifndef APP_MACHINE_LOG_STAT_PERIOD_MS\n"
        "#ifdef APP_REPORT_STAT_PERIOD_MS\n"
        "#define APP_MACHINE_LOG_STAT_PERIOD_MS (APP_REPORT_STAT_PERIOD_MS)\n"
        "#else\n"
        "#define APP_MACHINE_LOG_STAT_PERIOD_MS (5000U)\n"
        "#endif\n"
        "#endif\n"
        "#ifndef APP_RX_THRESH_ENABLE\n#define APP_RX_THRESH_ENABLE (1)\n#endif\n"
        "#ifndef APP_RX_MIN_RSSI_DBM\n#define APP_RX_MIN_RSSI_DBM (-95)\n#endif\n"
        "#ifndef APP_RX_MIN_LQI\n#define APP_RX_MIN_LQI (40U)\n#endif\n"
        "#ifndef APP_RX_THRESH_LOG_EVERY\n#define APP_RX_THRESH_LOG_EVERY (1000U)\n#endif\n"
        "#ifndef APP_RX_POLL_INTERVAL_MS\n#define APP_RX_POLL_INTERVAL_MS (2U)\n#endif\n"
        "#ifndef APP_TX_COMPLETE_TIMEOUT_MS\n#define APP_TX_COMPLETE_TIMEOUT_MS (20U)\n#endif\n"
        "#ifndef APP_RX_HOST_BRIDGE_BUDGET\n#define APP_RX_HOST_BRIDGE_BUDGET (32U)\n#endif\n"
        "#ifndef APP_TELEM_GPS_PERIOD_MS\n#define APP_TELEM_GPS_PERIOD_MS (200U)\n#endif\n"
        "#ifndef APP_TELEM_IMU_BARO_PERIOD_MS\n#define APP_TELEM_IMU_BARO_PERIOD_MS (50U)\n#endif\n"
        "#ifndef APP_RF_BITRATE_BPS\n#define APP_RF_BITRATE_BPS (76760UL)\n#endif\n"
        "#ifndef APP_RF_RX_BW_HZ\n#define APP_RF_RX_BW_HZ (203102UL)\n#endif\n"
        "#ifndef APP_RF_DEVIATION_HZ\n#define APP_RF_DEVIATION_HZ (31735UL)\n#endif\n"
        "#ifndef APP_RF_PREAMBLE_BYTES\n#define APP_RF_PREAMBLE_BYTES (4U)\n#endif\n"
        "#ifndef APP_RF_SYNC_WORD\n#define APP_RF_SYNC_WORD (0xD391UL)\n#endif\n"
        "#ifndef APP_RF_PA_TABLE_LIST\n#define APP_RF_PA_TABLE_LIST 0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U\n#endif\n"
        "#ifndef APP_AIRTIME_LIMIT_US_PER_HOUR\n#define APP_AIRTIME_LIMIT_US_PER_HOUR (306000000UL)\n#endif\n"
        "#ifndef APP_CHSEL_ALLOWLIST_COUNT\n#define APP_CHSEL_ALLOWLIST_COUNT (2U)\n#endif\n"
        "#ifndef APP_CHSEL_ALLOWLIST_HZ_LIST\n#define APP_CHSEL_ALLOWLIST_HZ_LIST 433200000UL,434600000UL\n#endif\n"
        "#ifndef APP_CHSEL_BAND_MIN_HZ\n#define APP_CHSEL_BAND_MIN_HZ (433050000UL)\n#endif\n"
        "#ifndef APP_CHSEL_BAND_MAX_HZ\n#define APP_CHSEL_BAND_MAX_HZ (434790000UL)\n#endif\n"
        "#ifndef APP_CHSEL_GUARD_BAND_HZ\n#define APP_CHSEL_GUARD_BAND_HZ (30000UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK_COUNT\n#define APP_CHSEL_EXCLUSION_MASK_COUNT (0U)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK2_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK2_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK2_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK2_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK3_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK3_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS\n#define APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS (15000U)\n#endif\n"
        "#ifndef APP_REPORT_ENABLE\n#define APP_REPORT_ENABLE (APP_MACHINE_LOG_ENABLE)\n#endif\n"
        "#ifndef APP_REPORT_DETAIL\n#define APP_REPORT_DETAIL (APP_MACHINE_LOG_DETAIL)\n#endif\n"
        "#ifndef APP_REPORT_STAT_PERIOD_MS\n#define APP_REPORT_STAT_PERIOD_MS (APP_MACHINE_LOG_STAT_PERIOD_MS)\n#endif\n",
        encoding="utf-8",
    )

    patch_app_config_defaults(
        app_config,
        role_macro="APP_ROLE_MODE_RX",
        build_config=_full_build_config(),
    )

    updated = app_config.read_text(encoding="utf-8")
    assert "#define APP_ROLE_MODE (APP_ROLE_MODE_RX)" in updated
    assert "#define APP_HUMAN_LOG_ENABLE (0)" in updated
    assert "#define APP_MACHINE_LOG_ENABLE (1)" in updated
    assert "#define APP_MACHINE_LOG_DETAIL (1)" in updated
    assert "#define APP_MACHINE_LOG_STAT_PERIOD_MS (2000U)" in updated
    assert "#define APP_RF_SYNC_WORD (0xD391UL)" in updated
    assert "#define APP_RF_PA_TABLE_LIST 0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U,0xC2U" in updated
    assert "#define APP_CHSEL_ALLOWLIST_COUNT (2U)" in updated
    assert "#define APP_CHSEL_ALLOWLIST_HZ_LIST 433200000UL,434600000UL" in updated
    assert "#define APP_CHSEL_EXCLUSION_MASK_COUNT (1U)" in updated
    assert "#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (433500000UL)" in updated
    assert "#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (25000UL)" in updated
    assert "#define APP_RX_MIN_RSSI_DBM (-95)" in updated
    assert "#define APP_TELEM_GPS_PERIOD_MS (200U)" in updated
    assert "#define APP_REPORT_DETAIL (APP_MACHINE_LOG_DETAIL)" in updated
    assert "#define APP_REPORT_STAT_PERIOD_MS (APP_MACHINE_LOG_STAT_PERIOD_MS)" in updated


def test_build_high_altitude_cc_uses_cmake_configure_build_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_dir = tmp_path / "firmware"
    app_config = source_dir / "Core" / "Inc" / "app_config.h"
    app_config.parent.mkdir(parents=True)
    app_config.write_text(
        "#ifndef APP_ROLE_MODE\n"
        "#define APP_ROLE_MODE (APP_ROLE_MODE_TX)\n"
        "#endif\n"
        "#ifndef APP_DEBUG_ENABLE\n"
        "#define APP_DEBUG_ENABLE (1)\n"
        "#endif\n",
        encoding="utf-8",
    )
    (source_dir / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    build_dir = source_dir / "build" / "debug"
    commands: list[tuple[list[str], Path]] = []

    def fake_run_command(command: list[str], *, cwd: Path) -> None:
        commands.append((command, cwd))
        if command[:3] == ["cmake", "--build", str(build_dir)]:
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "High-Altitude-CC.elf").write_text("elf", encoding="utf-8")

    monkeypatch.setattr(
        "rtms.host.app.services.high_altitude_cc_build._run_command",
        fake_run_command,
    )

    elf_path = build_high_altitude_cc(
        source_dir=source_dir,
        build_dir=build_dir,
        role="tx",
        app_debug_enable=1,
    )

    assert elf_path == build_dir / "High-Altitude-CC.elf"
    assert commands == [
        (
            ["cmake", "-S", str(source_dir), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Debug"],
            source_dir,
        ),
        (
            ["cmake", "--build", str(build_dir), "--config", "Debug", "--parallel"],
            source_dir,
        ),
    ]
