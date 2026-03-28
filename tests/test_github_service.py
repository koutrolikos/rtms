from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rtms.server.app.core.config import ServerSettings
from rtms.server.app.services.github import GitHubService


def _run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _create_local_high_altitude_cc_repo(tmp_path: Path) -> tuple[Path, str]:
    repo_root = tmp_path / "High-Altitude-CC"
    app_config = repo_root / "Core" / "Inc" / "app_config.h"
    app_config.parent.mkdir(parents=True)

    _run("git", "init", cwd=repo_root)
    _run("git", "checkout", "-b", "dev", cwd=repo_root)
    _run("git", "config", "user.name", "RTMS Test", cwd=repo_root)
    _run("git", "config", "user.email", "rtms@example.com", cwd=repo_root)

    app_config.write_text("#define APP_DEBUG_ENABLE (0)\n", encoding="utf-8")
    _run("git", "add", "Core/Inc/app_config.h", cwd=repo_root)
    _run("git", "commit", "-m", "initial app config", cwd=repo_root)

    app_config.write_text("#define APP_DEBUG_ENABLE (1)\n", encoding="utf-8")
    _run("git", "add", "Core/Inc/app_config.h", cwd=repo_root)
    _run("git", "commit", "-m", "dev build defaults", cwd=repo_root)

    return repo_root, _run("git", "rev-parse", "HEAD", cwd=repo_root)


def _write_repo_config(tmp_path: Path, repo_root: Path) -> Path:
    config_path = tmp_path / "repos.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "id": "high-altitude-cc",
                    "display_name": "High-Altitude-CC",
                    "full_name": "missing/private-high-altitude-cc",
                    "clone_url": "https://github.com/missing/private-high-altitude-cc.git",
                    "default_branch": "dev",
                    "local_checkout_path": str(repo_root),
                    "build_recipe": {
                        "build_command": "rtms-host build-high-altitude-cc --source . --build-dir build/debug",
                        "artifact_globs": ["build/debug/HighAltitudeCC.elf"],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    return config_path


def test_github_service_browse_commits_uses_local_checkout_for_branch_and_text_queries(tmp_path) -> None:
    repo_root, head_sha = _create_local_high_altitude_cc_repo(tmp_path)
    settings = ServerSettings(repo_config_path=_write_repo_config(tmp_path, repo_root))
    service = GitHubService(settings)

    branch_commits = service.browse_commits("high-altitude-cc", query="dev")
    filtered_commits = service.browse_commits("high-altitude-cc", query="defaults")
    missing_commits = service.browse_commits("high-altitude-cc", query="definitely-not-in-history")

    assert branch_commits
    assert branch_commits[0]["sha"] == head_sha
    assert branch_commits[0]["message"] == "dev build defaults"
    assert filtered_commits
    assert filtered_commits[0]["sha"] == head_sha
    assert missing_commits == []


def test_github_service_fetch_file_at_ref_uses_local_checkout_for_exact_sha(tmp_path) -> None:
    repo_root, head_sha = _create_local_high_altitude_cc_repo(tmp_path)
    settings = ServerSettings(repo_config_path=_write_repo_config(tmp_path, repo_root))
    service = GitHubService(settings)

    source = service.fetch_file_at_ref("high-altitude-cc", "Core/Inc/app_config.h", head_sha)

    assert "#define APP_DEBUG_ENABLE (1)" in source
