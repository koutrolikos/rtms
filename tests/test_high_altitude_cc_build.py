from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.services.high_altitude_cc_build import (
    HighAltitudeCCBuildError,
    patch_app_config_defaults,
)
from shared.schemas import (
    HighAltitudeCCBuildConfig,
    HighAltitudeCCChannelSelectionConfig,
    HighAltitudeCCExclusionMask,
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
        "#ifndef APP_DEBUG_ENABLE\n#define APP_DEBUG_ENABLE (1)\n#endif\n"
        "#ifndef APP_LOG_LEVEL\n#define APP_LOG_LEVEL (4)\n#endif\n"
        "#ifndef APP_CHSEL_ALLOWLIST_COUNT\n#define APP_CHSEL_ALLOWLIST_COUNT (2U)\n#endif\n"
        "#ifndef APP_CHSEL_ALLOWLIST_HZ_LIST\n#define APP_CHSEL_ALLOWLIST_HZ_LIST 433200000UL,434600000UL\n#endif\n"
        "#ifndef APP_CHSEL_BAND_MIN_HZ\n#define APP_CHSEL_BAND_MIN_HZ (433050000UL)\n#endif\n"
        "#ifndef APP_CHSEL_BAND_MAX_HZ\n#define APP_CHSEL_BAND_MAX_HZ (434790000UL)\n#endif\n"
        "#ifndef APP_CHSEL_OUR_HALF_BW_HZ\n#define APP_CHSEL_OUR_HALF_BW_HZ (108500UL)\n#endif\n"
        "#ifndef APP_CHSEL_GUARD_BAND_HZ\n#define APP_CHSEL_GUARD_BAND_HZ (30000UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK_COUNT\n#define APP_CHSEL_EXCLUSION_MASK_COUNT (0U)\n#endif\n"
        "#ifndef APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS\n#define APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS (15000U)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK1_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK1_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK2_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK2_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK2_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK2_HALF_BW_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK3_CENTER_HZ\n#define APP_CHSEL_EXCLUSION_MASK3_CENTER_HZ (0UL)\n#endif\n"
        "#ifndef APP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ\n#define APP_CHSEL_EXCLUSION_MASK3_HALF_BW_HZ (0UL)\n#endif\n",
        encoding="utf-8",
    )

    patch_app_config_defaults(
        app_config,
        role_macro="APP_ROLE_MODE_RX",
        build_config=HighAltitudeCCBuildConfig(
            app_debug_enable=0,
            app_log_level=2,
            chsel=HighAltitudeCCChannelSelectionConfig(
                allowlist_hz=[433200000, 434600000],
                band_min_hz=433050000,
                band_max_hz=434790000,
                our_half_bw_hz=108500,
                guard_band_hz=30000,
                exclusion_masks=[HighAltitudeCCExclusionMask(center_hz=433920000, half_bw_hz=25000)],
                backup_failover_holdoff_ms=15000,
            ),
        ),
    )

    updated = app_config.read_text(encoding="utf-8")
    assert "#define APP_ROLE_MODE (APP_ROLE_MODE_RX)" in updated
    assert "#define APP_DEBUG_ENABLE (0)" in updated
    assert "#define APP_LOG_LEVEL (2)" in updated
    assert "#define APP_CHSEL_ALLOWLIST_COUNT (2U)" in updated
    assert "#define APP_CHSEL_ALLOWLIST_HZ_LIST 433200000UL,434600000UL" in updated
    assert "#define APP_CHSEL_EXCLUSION_MASK_COUNT (1U)" in updated
    assert "#define APP_CHSEL_EXCLUSION_MASK0_CENTER_HZ (433920000UL)" in updated
    assert "#define APP_CHSEL_EXCLUSION_MASK0_HALF_BW_HZ (25000UL)" in updated
