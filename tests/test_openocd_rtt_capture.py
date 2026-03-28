from __future__ import annotations

import threading
from pathlib import Path

import pytest

from rtms.host.app.core.config import HostSettings
from rtms.host.app.executors.openocd_rtt_capture import OpenOcdRttCapture
from rtms.host.app.storage.local_state import PreparedRoleContext
from rtms.shared.enums import ArtifactOriginType, Role
from rtms.shared.manifest import ArtifactBundleManifest, FlashSpec
from rtms.shared.time_sync import utc_now


def test_builtin_capture_uses_adapter_serial_with_normalized_probe_serial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = HostSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "host_data",
    )
    ports = iter([9000, 9001])
    monkeypatch.setattr(
        "rtms.host.app.executors.openocd_rtt_capture._reserve_local_tcp_port",
        lambda: next(ports),
    )
    capture = OpenOcdRttCapture(
        settings=settings,
        context=PreparedRoleContext(
            session_id="session-1",
            role_run_id="role-run-1",
            role=Role.RX.value,
            artifact_id="artifact-1",
            work_dir=str(tmp_path / "session" / "rx"),
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
                files=[],
                flash=FlashSpec(elf_path="firmware.elf", flash_image_path="firmware.elf"),
            ),
            probe_serial='Tÿp\x06fuUU\x13D"\x87',
        ),
        duration_seconds=1,
        stop_requested=threading.Event(),
        rtt_human_log_path=tmp_path / "session" / "rx" / "rtt.log",
        rtt_machine_log_path=tmp_path / "session" / "rx" / "rtt.rttbin",
        capture_command_log_path=tmp_path / "session" / "rx" / "capture-command.log",
    )

    command = capture._command()

    assert command[5:7] == ["-c", "adapter serial 54FF70066675555513442287"]
