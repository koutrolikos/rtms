from __future__ import annotations

import json
import os
import socket
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from rtms.shared.schemas import ConfiguredRepo


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    public_base_url: str | None = None
    database_url: str = "sqlite:///./server_data/server.db"
    data_dir: Path = Path("server_data")
    artifacts_dir_name: str = "artifacts"
    raw_dir_name: str = "raw"
    reports_dir_name: str = "reports"
    default_duration_minutes: int = 5
    capture_start_lead_seconds: int = 5
    host_offline_seconds: int = 30
    github_token: str | None = None
    repo_config_path: Path = Path("server_data/repos.json")
    auth_username: str | None = None
    auth_password: str | None = None

    @model_validator(mode="after")
    def _validate_auth_fields(self) -> "ServerSettings":
        if bool(self.auth_username) != bool(self.auth_password):
            raise ValueError(
                "RTMS_AUTH_USERNAME and RTMS_AUTH_PASSWORD must either both be set or both be empty"
            )
        return self

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / self.artifacts_dir_name

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / self.raw_dir_name

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / self.reports_dir_name

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_username and self.auth_password)

    def load_repos(self) -> list[ConfiguredRepo]:
        if not self.repo_config_path.exists():
            return []
        payload = json.loads(self.repo_config_path.read_text(encoding="utf-8"))
        return [ConfiguredRepo.model_validate(item) for item in payload]

    @property
    def effective_public_base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        return f"http://{detect_lan_ip()}:{self.port}"


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    try:
        address = socket.gethostbyname(socket.gethostname())
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    return "127.0.0.1"


@lru_cache(maxsize=1)
def get_settings() -> ServerSettings:
    data_dir = Path(os.getenv("RTMS_SERVER_DATA_DIR", "server_data"))
    return ServerSettings(
        host=os.getenv("RTMS_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("RTMS_SERVER_PORT", "8000")),
        public_base_url=os.getenv("RTMS_SERVER_PUBLIC_BASE_URL"),
        database_url=os.getenv(
            "RTMS_SERVER_DB_URL", f"sqlite:///{data_dir / 'server.db'}"
        ),
        data_dir=data_dir,
        default_duration_minutes=int(
            os.getenv("RTMS_DEFAULT_DURATION_MINUTES", "5")
        ),
        capture_start_lead_seconds=int(
            os.getenv("RTMS_CAPTURE_START_LEAD_SECONDS", "5")
        ),
        host_offline_seconds=int(os.getenv("RTMS_HOST_OFFLINE_SECONDS", "30")),
        github_token=os.getenv("GITHUB_TOKEN"),
        auth_username=os.getenv("RTMS_AUTH_USERNAME"),
        auth_password=os.getenv("RTMS_AUTH_PASSWORD"),
        repo_config_path=Path(
            os.getenv("RTMS_REPO_CONFIG", str(data_dir / "repos.json"))
        ),
    )
