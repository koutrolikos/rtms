from pathlib import Path

import pytest

from rtms.host.app.core.config import HostSettings, OpenOcdSettings
from rtms.host.app.executors.openocd import OpenOcdExecutor, PrepareFailure
from rtms.host.app.storage.local_state import LocalStateStore, PreparedRoleContext
from rtms.shared.enums import ArtifactOriginType, Role
from rtms.shared.manifest import ArtifactBundleManifest, BundleFileEntry, FlashSpec
from rtms.shared.time_sync import utc_now


def test_default_openocd_target_cfg_matches_stm32g4() -> None:
    settings = HostSettings(server_url="http://172.20.10.3:8000")
    assert settings.openocd.target_cfg == "target/stm32g4x.cfg"


def test_program_command_normalizes_windows_path() -> None:
    settings = HostSettings(server_url="http://172.20.10.3:8000")
    executor = OpenOcdExecutor(settings, LocalStateStore(Path("unused-state")))
    command = executor._program_command(
        Path(r"host_data\sessions\session-1\rx\bundle\firmware\High-Altitude-CC.elf")
    )
    assert command == (
        "program {host_data/sessions/session-1/rx/bundle/firmware/High-Altitude-CC.elf} "
        "verify reset exit"
    )


def test_flash_failure_includes_target_cfg_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    image_path = tmp_path / "fw.elf"
    image_path.write_bytes(b"firmware")
    settings = HostSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "host_data",
        openocd=OpenOcdSettings(target_cfg="target/stm32f4x.cfg"),
    )
    executor = OpenOcdExecutor(settings, LocalStateStore(tmp_path / "state"))
    context = PreparedRoleContext(
        session_id="session-1",
        role_run_id="role-run-1",
        role=Role.TX.value,
        artifact_id="artifact-1",
        work_dir=str(tmp_path / "work"),
        bundle_path=str(tmp_path / "bundle.zip"),
        extracted_dir=str(tmp_path / "bundle"),
        manifest=ArtifactBundleManifest(
            artifact_id="artifact-1",
            session_id="session-1",
            origin_type=ArtifactOriginType.MANUAL_UPLOAD,
            role_hint=Role.TX,
            source_repo="koutrolikos/High-Altitude-CC",
            git_sha="abc123",
            created_at=utc_now(),
            files=[
                BundleFileEntry(
                    path="firmware/fw.elf",
                    size_bytes=image_path.stat().st_size,
                    sha256="abc",
                    kind="payload",
                )
            ],
            flash=FlashSpec(flash_image_path="firmware/fw.elf", elf_path="firmware/fw.elf"),
        ),
        probe_serial="123456",
        openocd_log_path=str(tmp_path / "openocd.log"),
    )

    class Completed:
        returncode = 1
        stdout = ""
        stderr = (
            "Warn : Cannot identify target as a STM32 family.\n"
            "Error: auto_probe failed\n"
        )

    monkeypatch.setattr("rtms.host.app.executors.openocd.subprocess.run", lambda *args, **kwargs: Completed())

    with pytest.raises(PrepareFailure) as exc_info:
        executor._flash_and_verify(image_path, context)

    assert exc_info.value.reason == "flash_failed"
    assert exc_info.value.diagnostics["openocd_target_cfg"] == "target/stm32f4x.cfg"
    assert "stm32g4x.cfg" in exc_info.value.diagnostics["hint"]


def test_missing_openocd_binary_includes_install_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    image_path = tmp_path / "fw.elf"
    image_path.write_bytes(b"firmware")
    settings = HostSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "host_data",
        openocd=OpenOcdSettings(openocd_bin="openocd"),
    )
    executor = OpenOcdExecutor(settings, LocalStateStore(tmp_path / "state"))
    context = PreparedRoleContext(
        session_id="session-1",
        role_run_id="role-run-1",
        role=Role.RX.value,
        artifact_id="artifact-1",
        work_dir=str(tmp_path / "work"),
        bundle_path=str(tmp_path / "bundle.zip"),
        extracted_dir=str(tmp_path / "bundle"),
        manifest=ArtifactBundleManifest(
            artifact_id="artifact-1",
            session_id="session-1",
            origin_type=ArtifactOriginType.MANUAL_UPLOAD,
            role_hint=Role.RX,
            source_repo="koutrolikos/High-Altitude-CC",
            git_sha="abc123",
            created_at=utc_now(),
            files=[
                BundleFileEntry(
                    path="firmware/fw.elf",
                    size_bytes=image_path.stat().st_size,
                    sha256="abc",
                    kind="payload",
                )
            ],
            flash=FlashSpec(flash_image_path="firmware/fw.elf", elf_path="firmware/fw.elf"),
        ),
        probe_serial="123456",
        openocd_log_path=str(tmp_path / "openocd.log"),
    )

    def raise_missing(*args, **kwargs):
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")

    monkeypatch.setattr("rtms.host.app.executors.openocd.subprocess.run", raise_missing)

    with pytest.raises(PrepareFailure) as exc_info:
        executor._flash_and_verify(image_path, context)

    assert exc_info.value.reason == "openocd_launch_failed"
    assert exc_info.value.diagnostics["openocd_bin"] == "openocd"
    assert "RTMS_OPENOCD_BIN" in exc_info.value.diagnostics["hint"]


def test_flash_uses_adapter_serial_with_normalized_probe_serial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "fw.elf"
    image_path.write_bytes(b"firmware")
    settings = HostSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "host_data",
    )
    executor = OpenOcdExecutor(settings, LocalStateStore(tmp_path / "state"))
    context = PreparedRoleContext(
        session_id="session-1",
        role_run_id="role-run-1",
        role=Role.TX.value,
        artifact_id="artifact-1",
        work_dir=str(tmp_path / "work"),
        bundle_path=str(tmp_path / "bundle.zip"),
        extracted_dir=str(tmp_path / "bundle"),
        manifest=ArtifactBundleManifest(
            artifact_id="artifact-1",
            session_id="session-1",
            origin_type=ArtifactOriginType.MANUAL_UPLOAD,
            role_hint=Role.TX,
            source_repo="koutrolikos/High-Altitude-CC",
            git_sha="abc123",
            created_at=utc_now(),
            files=[
                BundleFileEntry(
                    path="firmware/fw.elf",
                    size_bytes=image_path.stat().st_size,
                    sha256="abc",
                    kind="payload",
                )
            ],
            flash=FlashSpec(flash_image_path="firmware/fw.elf", elf_path="firmware/fw.elf"),
        ),
        probe_serial='Tÿp\x06fuUU\x13D"\x87',
        openocd_log_path=str(tmp_path / "openocd.log"),
    )
    captured_command: list[str] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = "** Verified OK **\nshutdown command invoked\n"

    def fake_run(command, **kwargs):
        captured_command[:] = command
        return Completed()

    monkeypatch.setattr("rtms.host.app.executors.openocd.subprocess.run", fake_run)

    executor._flash_and_verify(image_path, context)

    assert captured_command[5:7] == ["-c", "adapter serial 54FF70066675555513442287"]
