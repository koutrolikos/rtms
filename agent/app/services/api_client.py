from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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


class ServerConnectionError(RuntimeError):
    """Raised when the agent cannot reach the configured server URL."""


def validate_server_url(server_url: str) -> None:
    parsed = urlparse(server_url)
    host = parsed.hostname
    if not parsed.scheme or not host:
        raise ServerConnectionError(
            f"Invalid RANGE_TEST_SERVER_URL: {server_url!r}. Expected format like http://172.20.10.3:8000"
        )
    if host == "0.0.0.0":
        raise ServerConnectionError(
            "RANGE_TEST_SERVER_URL points at the server listen address "
            f"({server_url}), which is not a routable destination. Use either "
            "http://127.0.0.1:8000 for same-machine development or the server machine's "
            "LAN/VPS IP for remote agents, for example http://172.20.10.3:8000"
        )


def describe_connect_error(server_url: str, exc: Exception) -> ServerConnectionError:
    parsed = urlparse(server_url)
    host = parsed.hostname or "<unknown-host>"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    error_text = str(exc)
    upper_error = error_text.upper()
    if isinstance(exc, httpx.TimeoutException) or "TIMED OUT" in upper_error:
        if host in {"127.0.0.1", "localhost"}:
            return ServerConnectionError(
                "Timed out while connecting to the control server at "
                f"{server_url}. The agent and server appear to be using a same-machine URL, "
                "so check that the server process is still running and listening on that port. "
                f"Original error: {exc}"
            )
        return ServerConnectionError(
            "Timed out while connecting to the control server at "
            f"{server_url}. Check that the server is running, that {host}:{port} is reachable "
            "from the agent machine, and that any firewall allows inbound TCP on that port. "
            "If the agent is running on the same machine as the server, prefer "
            "http://127.0.0.1:8000 instead of the machine's LAN IP. "
            f"Original error: {exc}"
        )
    if "WRONG_VERSION_NUMBER" in upper_error or (
        parsed.scheme == "https" and "SSL" in upper_error
    ):
        return ServerConnectionError(
            "TLS negotiation failed while connecting to "
            f"{server_url}. This usually means the agent is using https:// but the server is "
            "running plain HTTP. Use an http:// URL for RANGE_TEST_SERVER_URL unless you have "
            "put the server behind a real TLS terminator or reverse proxy. "
            f"Original error: {exc}"
        )
    return ServerConnectionError(
        "Could not connect to the control server at "
        f"{server_url}. The TCP connection to {host}:{port} was refused. "
        "Check that the server is running and that inbound TCP "
        f"{port} is allowed through the server machine's firewall. If the agent is on the same "
        "machine as the server, prefer http://127.0.0.1:8000. "
        f"Original error: {exc}"
    )


class ServerClient:
    def __init__(self, server_url: str, timeout: float = 60.0) -> None:
        self.server_url = server_url.rstrip("/")
        validate_server_url(self.server_url)
        self.client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def register_agent(self, request: AgentRegistrationRequest) -> AgentRegistrationResponse:
        try:
            response = self.client.post(
                f"{self.server_url}/api/agent/register",
                json=request.model_dump(mode="json"),
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise describe_connect_error(self.server_url, exc) from exc
        response.raise_for_status()
        return AgentRegistrationResponse.model_validate(response.json())

    def heartbeat(self, request: AgentHeartbeatRequest) -> AgentHeartbeatResponse:
        try:
            response = self.client.post(
                f"{self.server_url}/api/agent/heartbeat",
                json=request.model_dump(mode="json"),
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise describe_connect_error(self.server_url, exc) from exc
        response.raise_for_status()
        return AgentHeartbeatResponse.model_validate(response.json())

    def sample_time_sync(self):
        send_at = datetime.now().astimezone()
        try:
            response = self.client.get(f"{self.server_url}/api/agent/time-sync")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise describe_connect_error(self.server_url, exc) from exc
        recv_at = datetime.now().astimezone()
        response.raise_for_status()
        server_time = datetime.fromisoformat(response.json()["server_time"])
        return estimate_offset(send_at, server_time, recv_at)

    def poll(self, agent_id: str, status: AgentStatus) -> AgentPollResponse:
        request = AgentPollRequest(agent_id=agent_id, status=status)
        try:
            response = self.client.post(
                f"{self.server_url}/api/agent/poll",
                json=request.model_dump(mode="json"),
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise describe_connect_error(self.server_url, exc) from exc
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
            try:
                response = self.client.post(
                    f"{self.server_url}/api/agent/artifacts/upload",
                    files=files,
                    data=data,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise describe_connect_error(self.server_url, exc) from exc
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
            try:
                response = self.client.post(
                    f"{self.server_url}/api/agent/raw-artifacts/upload",
                    files=files,
                    data=data,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                raise describe_connect_error(self.server_url, exc) from exc
        response.raise_for_status()
        return RawArtifactUploadResult.model_validate(response.json())

    def download_artifact(self, artifact_download_url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.client.stream("GET", artifact_download_url) as response:
                response.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise describe_connect_error(artifact_download_url, exc) from exc
        return destination

    def list_repos(self) -> list[ConfiguredRepo]:
        try:
            response = self.client.get(f"{self.server_url}/api/repos")
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise describe_connect_error(self.server_url, exc) from exc
        response.raise_for_status()
        return [ConfiguredRepo.model_validate(item) for item in response.json()]
