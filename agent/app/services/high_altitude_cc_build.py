from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import ValidationError

from shared.high_altitude_cc import HIGH_ALTITUDE_CC_MAX_EXCLUSION_MASKS
from shared.schemas import HighAltitudeCCBuildConfig, HighAltitudeCCExclusionMask


ROLE_MACROS = {
    "rx": "APP_ROLE_MODE_RX",
    "tx": "APP_ROLE_MODE_TX",
    "tx-cw": "APP_ROLE_MODE_TX_CW",
}


class HighAltitudeCCBuildError(RuntimeError):
    pass


def _replace_guarded_default(text: str, macro: str, value: str) -> str:
    pattern = re.compile(
        rf"(?P<prefix>^\s*#ifndef\s+{re.escape(macro)}\s*$\n^\s*#define\s+{re.escape(macro)}\s+)"
        rf"(?P<value>.+?)(?P<suffix>\s*$)",
        re.MULTILINE,
    )
    updated, count = pattern.subn(lambda match: f"{match.group('prefix')}{value}{match.group('suffix')}", text, count=1)
    if count != 1:
        raise HighAltitudeCCBuildError(
            f"could not locate default definition for {macro} in app_config.h"
        )
    return updated


def _macro_value_overrides(role_macro: str, build_config: HighAltitudeCCBuildConfig) -> dict[str, str]:
    replacements = {
        "APP_ROLE_MODE": f"({role_macro})",
        "APP_DEBUG_ENABLE": f"({build_config.app_debug_enable})",
        "APP_LOG_LEVEL": f"({build_config.app_log_level})",
        "APP_CHSEL_ALLOWLIST_COUNT": f"({len(build_config.chsel.allowlist_hz)}U)",
        "APP_CHSEL_ALLOWLIST_HZ_LIST": ",".join(f"{value}UL" for value in build_config.chsel.allowlist_hz),
        "APP_CHSEL_BAND_MIN_HZ": f"({build_config.chsel.band_min_hz}UL)",
        "APP_CHSEL_BAND_MAX_HZ": f"({build_config.chsel.band_max_hz}UL)",
        "APP_CHSEL_OUR_HALF_BW_HZ": f"({build_config.chsel.our_half_bw_hz}UL)",
        "APP_CHSEL_GUARD_BAND_HZ": f"({build_config.chsel.guard_band_hz}UL)",
        "APP_CHSEL_EXCLUSION_MASK_COUNT": f"({len(build_config.chsel.exclusion_masks)}U)",
        "APP_CHSEL_BACKUP_FAILOVER_HOLDOFF_MS": f"({build_config.chsel.backup_failover_holdoff_ms}U)",
    }
    for index in range(HIGH_ALTITUDE_CC_MAX_EXCLUSION_MASKS):
        if index < len(build_config.chsel.exclusion_masks):
            mask = build_config.chsel.exclusion_masks[index]
        else:
            mask = HighAltitudeCCExclusionMask(center_hz=0, half_bw_hz=0)
        replacements[f"APP_CHSEL_EXCLUSION_MASK{index}_CENTER_HZ"] = f"({mask.center_hz}UL)"
        replacements[f"APP_CHSEL_EXCLUSION_MASK{index}_HALF_BW_HZ"] = f"({mask.half_bw_hz}UL)"
    return replacements


def patch_app_config_defaults(
    app_config_path: Path,
    *,
    role_macro: str,
    app_debug_enable: int | None = None,
    build_config: HighAltitudeCCBuildConfig | None = None,
) -> None:
    if build_config is None:
        if app_debug_enable not in {0, 1}:
            raise HighAltitudeCCBuildError("app_debug_enable must be 0 or 1")
        replacements = {
            "APP_ROLE_MODE": f"({role_macro})",
            "APP_DEBUG_ENABLE": f"({app_debug_enable})",
        }
    else:
        replacements = _macro_value_overrides(role_macro, build_config)

    source = app_config_path.read_text(encoding="utf-8")
    updated = source
    for macro, value in replacements.items():
        updated = _replace_guarded_default(updated, macro, value)
    app_config_path.write_text(updated, encoding="utf-8")


def _run_command(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=str(cwd), check=False)
    if completed.returncode != 0:
        quoted = " ".join(command)
        raise HighAltitudeCCBuildError(f"command failed with exit code {completed.returncode}: {quoted}")


def build_high_altitude_cc(
    *,
    source_dir: Path,
    build_dir: Path,
    role: str,
    app_debug_enable: int | None = None,
    build_config: HighAltitudeCCBuildConfig | None = None,
    cmake_bin: str = "cmake",
) -> Path:
    if role not in ROLE_MACROS:
        raise HighAltitudeCCBuildError(f"unsupported role {role!r}")
    if build_config is None and app_debug_enable not in {0, 1}:
        raise HighAltitudeCCBuildError("app_debug_enable must be 0 or 1")

    source_dir = source_dir.resolve()
    build_dir = build_dir.resolve()

    cmake_lists = source_dir / "CMakeLists.txt"
    app_config = source_dir / "Core" / "Inc" / "app_config.h"
    if not cmake_lists.exists():
        raise HighAltitudeCCBuildError(f"missing build input: {cmake_lists}")
    if not app_config.exists():
        raise HighAltitudeCCBuildError(f"missing build input: {app_config}")

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.parent.mkdir(parents=True, exist_ok=True)

    patch_app_config_defaults(
        app_config,
        role_macro=ROLE_MACROS[role],
        app_debug_enable=app_debug_enable,
        build_config=build_config,
    )

    configure_command = [
        cmake_bin,
        "-S",
        str(source_dir),
        "-B",
        str(build_dir),
        "-DCMAKE_SYSTEM_NAME=Generic",
        "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY",
        "-DCMAKE_C_COMPILER=arm-none-eabi-gcc",
        "-DCMAKE_ASM_COMPILER=arm-none-eabi-gcc",
    ]
    build_command = [cmake_bin, "--build", str(build_dir), "--parallel"]

    effective_app_debug = build_config.app_debug_enable if build_config is not None else app_debug_enable
    print(f"building High-Altitude-CC role={role} app_debug_enable={effective_app_debug}")
    _run_command(configure_command, cwd=source_dir)
    _run_command(build_command, cwd=source_dir)

    elf_path = build_dir / "HighAltitudeCC.elf"
    if not elf_path.exists():
        raise HighAltitudeCCBuildError(f"expected build output missing: {elf_path}")
    print(str(elf_path))
    return elf_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build High-Altitude-CC from a clean checkout")
    parser.add_argument("--source", default=".")
    parser.add_argument("--build-dir", default="build/debug")
    parser.add_argument("--role", choices=sorted(ROLE_MACROS), required=True)
    parser.add_argument("--app-debug", type=int, choices=[0, 1], default=1)
    parser.add_argument("--build-config-json")
    parser.add_argument("--cmake-bin", default="cmake")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        build_config = (
            HighAltitudeCCBuildConfig.model_validate_json(args.build_config_json)
            if args.build_config_json
            else None
        )
    except ValidationError as exc:
        print(f"invalid build config json: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"invalid build config json: {exc}", file=sys.stderr)
        return 1
    try:
        build_high_altitude_cc(
            source_dir=Path(args.source),
            build_dir=Path(args.build_dir),
            role=args.role,
            app_debug_enable=args.app_debug,
            build_config=build_config,
            cmake_bin=args.cmake_bin,
        )
    except HighAltitudeCCBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
