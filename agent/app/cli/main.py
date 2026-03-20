from __future__ import annotations

import argparse

from agent.app.core.config import get_settings
from agent.app.core.logging import configure_logging
from agent.app.services.runtime import AgentRuntime
from shared.enums import Role


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RF range-test agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Run the polling agent")

    build_parser = subparsers.add_parser("build-local-upload", help="Build locally and upload a session artifact")
    build_parser.add_argument("--session-id", required=True)
    build_parser.add_argument("--repo-id", required=True)
    build_parser.add_argument("--git-sha", required=True)
    build_parser.add_argument("--role", choices=[Role.TX.value, Role.RX.value], required=False)

    upload_parser = subparsers.add_parser(
        "upload-prebuilt",
        help="Upload an existing ELF as a session-scoped manual artifact",
    )
    upload_parser.add_argument("--session-id", required=True)
    upload_parser.add_argument("--role", choices=[Role.TX.value, Role.RX.value], required=True)
    upload_parser.add_argument("--elf-path", required=True)
    upload_parser.add_argument("--git-sha", required=False)
    upload_parser.add_argument("--source-repo", required=False)
    upload_parser.add_argument("--rtt-symbol", default="_SEGGER_RTT")
    upload_parser.add_argument("--dirty-worktree", action="store_true")
    return parser


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    runtime = AgentRuntime(get_settings())
    try:
        if args.command == "run":
            runtime.run()
        elif args.command == "build-local-upload":
            runtime.build_local_upload(
                session_id=args.session_id,
                repo_id=args.repo_id,
                git_sha=args.git_sha,
                role=Role(args.role) if args.role else None,
            )
        elif args.command == "upload-prebuilt":
            artifact_id = runtime.upload_prebuilt_artifact(
                session_id=args.session_id,
                role=Role(args.role),
                elf_path=args.elf_path,
                git_sha=args.git_sha,
                source_repo=args.source_repo,
                rtt_symbol=args.rtt_symbol,
                dirty_worktree=args.dirty_worktree,
            )
            print(artifact_id)
    finally:
        runtime.close()
