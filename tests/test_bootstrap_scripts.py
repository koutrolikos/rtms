from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LINUX_BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap_agent_linux.sh"
MACOS_BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap_agent_macos.sh"
RUN_AGENT = REPO_ROOT / "scripts" / "run_agent.sh"
RUN_SERVER = REPO_ROOT / "scripts" / "run_server.sh"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_script(
    script: Path,
    args: list[str],
    *,
    cwd: Path,
    home: Path,
    path_prefix: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["SHELL"] = "/bin/zsh"
    env["PATH"] = (
        f"{path_prefix}:{SYSTEM_PATH}"
        if path_prefix is not None
        else SYSTEM_PATH
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _install_fake_linux_commands(bin_dir: Path, log_dir: Path) -> None:
    _write_executable(
        bin_dir / "sudo",
        """#!/usr/bin/env bash
set -euo pipefail
exec "$@"
""",
    )
    _write_executable(
        bin_dir / "apt-get",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/apt-get.log"
""",
    )
    _write_executable(
        bin_dir / "git",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/git.log"
if [[ "${1:-}" == "clone" ]]; then
  mkdir -p "$3/.git"
fi
""",
    )
    _write_executable(
        bin_dir / "python3",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  dest="$3"
  mkdir -p "$dest/bin"
  cat > "$dest/bin/pip" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/pip.log"
EOF
  chmod +x "$dest/bin/pip"
  exit 0
fi
echo "unexpected python3 invocation: $*" >&2
exit 1
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/curl.log"
""",
    )
    log_dir.mkdir(parents=True, exist_ok=True)


def _install_fake_macos_commands(bin_dir: Path, log_dir: Path) -> None:
    _write_executable(
        bin_dir / "xcode-select",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-p" ]]; then
  printf '%s\\n' "/Library/Developer/CommandLineTools"
fi
""",
    )
    _write_executable(
        bin_dir / "brew",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/brew.log"
""",
    )
    _write_executable(
        bin_dir / "git",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/git.log"
if [[ "${1:-}" == "clone" ]]; then
  mkdir -p "$3/.git"
fi
""",
    )
    _write_executable(
        bin_dir / "python3.11",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  dest="$3"
  mkdir -p "$dest/bin"
  cat > "$dest/bin/pip" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/pip.log"
EOF
  chmod +x "$dest/bin/pip"
  exit 0
fi
echo "unexpected python3.11 invocation: $*" >&2
exit 1
""",
    )
    _write_executable(
        bin_dir / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$RTMS_TEST_LOG_DIR/curl.log"
""",
    )
    log_dir.mkdir(parents=True, exist_ok=True)


def test_bootstrap_scripts_reject_missing_option_values(tmp_path: Path) -> None:
    for script in (LINUX_BOOTSTRAP, MACOS_BOOTSTRAP):
        result = _run_script(
            script,
            ["--server-url", "http://example.com:8000", "--install-dir"],
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert result.returncode != 0
        assert "missing value for --install-dir" in result.stderr


def test_bootstrap_scripts_reject_unroutable_server_url(tmp_path: Path) -> None:
    for script in (LINUX_BOOTSTRAP, MACOS_BOOTSTRAP):
        result = _run_script(
            script,
            ["--server-url", "http://0.0.0.0:8000"],
            cwd=tmp_path,
            home=tmp_path / "home",
        )
        assert result.returncode != 0
        assert "--server-url cannot use 0.0.0.0" in result.stderr


def test_linux_bootstrap_supports_equals_syntax_and_relative_install_dir(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "workspace"
    bin_dir.mkdir()
    home_dir.mkdir()
    work_dir.mkdir()
    _install_fake_linux_commands(bin_dir, log_dir)

    result = _run_script(
        LINUX_BOOTSTRAP,
        [
            "--server-url=http://example.com:8000/",
            "--install-dir=relative/agent",
            "--mode=build-only",
            "--install-build-tools=false",
        ],
        cwd=work_dir,
        home=home_dir,
        path_prefix=bin_dir,
        extra_env={"RTMS_TEST_LOG_DIR": str(log_dir)},
    )

    assert result.returncode == 0, result.stderr
    env_file = work_dir / "relative" / "agent" / ".agent-env.sh"
    assert env_file.exists()
    env_text = env_file.read_text(encoding="utf-8")
    assert f'export RANGE_TEST_INSTALL_DIR="{work_dir / "relative" / "agent"}"' in env_text
    assert 'export RANGE_TEST_SERVER_URL="http://example.com:8000"' in env_text
    assert f'export RANGE_TEST_AGENT_DATA_DIR="{work_dir / "relative" / "agent" / "agent_data"}"' in env_text
    assert f'export RANGE_TEST_SERVER_DATA_DIR="{work_dir / "relative" / "agent" / "server_data"}"' in env_text
    assert not (home_dir / "relative" / "agent" / ".agent-env.sh").exists()


def test_linux_bootstrap_rejects_non_git_nonempty_install_dir(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "workspace"
    existing_dir = work_dir / "existing-agent"
    bin_dir.mkdir()
    home_dir.mkdir()
    work_dir.mkdir()
    existing_dir.mkdir(parents=True)
    (existing_dir / "notes.txt").write_text("keep me", encoding="utf-8")
    _install_fake_linux_commands(bin_dir, log_dir)

    result = _run_script(
        LINUX_BOOTSTRAP,
        [
            "--server-url",
            "http://example.com:8000",
            "--install-dir",
            str(existing_dir),
            "--install-build-tools",
            "false",
        ],
        cwd=work_dir,
        home=home_dir,
        path_prefix=bin_dir,
        extra_env={"RTMS_TEST_LOG_DIR": str(log_dir)},
    )

    assert result.returncode != 0
    assert "already exists and is not an RTMS git checkout" in result.stderr


def test_macos_bootstrap_supports_equals_syntax_and_relative_install_dir(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "workspace"
    bin_dir.mkdir()
    home_dir.mkdir()
    work_dir.mkdir()
    _install_fake_macos_commands(bin_dir, log_dir)

    result = _run_script(
        MACOS_BOOTSTRAP,
        [
            "--server-url=http://example.com:8000/",
            "--install-dir=relative/agent",
            "--mode=build-only",
            "--install-build-tools=false",
        ],
        cwd=work_dir,
        home=home_dir,
        path_prefix=bin_dir,
        extra_env={"RTMS_TEST_LOG_DIR": str(log_dir)},
    )

    assert result.returncode == 0, result.stderr
    env_file = work_dir / "relative" / "agent" / ".agent-env.sh"
    assert env_file.exists()
    env_text = env_file.read_text(encoding="utf-8")
    assert f'export RANGE_TEST_INSTALL_DIR="{work_dir / "relative" / "agent"}"' in env_text
    assert 'export RANGE_TEST_SERVER_URL="http://example.com:8000"' in env_text
    assert f'export RANGE_TEST_AGENT_DATA_DIR="{work_dir / "relative" / "agent" / "agent_data"}"' in env_text
    assert f'export RANGE_TEST_SERVER_DATA_DIR="{work_dir / "relative" / "agent" / "server_data"}"' in env_text


def test_run_agent_uses_install_dir_fallback_when_global_shim_is_missing(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    agent_bin = install_dir / ".venv" / "bin" / "range-test-agent"
    env_file = install_dir / ".agent-env.sh"
    agent_bin.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(
        agent_bin,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'ARGS:%s\\n' "$*"
printf 'SERVER:%s\\n' "${RANGE_TEST_SERVER_URL:-}"
""",
    )
    env_file.write_text(
        (
            'export RANGE_TEST_SERVER_URL="http://fallback.test:8000"\n'
            f'export RANGE_TEST_INSTALL_DIR="{install_dir}"\n'
            f'export RANGE_TEST_AGENT_DATA_DIR="{install_dir / "agent_data"}"\n'
            f'export RANGE_TEST_SERVER_DATA_DIR="{install_dir / "server_data"}"\n'
        ),
        encoding="utf-8",
    )

    result = _run_script(
        RUN_AGENT,
        ["--foreground"],
        cwd=tmp_path,
        home=tmp_path / "home",
        extra_env={"RANGE_TEST_INSTALL_DIR": str(install_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert "ARGS:run --foreground" in result.stdout
    assert "SERVER:http://fallback.test:8000" in result.stdout


def test_run_server_uses_install_dir_fallback_when_global_shim_is_missing(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    server_bin = install_dir / ".venv" / "bin" / "range-test-server"
    server_bin.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(
        server_bin,
        """#!/usr/bin/env bash
set -euo pipefail
printf 'ARGS:%s\\n' "$*"
printf 'SERVER_DATA:%s\\n' "${RANGE_TEST_SERVER_DATA_DIR:-}"
""",
    )
    env_file = install_dir / ".agent-env.sh"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        (
            f'export RANGE_TEST_INSTALL_DIR="{install_dir}"\n'
            f'export RANGE_TEST_AGENT_DATA_DIR="{install_dir / "agent_data"}"\n'
            f'export RANGE_TEST_SERVER_DATA_DIR="{install_dir / "server_data"}"\n'
        ),
        encoding="utf-8",
    )

    result = _run_script(
        RUN_SERVER,
        ["--host", "127.0.0.1"],
        cwd=tmp_path,
        home=tmp_path / "home",
        extra_env={"RANGE_TEST_INSTALL_DIR": str(install_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert "ARGS:--host 127.0.0.1" in result.stdout
    assert f"SERVER_DATA:{install_dir / 'server_data'}" in result.stdout
