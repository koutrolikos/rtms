from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from shared.schemas import AgentCapabilities


class OpenOcdSettings(BaseModel):
    openocd_bin: str = "openocd"
    interface_cfg: str = "interface/stlink.cfg"
    target_cfg: str = "target/stm32g4x.cfg"
    extra_args: list[str] = Field(default_factory=list)
    flash_timeout_seconds: int = 180
    probe_scan_enabled: bool = True
    probe_scan_interval_seconds: float = 10.0
    verify_markers: list[str] = Field(
        default_factory=lambda: ["verified", "verify", "wrote", "shutdown command invoked"]
    )
    probe_serial: str | None = None
    simulate_hardware: bool = False


class CaptureSettings(BaseModel):
    command_template: str | None = None
    terminate_grace_seconds: int = 5
    simulate_capture: bool = False
    builtin_rtt_search_address: str = "0x20000000"
    builtin_rtt_search_size_bytes: int = 131072
    builtin_rtt_id: str = "SEGGER RTT"
    builtin_rtt_human_channel: int = 0
    builtin_rtt_machine_channel: int = 1
    builtin_rtt_polling_interval_ms: int = 10
    builtin_startup_timeout_seconds: int = 15


class AgentSettings(BaseModel):
    server_url: str = "http://127.0.0.1:8000"
    name: str = socket.gethostname()
    label: str | None = None
    hostname: str = socket.gethostname()
    ip_address: str | None = None
    software_version: str = "0.1.0"
    data_dir: Path = Path("agent_data")
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 10.0
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    repo_workspace_root: Path = Path("agent_data/repos")
    downloads_root: Path = Path("agent_data/downloads")
    sessions_root: Path = Path("agent_data/sessions")
    build_root: Path = Path("agent_data/builds")
    git_bin: str = "git"
    github_token: str | None = None
    openocd: OpenOcdSettings = Field(default_factory=OpenOcdSettings)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)

    def prepare_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.repo_workspace_root,
            self.downloads_root,
            self.sessions_root,
            self.build_root,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    data_dir = Path(os.getenv("RANGE_TEST_AGENT_DATA_DIR", "agent_data"))
    return AgentSettings(
        server_url=os.getenv("RANGE_TEST_SERVER_URL", "http://127.0.0.1:8000").rstrip("/"),
        name=os.getenv("RANGE_TEST_AGENT_NAME", socket.gethostname()),
        label=os.getenv("RANGE_TEST_AGENT_LABEL"),
        hostname=os.getenv("RANGE_TEST_AGENT_HOSTNAME", socket.gethostname()),
        ip_address=os.getenv("RANGE_TEST_AGENT_IP"),
        software_version=os.getenv("RANGE_TEST_AGENT_VERSION", "0.1.0"),
        data_dir=data_dir,
        poll_interval_seconds=float(os.getenv("RANGE_TEST_AGENT_POLL_INTERVAL_SECONDS", "2")),
        heartbeat_interval_seconds=float(
            os.getenv("RANGE_TEST_AGENT_HEARTBEAT_INTERVAL_SECONDS", "10")
        ),
        capabilities=AgentCapabilities(
            build_capable=os.getenv("RANGE_TEST_AGENT_BUILD_CAPABLE", "1") == "1",
            flash_capable=os.getenv("RANGE_TEST_AGENT_FLASH_CAPABLE", "1") == "1",
            capture_capable=os.getenv("RANGE_TEST_AGENT_CAPTURE_CAPABLE", "1") == "1",
        ),
        repo_workspace_root=Path(
            os.getenv("RANGE_TEST_AGENT_REPO_WORKSPACE_ROOT", str(data_dir / "repos"))
        ),
        downloads_root=Path(os.getenv("RANGE_TEST_AGENT_DOWNLOADS_ROOT", str(data_dir / "downloads"))),
        sessions_root=Path(os.getenv("RANGE_TEST_AGENT_SESSIONS_ROOT", str(data_dir / "sessions"))),
        build_root=Path(os.getenv("RANGE_TEST_AGENT_BUILD_ROOT", str(data_dir / "builds"))),
        git_bin=os.getenv("RANGE_TEST_AGENT_GIT_BIN", "git"),
        github_token=os.getenv("GITHUB_TOKEN"),
        openocd=OpenOcdSettings(
            openocd_bin=os.getenv("RANGE_TEST_OPENOCD_BIN", "openocd"),
            interface_cfg=os.getenv("RANGE_TEST_OPENOCD_INTERFACE_CFG", "interface/stlink.cfg"),
            target_cfg=os.getenv("RANGE_TEST_OPENOCD_TARGET_CFG", "target/stm32g4x.cfg"),
            probe_scan_enabled=os.getenv("RANGE_TEST_OPENOCD_SCAN_PROBES", "1") == "1",
            probe_scan_interval_seconds=float(
                os.getenv("RANGE_TEST_OPENOCD_SCAN_INTERVAL_SECONDS", "10")
            ),
            probe_serial=os.getenv("RANGE_TEST_PROBE_SERIAL"),
            simulate_hardware=os.getenv("RANGE_TEST_SIMULATE_HARDWARE", "0") == "1",
        ),
        capture=CaptureSettings(
            command_template=os.getenv("RANGE_TEST_CAPTURE_COMMAND_TEMPLATE"),
            simulate_capture=os.getenv("RANGE_TEST_SIMULATE_CAPTURE", "0") == "1",
            builtin_rtt_search_address=os.getenv(
                "RANGE_TEST_OPENOCD_RTT_SEARCH_ADDRESS",
                "0x20000000",
            ),
            builtin_rtt_search_size_bytes=int(
                os.getenv("RANGE_TEST_OPENOCD_RTT_SEARCH_SIZE_BYTES", "131072")
            ),
            builtin_rtt_id=os.getenv("RANGE_TEST_OPENOCD_RTT_ID", "SEGGER RTT"),
            builtin_rtt_human_channel=int(
                os.getenv("RANGE_TEST_OPENOCD_RTT_HUMAN_CHANNEL", "0")
            ),
            builtin_rtt_machine_channel=int(
                os.getenv("RANGE_TEST_OPENOCD_RTT_MACHINE_CHANNEL", "1")
            ),
            builtin_rtt_polling_interval_ms=int(
                os.getenv("RANGE_TEST_OPENOCD_RTT_POLLING_INTERVAL_MS", "10")
            ),
            builtin_startup_timeout_seconds=int(
                os.getenv("RANGE_TEST_OPENOCD_RTT_STARTUP_TIMEOUT_SECONDS", "15")
            ),
        ),
    )
