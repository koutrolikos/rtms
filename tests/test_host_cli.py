from __future__ import annotations

from rtms.host.app.cli.main import build_parser


def test_build_high_altitude_cc_subcommand_parses() -> None:
    args = build_parser().parse_args(
        [
            "build-high-altitude-cc",
            "--source",
            ".",
            "--build-dir",
            "build/debug",
            "--role",
            "tx",
            "--app-debug",
            "1",
        ]
    )

    assert args.command == "build-high-altitude-cc"
    assert args.source == "."
    assert args.build_dir == "build/debug"
    assert args.role == "tx"
    assert args.app_debug == 1
