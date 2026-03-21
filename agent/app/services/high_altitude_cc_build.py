from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROLE_MACROS = {
    "rx": "APP_ROLE_MODE_RX",
    "tx": "APP_ROLE_MODE_TX",
    "tx-cw": "APP_ROLE_MODE_TX_CW",
}


class HighAltitudeCCBuildError(RuntimeError):
    pass


def _replace_guarded_default(text: str, macro: str, value: str) -> str:
    pattern = re.compile(
        rf"(?P<prefix>#ifndef {re.escape(macro)}\s+#define {re.escape(macro)} \()(?P<value>[^)]+)(?P<suffix>\))",
        re.MULTILINE,
    )
    updated, count = pattern.subn(rf"\g<prefix>{value}\g<suffix>", text, count=1)
    if count != 1:
        raise HighAltitudeCCBuildError(
            f"could not locate default definition for {macro} in app_config.h"
        )
    return updated


def patch_app_config_defaults(
    app_config_path: Path,
    *,
    role_macro: str,
    app_debug_enable: int,
) -> None:
    source = app_config_path.read_text(encoding="utf-8")
    updated = _replace_guarded_default(source, "APP_ROLE_MODE", role_macro)
    updated = _replace_guarded_default(updated, "APP_DEBUG_ENABLE", str(app_debug_enable))
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
    app_debug_enable: int,
    cmake_bin: str = "cmake",
) -> Path:
    if role not in ROLE_MACROS:
        raise HighAltitudeCCBuildError(f"unsupported role {role!r}")
    if app_debug_enable not in {0, 1}:
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

    print(f"building High-Altitude-CC role={role} app_debug_enable={app_debug_enable}")
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
    parser.add_argument("--cmake-bin", default="cmake")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        build_high_altitude_cc(
            source_dir=Path(args.source),
            build_dir=Path(args.build_dir),
            role=args.role,
            app_debug_enable=args.app_debug,
            cmake_bin=args.cmake_bin,
        )
    except HighAltitudeCCBuildError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
