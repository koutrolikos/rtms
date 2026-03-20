from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from shared.enums import (
    AgentStatus,
    ArtifactOriginType,
    ArtifactStatus,
    EventSourceType,
    EventType,
    JobState,
    JobType,
    LocationMode,
    RawArtifactType,
    ReportStatus,
    Role,
    RoleRunState,
    SessionState,
    StopMode,
    TimestampKind,
)
from shared.manifest import ArtifactBundleManifest
from shared.time_sync import TimeSyncSample


class AgentCapabilities(BaseModel):
    build_capable: bool = False
    flash_capable: bool = True
    capture_capable: bool = True


class BuildRecipe(BaseModel):
    build_command: str
    artifact_globs: list[str] = Field(default_factory=list)
    elf_glob: str | None = None
    flash_image_glob: str | None = None
    checkout_subdir: str = "."
    timeout_seconds: int = 900
    env: dict[str, str] = Field(default_factory=dict)
    rtt_symbol: str | None = None


class ConfiguredRepo(BaseModel):
    id: str
    display_name: str
    full_name: str
    clone_url: str
    api_url: str = "https://api.github.com"
    default_branch: str = "main"
    build_recipe: BuildRecipe


class AgentRegistrationRequest(BaseModel):
    name: str
    label: str | None = None
    hostname: str
    capabilities: AgentCapabilities
    ip_address: str | None = None
    connected_probe_count: int = 0
    location_text: str | None = None
    software_version: str = "0.1.0"


class AgentRegistrationResponse(BaseModel):
    agent_id: str
    server_time: datetime


class AgentHeartbeatRequest(BaseModel):
    agent_id: str
    status: AgentStatus
    ip_address: str | None = None
    connected_probe_count: int = 0
    active_session_id: str | None = None
    latest_time_sample: TimeSyncSample | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class AgentHeartbeatResponse(BaseModel):
    server_time: datetime
    status: str = "ok"


class AgentPollRequest(BaseModel):
    agent_id: str
    status: AgentStatus


class JobEnvelope(BaseModel):
    id: str
    agent_id: str
    session_id: str | None = None
    role: Role | None = None
    type: JobType
    state: JobState
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentPollResponse(BaseModel):
    server_time: datetime
    job: JobEnvelope | None = None


class BuildArtifactPayload(BaseModel):
    artifact_id: str | None = None
    session_id: str
    role_hint: Role | None = None
    repo: ConfiguredRepo
    git_sha: str


class PrepareRolePayload(BaseModel):
    session_id: str
    role_run_id: str
    role: Role
    artifact_id: str
    artifact_download_url: str
    capture_duration_seconds: int | None = None
    stop_mode: StopMode


class StartCapturePayload(BaseModel):
    session_id: str
    role_run_id: str
    role: Role
    planned_start_at: datetime
    duration_seconds: int | None = None
    stop_mode: StopMode


class StopCapturePayload(BaseModel):
    session_id: str
    role_run_id: str
    role: Role
    reason: str = "manual_stop"


class JobResult(BaseModel):
    success: bool
    failure_reason: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    artifact_id: str | None = None
    state_hint: RoleRunState | None = None
    uploaded_raw_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    time_samples: list[TimeSyncSample] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    name: str
    stop_mode: StopMode = StopMode.DEFAULT_DURATION
    selected_duration_minutes: int | None = None
    initial_notes: str | None = None
    location_mode: LocationMode = LocationMode.NONE
    location_text: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None


class SessionUpdateRequest(BaseModel):
    name: str | None = None
    initial_notes: str | None = None
    final_notes: str | None = None
    location_mode: LocationMode | None = None
    location_text: str | None = None
    location_lat: float | None = None
    location_lon: float | None = None
    selected_duration_minutes: int | None = None
    stop_mode: StopMode | None = None


class AssignHostsRequest(BaseModel):
    tx_agent_id: str
    rx_agent_id: str


class AssignArtifactRequest(BaseModel):
    role: Role
    artifact_id: str


class BuildRequest(BaseModel):
    session_id: str
    role: Role | None = None
    repo_id: str
    git_sha: str
    build_agent_id: str


class AnnotationCreateRequest(BaseModel):
    text: str


class ArtifactSummary(BaseModel):
    id: str
    session_id: str
    status: ArtifactStatus
    origin_type: ArtifactOriginType
    source_repo: str | None = None
    git_sha: str | None = None
    producing_agent_id: str | None = None
    role_compatibility: list[Role] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RawArtifactUploadResult(BaseModel):
    raw_artifact_id: str
    storage_path: str
    sha256: str
    size_bytes: int


class ArtifactUploadResult(BaseModel):
    artifact_id: str
    storage_path: str
    sha256: str
    manifest: ArtifactBundleManifest


class SessionEventRecord(BaseModel):
    source_type: EventSourceType
    source_ref: str | None = None
    event_type: EventType
    local_timestamp: datetime | None = None
    corrected_timestamp: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ParsedEvent(BaseModel):
    role: Role | None = None
    raw_line: str
    line_number: int
    event_name: str
    level: str | None = None
    timestamp_kind: TimestampKind = TimestampKind.NONE
    host_timestamp: datetime | None = None
    relative_seconds: float | None = None
    corrected_timestamp: datetime | None = None
    packet_sequence: int | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    parse_error: str | None = None


class MergeReport(BaseModel):
    metrics: dict[str, Any] = Field(default_factory=dict)
    anomalies: list[str] = Field(default_factory=list)
    merged_events: list[ParsedEvent] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)
    correction_diagnostics: dict[str, Any] = Field(default_factory=dict)
    status: ReportStatus = ReportStatus.PENDING
