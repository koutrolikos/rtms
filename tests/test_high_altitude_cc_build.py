from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.services.high_altitude_cc_build import (
    HighAltitudeCCBuildError,
    patch_app_config_defaults,
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
