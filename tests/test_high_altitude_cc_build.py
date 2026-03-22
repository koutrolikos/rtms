from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.services.high_altitude_cc_build import (
    HighAltitudeCCBuildError,
    patch_app_config_defaults,
)
from shared.schemas import HighAltitudeCCBuildConfig


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
        "#ifndef APP_REPORT_ENABLE\n#define APP_REPORT_ENABLE (APP_MACHINE_LOG_ENABLE)\n#endif\n"
        "#ifndef APP_REPORT_DETAIL\n#define APP_REPORT_DETAIL (APP_MACHINE_LOG_DETAIL)\n#endif\n"
        "#ifndef APP_REPORT_STAT_PERIOD_MS\n#define APP_REPORT_STAT_PERIOD_MS (APP_MACHINE_LOG_STAT_PERIOD_MS)\n#endif\n",
        encoding="utf-8",
    )

    patch_app_config_defaults(
        app_config,
        role_macro="APP_ROLE_MODE_RX",
        build_config=HighAltitudeCCBuildConfig(
            machine_log_detail=1,
            machine_log_stat_period_ms=2000,
        ),
    )

    updated = app_config.read_text(encoding="utf-8")
    assert "#define APP_ROLE_MODE (APP_ROLE_MODE_RX)" in updated
    assert "#define APP_HUMAN_LOG_ENABLE (0)" in updated
    assert "#define APP_MACHINE_LOG_ENABLE (1)" in updated
    assert "#define APP_MACHINE_LOG_DETAIL (1)" in updated
    assert "#define APP_MACHINE_LOG_STAT_PERIOD_MS (2000U)" in updated
    assert "#define APP_REPORT_DETAIL (APP_MACHINE_LOG_DETAIL)" in updated
    assert "#define APP_REPORT_STAT_PERIOD_MS (APP_MACHINE_LOG_STAT_PERIOD_MS)" in updated
