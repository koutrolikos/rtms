from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from shared.schemas import ConfiguredRepo


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    database_url: str = "sqlite:///./server_data/server.db"
    data_dir: Path = Path("server_data")
    artifacts_dir_name: str = "artifacts"
    raw_dir_name: str = "raw"
    reports_dir_name: str = "reports"
    default_duration_minutes: int = 5
    capture_start_lead_seconds: int = 5
    agent_offline_seconds: int = 30
    github_token: str | None = None
    repo_config_path: Path = Path("server_data/repos.json")

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / self.artifacts_dir_name

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / self.raw_dir_name

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / self.reports_dir_name

    def load_repos(self) -> list[ConfiguredRepo]:
        if not self.repo_config_path.exists():
            return []
        payload = json.loads(self.repo_config_path.read_text(encoding="utf-8"))
        return [ConfiguredRepo.model_validate(item) for item in payload]


@lru_cache(maxsize=1)
def get_settings() -> ServerSettings:
    data_dir = Path(os.getenv("RANGE_TEST_SERVER_DATA_DIR", "server_data"))
    return ServerSettings(
        host=os.getenv("RANGE_TEST_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("RANGE_TEST_SERVER_PORT", "8000")),
        database_url=os.getenv(
            "RANGE_TEST_SERVER_DB_URL", f"sqlite:///{data_dir / 'server.db'}"
        ),
        data_dir=data_dir,
        default_duration_minutes=int(
            os.getenv("RANGE_TEST_DEFAULT_DURATION_MINUTES", "5")
        ),
        capture_start_lead_seconds=int(
            os.getenv("RANGE_TEST_CAPTURE_START_LEAD_SECONDS", "5")
        ),
        agent_offline_seconds=int(os.getenv("RANGE_TEST_AGENT_OFFLINE_SECONDS", "30")),
        github_token=os.getenv("GITHUB_TOKEN"),
        repo_config_path=Path(
            os.getenv("RANGE_TEST_REPO_CONFIG", str(data_dir / "repos.json"))
        ),
    )

