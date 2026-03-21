import subprocess
from pathlib import Path

import pytest

from agent.app.core.config import AgentSettings
from agent.app.executors.build import BuildExecutor, BuildFailure
from shared.enums import ArtifactOriginType, RawArtifactType, Role
from shared.manifest import ArtifactBundleManifest, BundleFileEntry, FlashSpec
from shared.schemas import (
    ArtifactUploadResult,
    BuildArtifactPayload,
    BuildRecipe,
    ConfiguredRepo,
    HighAltitudeCCBuildConfig,
    HighAltitudeCCChannelSelectionConfig,
    HighAltitudeCCExclusionMask,
    RawArtifactUploadResult,
)
from shared.time_sync import utc_now


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


def test_preflight_command_inputs_reports_missing_makefile(tmp_path: Path) -> None:
    executor = BuildExecutor(AgentSettings())
    build_dir = tmp_path / "repo"
    build_dir.mkdir()

    with pytest.raises(BuildFailure) as exc_info:
        executor._preflight_command_inputs("make -f Debug/makefile DEBUG=1", cwd=build_dir)

    assert exc_info.value.reason == "build_inputs_missing"
    assert exc_info.value.diagnostics["missing_inputs"] == [
        {
            "kind": "makefile",
            "path": "Debug/makefile",
            "resolved_path": str(build_dir / "Debug" / "makefile"),
        }
    ]


def test_run_command_timeout_raises_specific_build_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = BuildExecutor(AgentSettings())
    log_path = tmp_path / "build.log"

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args") or args[0],
            timeout=kwargs.get("timeout", 1),
            output="partial stdout",
            stderr="partial stderr",
        )

    monkeypatch.setattr("agent.app.executors.build.subprocess.run", fake_run)

    with pytest.raises(BuildFailure) as exc_info:
        executor._run_command("sleep 10", cwd=tmp_path, timeout_seconds=1, log_path=log_path)

    assert exc_info.value.reason == "command_timed_out"
    assert exc_info.value.diagnostics["timeout_seconds"] == 1
    assert exc_info.value.diagnostics["stdout"] == "partial stdout"
    assert exc_info.value.diagnostics["stderr"] == "partial stderr"
    assert log_path.exists()


def _high_altitude_cc_payload() -> BuildArtifactPayload:
    return BuildArtifactPayload(
        artifact_id="artifact-1",
        session_id="session-1",
        role_hint=Role.RX,
        git_sha="deadbeefcafebabe",
        repo=ConfiguredRepo(
            id="high-altitude-cc",
            display_name="High-Altitude-CC",
            full_name="koutrolikos/High-Altitude-CC",
            clone_url="https://github.com/koutrolikos/High-Altitude-CC.git",
            default_branch="dev",
            build_recipe=BuildRecipe(
                build_command="make -f Debug/makefile DEBUG=1 all hex bin",
                artifact_globs=[
                    "build/debug/High-Altitude-CC.elf",
                    "build/debug/High-Altitude-CC.hex",
                    "build/debug/High-Altitude-CC.bin",
                    "build/debug/High-Altitude-CC.map",
                ],
                elf_glob="build/debug/High-Altitude-CC.elf",
                flash_image_glob="build/debug/High-Altitude-CC.elf",
                timeout_seconds=1200,
                env={},
                rtt_symbol="_SEGGER_RTT",
            ),
        ),
        build_config=HighAltitudeCCBuildConfig(
            app_debug_enable=0,
            app_log_level=2,
            chsel=HighAltitudeCCChannelSelectionConfig(
                allowlist_hz=[433200000, 434600000],
                band_min_hz=433050000,
                band_max_hz=434790000,
                our_half_bw_hz=108500,
                guard_band_hz=30000,
                exclusion_masks=[
                    HighAltitudeCCExclusionMask(center_hz=433920000, half_bw_hz=25000),
                ],
                backup_failover_holdoff_ms=15000,
            ),
        ),
    )


def test_resolve_build_command_adds_high_altitude_cc_cdefs() -> None:
    executor = BuildExecutor(AgentSettings())

    command, cdefs = executor._resolve_build_command(_high_altitude_cc_payload())

    assert command.startswith("make -f Debug/makefile DEBUG=1 all hex bin CDEFS_EXTRA=")
    assert "-DAPP_ROLE_MODE=APP_ROLE_MODE_RX" in cdefs
    assert "-DAPP_DEBUG_ENABLE=0" in cdefs
    assert "-DAPP_LOG_LEVEL=2" in cdefs
    assert "-DAPP_CHSEL_ALLOWLIST_COUNT=2U" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK_COUNT=1U" in cdefs
    assert "-DAPP_CHSEL_EXCLUSION_MASK1_CENTER_HZ=0UL" in cdefs


def test_run_build_uploads_build_log_and_cleans_workspace(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://192.168.1.50:8000",
        data_dir=tmp_path / "agent_data",
    )
    settings.prepare_dirs()
    executor = BuildExecutor(settings)
    payload = _high_altitude_cc_payload()
    commands: list[str] = []
    raw_upload_calls: list[dict] = []

    def fake_checkout(repo_root: Path, clone_url: str, git_sha: str, *, default_branch: str | None = None) -> None:
        del clone_url, git_sha, default_branch
        (repo_root / ".git").mkdir(parents=True, exist_ok=True)
        (repo_root / "Debug").mkdir(parents=True, exist_ok=True)
        (repo_root / "Debug" / "makefile").write_text("all:\n", encoding="utf-8")

    def fake_run_command(
        command: str,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 900,
        log_path: Path | None = None,
        display_command: str | None = None,
    ) -> None:
        del env, timeout_seconds, display_command
        assert cwd is not None
        commands.append(command)
        build_dir = cwd / "build" / "debug"
        build_dir.mkdir(parents=True, exist_ok=True)
        for suffix in ("elf", "hex", "bin", "map"):
            (build_dir / f"High-Altitude-CC.{suffix}").write_text("artifact", encoding="utf-8")
        if log_path is not None:
            log_path.write_text("build log", encoding="utf-8")

    class FakeClient:
        def upload_artifact_bundle(self, **kwargs):
            assert kwargs["bundle_path"].exists()
            return ArtifactUploadResult(
                artifact_id="artifact-1",
                storage_path="artifacts/session-1/artifact-1/bundle.zip",
                sha256="bundle-sha",
                manifest=ArtifactBundleManifest(
                    artifact_id="artifact-1",
                    session_id="session-1",
                    origin_type=ArtifactOriginType.GITHUB_BUILD,
                    role_hint=Role.RX,
                    source_repo="koutrolikos/High-Altitude-CC",
                    git_sha="deadbeefcafebabe",
                    created_at=utc_now(),
                    files=[
                        BundleFileEntry(
                            path="build/debug/High-Altitude-CC.elf",
                            size_bytes=8,
                            sha256="artifact-sha",
                            kind="payload",
                        )
                    ],
                    flash=FlashSpec(
                        flash_image_path="build/debug/High-Altitude-CC.elf",
                        elf_path="build/debug/High-Altitude-CC.elf",
                        rtt_symbol="_SEGGER_RTT",
                    ),
                ),
            )

        def upload_raw_artifact(self, **kwargs):
            assert kwargs["path"].exists()
            raw_upload_calls.append(kwargs)
            return RawArtifactUploadResult(
                raw_artifact_id="raw-build-log",
                storage_path="raw/session-1/RX/build.log",
                sha256="raw-sha",
                size_bytes=8,
            )

    executor._checkout = fake_checkout  # type: ignore[method-assign]
    executor._run_command = fake_run_command  # type: ignore[method-assign]

    result = executor.run_build(payload, client=FakeClient(), agent_id="agent-1")

    assert result.success is True
    assert "-DAPP_ROLE_MODE=APP_ROLE_MODE_RX" in commands[0]
    assert raw_upload_calls[0]["artifact_type"] == RawArtifactType.BUILD_LOG
    assert raw_upload_calls[0]["role"] == Role.RX
    assert result.uploaded_raw_artifacts[0]["raw_artifact_id"] == "raw-build-log"
    assert not (settings.repo_workspace_root / "high-altitude-cc").exists()
    assert not (settings.build_root / "session-1" / "artifact-1").exists()
