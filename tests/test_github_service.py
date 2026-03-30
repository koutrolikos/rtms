from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx

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


def test_github_service_browse_commits_falls_back_to_message_search_when_remote_ref_is_invalid(
    tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "repos.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "id": "high-altitude-cc",
                    "display_name": "High-Altitude-CC",
                    "full_name": "missing/private-high-altitude-cc",
                    "clone_url": "https://github.com/missing/private-high-altitude-cc.git",
                    "default_branch": "main",
                    "build_recipe": {
                        "build_command": "rtms-host build-high-altitude-cc --source . --build-dir build/debug",
                        "artifact_globs": ["build/debug/HighAltitudeCC.elf"],
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    settings = ServerSettings(
        repo_config_path=config_path,
        github_token="test-token",
    )
    service = GitHubService(settings)

    class FakeResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.request = httpx.Request("GET", "https://api.github.test/repos/example/commits")

        def json(self):
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"status {self.status_code}",
                    request=self.request,
                    response=httpx.Response(self.status_code, request=self.request),
                )

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, headers: dict[str, str], params: dict[str, str]):
            if params.get("sha") == "defaults":
                return FakeResponse(422, {"message": "No commit found for SHA: defaults"})
            assert headers["Authorization"] == "Bearer test-token"
            return FakeResponse(
                200,
                [
                    {
                        "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        "commit": {
                            "message": "dev build defaults",
                            "author": {"name": "RTMS Test", "date": "2025-03-01T12:00:00Z"},
                        },
                        "html_url": "https://github.com/example/repo/commit/aaaaaaaa",
                    },
                    {
                        "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "commit": {
                            "message": "initial app config",
                            "author": {"name": "RTMS Test", "date": "2025-02-28T12:00:00Z"},
                        },
                        "html_url": "https://github.com/example/repo/commit/bbbbbbbb",
                    },
                ],
            )

    monkeypatch.setattr("rtms.server.app.services.github.httpx.Client", FakeClient)

    commits = service.browse_commits("high-altitude-cc", query="defaults")

    assert len(commits) == 1
    assert commits[0]["message"] == "dev build defaults"
