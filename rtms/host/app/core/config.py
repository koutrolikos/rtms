from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from rtms.shared.schemas import HostCapabilities


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


class HostSettings(BaseModel):
    server_url: str = "http://127.0.0.1:8000"
    server_username: str | None = None
    server_password: str | None = None
    name: str = socket.gethostname()
    label: str | None = None
    hostname: str = socket.gethostname()
    ip_address: str | None = None
    software_version: str = "0.1.0"
    data_dir: Path = Path("host_data")
    poll_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 10.0
    capabilities: HostCapabilities = Field(default_factory=HostCapabilities)
    repo_workspace_root: Path = Path("host_data/repos")
    downloads_root: Path = Path("host_data/downloads")
    sessions_root: Path = Path("host_data/sessions")
    build_root: Path = Path("host_data/builds")
    git_bin: str = "git"
    github_token: str | None = None
    openocd: OpenOcdSettings = Field(default_factory=OpenOcdSettings)
    capture: CaptureSettings = Field(default_factory=CaptureSettings)

    @model_validator(mode="after")
    def _validate_server_auth(self) -> "HostSettings":
        if bool(self.server_username) != bool(self.server_password):
            raise ValueError(
                "RTMS_SERVER_USERNAME and RTMS_SERVER_PASSWORD must either both be set or both be empty"
            )
        default_roots = {
            "repo_workspace_root": Path("host_data/repos"),
            "downloads_root": Path("host_data/downloads"),
            "sessions_root": Path("host_data/sessions"),
            "build_root": Path("host_data/builds"),
        }
        if self.repo_workspace_root == default_roots["repo_workspace_root"]:
            self.repo_workspace_root = self.data_dir / "repos"
        if self.downloads_root == default_roots["downloads_root"]:
            self.downloads_root = self.data_dir / "downloads"
        if self.sessions_root == default_roots["sessions_root"]:
            self.sessions_root = self.data_dir / "sessions"
        if self.build_root == default_roots["build_root"]:
            self.build_root = self.data_dir / "builds"
        return self

    @property
    def server_basic_auth(self) -> tuple[str, str] | None:
        if not self.server_username or not self.server_password:
            return None
        return self.server_username, self.server_password

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
def get_settings() -> HostSettings:
    data_dir = Path(os.getenv("RTMS_HOST_DATA_DIR", "host_data"))
    return HostSettings(
        server_url=os.getenv("RTMS_SERVER_URL", "http://127.0.0.1:8000").rstrip("/"),
        server_username=os.getenv("RTMS_SERVER_USERNAME"),
        server_password=os.getenv("RTMS_SERVER_PASSWORD"),
        name=os.getenv("RTMS_HOST_NAME", socket.gethostname()),
        label=os.getenv("RTMS_HOST_LABEL"),
        hostname=os.getenv("RTMS_HOST_HOSTNAME", socket.gethostname()),
        ip_address=os.getenv("RTMS_HOST_IP"),
        software_version=os.getenv("RTMS_HOST_VERSION", "0.1.0"),
        data_dir=data_dir,
        poll_interval_seconds=float(os.getenv("RTMS_HOST_POLL_INTERVAL_SECONDS", "2")),
        heartbeat_interval_seconds=float(
            os.getenv("RTMS_HOST_HEARTBEAT_INTERVAL_SECONDS", "10")
        ),
        capabilities=HostCapabilities(
            build_capable=os.getenv("RTMS_HOST_BUILD_CAPABLE", "1") == "1",
            flash_capable=os.getenv("RTMS_HOST_FLASH_CAPABLE", "1") == "1",
            capture_capable=os.getenv("RTMS_HOST_CAPTURE_CAPABLE", "1") == "1",
        ),
        repo_workspace_root=Path(
            os.getenv("RTMS_HOST_REPO_WORKSPACE_ROOT", str(data_dir / "repos"))
        ),
        downloads_root=Path(os.getenv("RTMS_HOST_DOWNLOADS_ROOT", str(data_dir / "downloads"))),
        sessions_root=Path(os.getenv("RTMS_HOST_SESSIONS_ROOT", str(data_dir / "sessions"))),
        build_root=Path(os.getenv("RTMS_HOST_BUILD_ROOT", str(data_dir / "builds"))),
        git_bin=os.getenv("RTMS_HOST_GIT_BIN", "git"),
        github_token=os.getenv("GITHUB_TOKEN"),
        openocd=OpenOcdSettings(
            openocd_bin=os.getenv("RTMS_OPENOCD_BIN", "openocd"),
            interface_cfg=os.getenv("RTMS_OPENOCD_INTERFACE_CFG", "interface/stlink.cfg"),
            target_cfg=os.getenv("RTMS_OPENOCD_TARGET_CFG", "target/stm32g4x.cfg"),
            probe_scan_enabled=os.getenv("RTMS_OPENOCD_SCAN_PROBES", "1") == "1",
            probe_scan_interval_seconds=float(
                os.getenv("RTMS_OPENOCD_SCAN_INTERVAL_SECONDS", "10")
            ),
            probe_serial=os.getenv("RTMS_PROBE_SERIAL"),
            simulate_hardware=os.getenv("RTMS_SIMULATE_HARDWARE", "0") == "1",
        ),
        capture=CaptureSettings(
            command_template=os.getenv("RTMS_CAPTURE_COMMAND_TEMPLATE"),
            simulate_capture=os.getenv("RTMS_SIMULATE_CAPTURE", "0") == "1",
            builtin_rtt_search_address=os.getenv(
                "RTMS_OPENOCD_RTT_SEARCH_ADDRESS",
                "0x20000000",
            ),
            builtin_rtt_search_size_bytes=int(
                os.getenv("RTMS_OPENOCD_RTT_SEARCH_SIZE_BYTES", "131072")
            ),
            builtin_rtt_id=os.getenv("RTMS_OPENOCD_RTT_ID", "SEGGER RTT"),
            builtin_rtt_human_channel=int(
                os.getenv("RTMS_OPENOCD_RTT_HUMAN_CHANNEL", "0")
            ),
            builtin_rtt_machine_channel=int(
                os.getenv("RTMS_OPENOCD_RTT_MACHINE_CHANNEL", "1")
            ),
            builtin_rtt_polling_interval_ms=int(
                os.getenv("RTMS_OPENOCD_RTT_POLLING_INTERVAL_MS", "10")
            ),
            builtin_startup_timeout_seconds=int(
                os.getenv("RTMS_OPENOCD_RTT_STARTUP_TIMEOUT_SECONDS", "15")
            ),
        ),
    )
