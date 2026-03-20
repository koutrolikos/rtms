from pathlib import Path

from agent.app.core.config import AgentSettings
from agent.app.executors.build import BuildExecutor


def test_clone_url_uses_github_token_for_https_private_repo() -> None:
    executor = BuildExecutor(AgentSettings(github_token="secret-token"))
    authed = executor._clone_url_with_auth("https://github.com/koutrolikos/High-Altitude-CC.git")
    assert "x-access-token:secret-token@" in authed
    redacted = executor._redact_clone_url(authed)
    assert "secret-token" not in redacted
    assert "x-access-token:***@" in redacted


def test_clone_url_keeps_plain_url_when_no_token() -> None:
    executor = BuildExecutor(AgentSettings())
    clone_url = "https://github.com/koutrolikos/High-Altitude-CC.git"
    assert executor._clone_url_with_auth(clone_url) == clone_url


def test_prepare_repo_root_archives_stale_non_git_workspace(tmp_path: Path) -> None:
    executor = BuildExecutor(AgentSettings())
    repo_root = tmp_path / "agent_data" / "repos" / "high-altitude-cc-rx-debug"
    repo_root.mkdir(parents=True)
    (repo_root / "partial-file.txt").write_text("partial clone", encoding="utf-8")

    archived = executor._prepare_repo_root(repo_root)

    assert archived is not None
    assert archived.exists()
    assert (archived / "partial-file.txt").exists()
    assert not repo_root.exists()


def test_prepare_repo_root_keeps_existing_git_workspace(tmp_path: Path) -> None:
    executor = BuildExecutor(AgentSettings())
    repo_root = tmp_path / "agent_data" / "repos" / "high-altitude-cc-rx-debug"
    (repo_root / ".git").mkdir(parents=True)

    archived = executor._prepare_repo_root(repo_root)

    assert archived is None
    assert repo_root.exists()


def test_checkout_uses_default_branch_before_requested_ref(tmp_path: Path) -> None:
    executor = BuildExecutor(AgentSettings(github_token="secret-token"))
    repo_root = tmp_path / "agent_data" / "repos" / "high-altitude-cc-rx-debug"
    commands: list[tuple[str, str | None]] = []

    def fake_run_command(
        command: str,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 900,
        log_path: Path | None = None,
        display_command: str | None = None,
    ) -> None:
        del env, timeout_seconds, log_path, display_command
        commands.append((command, str(cwd) if cwd else None))
        if " clone " in command:
            (repo_root / ".git").mkdir(parents=True, exist_ok=True)

    executor._run_command = fake_run_command  # type: ignore[method-assign]

    executor._checkout(
        repo_root,
        "https://github.com/koutrolikos/High-Altitude-CC.git",
        "deadbeefcafebabe",
        default_branch="dev",
    )

    assert commands[0][0].startswith("git clone --branch dev ")
    assert commands[1][0].startswith("git remote set-url origin https://x-access-token:secret-token@")
    assert commands[2][0] == "git fetch --all --tags"
    assert commands[3][0] == "git fetch origin dev --tags"
    assert commands[4][0] == "git checkout dev"
    assert commands[5][0] == "git reset --hard origin/dev"
    assert commands[6][0] == "git clean -fdx"
    assert commands[7][0] == "git checkout deadbeefcafebabe"
    assert commands[8][0] == "git remote set-url origin https://github.com/koutrolikos/High-Altitude-CC.git"
