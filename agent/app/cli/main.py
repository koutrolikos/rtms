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
    finally:
        runtime.close()

