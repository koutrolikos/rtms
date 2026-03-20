from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from shared.enums import AgentStatus, ArtifactOriginType, RawArtifactType, Role
from shared.schemas import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentPollRequest,
    AgentPollResponse,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    ArtifactUploadResult,
    ConfiguredRepo,
    JobResult,
    RawArtifactUploadResult,
)
from shared.time_sync import estimate_offset


class ServerClient:
    def __init__(self, server_url: str, timeout: float = 60.0) -> None:
        self.server_url = server_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def register_agent(self, request: AgentRegistrationRequest) -> AgentRegistrationResponse:
        response = self.client.post(f"{self.server_url}/api/agent/register", json=request.model_dump(mode="json"))
        response.raise_for_status()
        return AgentRegistrationResponse.model_validate(response.json())

    def heartbeat(self, request: AgentHeartbeatRequest) -> AgentHeartbeatResponse:
        response = self.client.post(f"{self.server_url}/api/agent/heartbeat", json=request.model_dump(mode="json"))
        response.raise_for_status()
        return AgentHeartbeatResponse.model_validate(response.json())

    def sample_time_sync(self):
        send_at = datetime.now().astimezone()
        response = self.client.get(f"{self.server_url}/api/agent/time-sync")
        recv_at = datetime.now().astimezone()
        response.raise_for_status()
        server_time = datetime.fromisoformat(response.json()["server_time"])
        return estimate_offset(send_at, server_time, recv_at)

    def poll(self, agent_id: str, status: AgentStatus) -> AgentPollResponse:
        request = AgentPollRequest(agent_id=agent_id, status=status)
        response = self.client.post(f"{self.server_url}/api/agent/poll", json=request.model_dump(mode="json"))
        response.raise_for_status()
        return AgentPollResponse.model_validate(response.json())

    def report_job_result(self, job_id: str, result: JobResult) -> None:
        response = self.client.post(
            f"{self.server_url}/api/agent/jobs/{job_id}/result",
            json=result.model_dump(mode="json"),
        )
        response.raise_for_status()

    def upload_artifact_bundle(
        self,
        *,
        bundle_path: Path,
        session_id: str,
        artifact_id: str | None,
        origin_type: ArtifactOriginType,
        producing_agent_id: str | None,
        role_hint: Role | None = None,
        source_repo: str | None = None,
        git_sha: str | None = None,
    ) -> ArtifactUploadResult:
        with bundle_path.open("rb") as handle:
            files = {"artifact_bundle": (bundle_path.name, handle, "application/zip")}
            data = {
                "session_id": session_id,
                "artifact_id": artifact_id,
                "origin_type": origin_type.value,
                "producing_agent_id": producing_agent_id,
                "role_hint": role_hint.value if role_hint else "",
                "source_repo": source_repo or "",
                "git_sha": git_sha or "",
            }
            response = self.client.post(f"{self.server_url}/api/agent/artifacts/upload", files=files, data=data)
        response.raise_for_status()
        return ArtifactUploadResult.model_validate(response.json())

    def upload_raw_artifact(
        self,
        *,
        path: Path,
        session_id: str,
        artifact_type: RawArtifactType,
        role: Role | None,
        metadata: dict[str, Any] | None = None,
    ) -> RawArtifactUploadResult:
        with path.open("rb") as handle:
            files = {"file": (path.name, handle, "application/octet-stream")}
            data = {
                "session_id": session_id,
                "artifact_type": artifact_type.value,
                "role": role.value if role else "",
                "metadata_json": json.dumps(metadata or {}),
            }
            response = self.client.post(
                f"{self.server_url}/api/agent/raw-artifacts/upload",
                files=files,
                data=data,
            )
        response.raise_for_status()
        return RawArtifactUploadResult.model_validate(response.json())

    def download_artifact(self, artifact_download_url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.client.stream("GET", artifact_download_url) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return destination

    def list_repos(self) -> list[ConfiguredRepo]:
        response = self.client.get(f"{self.server_url}/api/repos")
        response.raise_for_status()
        return [ConfiguredRepo.model_validate(item) for item in response.json()]

