from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from rtms.shared.enums import (
    HostStatus,
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
from rtms.shared.manifest import ArtifactBundleManifest
from rtms.shared.time_sync import TimeSyncSample


class HostCapabilities(BaseModel):
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
    local_checkout_path: str | None = None
    build_recipe: BuildRecipe


class HighAltitudeCCExclusionMask(BaseModel):
    center_hz: int = Field(ge=0)
    half_bw_hz: int = Field(ge=0)


class HighAltitudeCCChannelSelectionConfig(BaseModel):
    allowlist_hz: list[int] = Field(min_length=1, max_length=2)
    band_min_hz: int
    band_max_hz: int
    our_half_bw_hz: int
    guard_band_hz: int
    exclusion_masks: list[HighAltitudeCCExclusionMask] = Field(default_factory=list, max_length=4)
    backup_failover_holdoff_ms: int


class HighAltitudeCCBuildConfig(BaseModel):
    machine_log_detail: int = Field(ge=0, le=1)
    machine_log_stat_period_ms: int = Field(ge=0)


class IntegerChoice(BaseModel):
    value: int
    label: str


class HighAltitudeCCBuildConfigConstraints(BaseModel):
    machine_log_detail_options: list[IntegerChoice] = Field(
        default_factory=lambda: [
            IntegerChoice(value=0, label="Summary"),
            IntegerChoice(value=1, label="Packet"),
        ]
    )
    machine_log_stat_period_ms_min: int = 0


class RepoBuildConfigResponse(BaseModel):
    repo_id: str
    git_sha: str
    build_config: HighAltitudeCCBuildConfig
    constraints: HighAltitudeCCBuildConfigConstraints


class HostRegistrationRequest(BaseModel):
    name: str
    label: str | None = None
    hostname: str
    capabilities: HostCapabilities
    ip_address: str | None = None
    connected_probe_count: int = 0
    location_text: str | None = None
    software_version: str = "0.1.0"


class HostRegistrationResponse(BaseModel):
    host_id: str
    server_time: datetime


class HostHeartbeatRequest(BaseModel):
    host_id: str
    status: HostStatus
    ip_address: str | None = None
    connected_probe_count: int = 0
    active_session_id: str | None = None
    latest_time_sample: TimeSyncSample | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class HostHeartbeatResponse(BaseModel):
    server_time: datetime
    status: str = "ok"


class HostPollRequest(BaseModel):
    host_id: str
    status: HostStatus


class JobEnvelope(BaseModel):
    id: str
    host_id: str
    session_id: str | None = None
    role: Role | None = None
    type: JobType
    state: JobState
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class HostPollResponse(BaseModel):
    server_time: datetime
    job: JobEnvelope | None = None


class BuildArtifactPayload(BaseModel):
    artifact_id: str | None = None
    session_id: str
    role_hint: Role | None = None
    repo: ConfiguredRepo
    git_sha: str
    build_config: HighAltitudeCCBuildConfig | None = None


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
    tx_host_id: str
    rx_host_id: str


class AssignArtifactRequest(BaseModel):
    role: Role
    artifact_id: str


class BuildRequest(BaseModel):
    session_id: str
    role: Role
    repo_id: str
    git_sha: str
    build_host_id: str
    build_config: HighAltitudeCCBuildConfig | None = None


class AnnotationCreateRequest(BaseModel):
    text: str


class ArtifactSummary(BaseModel):
    id: str
    session_id: str
    status: ArtifactStatus
    origin_type: ArtifactOriginType
    source_repo: str | None = None
    git_sha: str | None = None
    producing_host_id: str | None = None
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


class ReportAnnotation(BaseModel):
    created_at: datetime
    text: str
    author: str | None = None


class MachineDecodeDiagnostic(BaseModel):
    role: Role | None = None
    artifact_id: str | None = None
    artifact_path: str | None = None
    offset: int | None = None
    code: str
    message: str


class MachineFrameBase(BaseModel):
    role: Role
    role_code: int
    kind: str
    kind_code: int
    t_ms: int = Field(ge=0)
    version: int = 1
    flags: int = 0
    payload_len: int = Field(ge=0)
    offset: int = Field(ge=0)


class MachineRunFrame(MachineFrameBase):
    machine_detail: str
    machine_detail_code: int
    build: str
    build_code: int
    channel_state: str
    channel_state_code: int
    human_log_level: int
    human_log_enable: bool
    machine_log_enable: bool
    active_slot: int
    active_freq_hz: int
    backup_slot: int
    backup_freq_hz: int
    rf_bitrate_bps: int
    machine_log_stat_period_ms: int
    airtime_limit_us: int | None = None
    telem_gps_period_ms: int | None = None
    telem_imu_baro_period_ms: int | None = None
    tx_complete_timeout_ms: int | None = None
    rx_thresh_enable: bool | None = None
    rx_min_rssi_dbm: int | None = None
    rx_min_lqi: int | None = None
    rx_poll_interval_ms: int | None = None
    rx_host_bridge_budget_count: int | None = None


class MachineStatFrame(MachineFrameBase):
    attempt_count: int | None = None
    queued_count: int | None = None
    completed_count: int | None = None
    gps_queued_count: int | None = None
    gps_completed_count: int | None = None
    imu_baro_queued_count: int | None = None
    imu_baro_completed_count: int | None = None
    other_queued_count: int | None = None
    other_completed_count: int | None = None
    busy_count: int | None = None
    airtime_reject_count: int | None = None
    send_fail_count: int | None = None
    timeout_count: int | None = None
    max_complete_latency_ms: int | None = None
    last_complete_latency_ms: int | None = None
    max_schedule_lag_ms: int | None = None
    airtime_used_us: int | None = None
    airtime_limit_us: int | None = None
    rx_ok_count: int | None = None
    accepted_count: int | None = None
    rejected_count: int | None = None
    rx_crc_fail_count: int | None = None
    rx_partial_count: int | None = None
    rx_overflow_count: int | None = None
    filtered_total_count: int | None = None
    filtered_rssi_only_count: int | None = None
    filtered_lqi_only_count: int | None = None
    filtered_both_count: int | None = None
    poll_recovery_count: int | None = None
    spi_backpressure_count: int | None = None
    rx_fifo_overwrite_count: int | None = None
    rx_fifo_depth_count: int | None = None
    spi_queue_depth_count: int | None = None
    rx_fifo_hwm: int | None = None


class MachinePacketFrame(MachineFrameBase):
    stream_id: str
    stream_id_code: int
    type_id: str
    type_id_code: int
    seq: int
    length: int
    complete_latency_ms: int | None = None
    schedule_lag_ms: int | None = None
    accepted: bool | None = None
    drop_reason: str | None = None
    drop_reason_code: int | None = None
    rssi_dbm: int | None = None
    lqi: int | None = None
    crc: bool | None = None


class MachineEventFrame(MachineFrameBase):
    event_id: str
    event_id_code: int
    state: str
    state_code: int
    reason: str
    reason_code: int
    active_slot: int | None = None
    active_freq_hz: int | None = None
    backup_slot: int | None = None
    backup_freq_hz: int | None = None
    stream_id: str | None = None
    stream_id_code: int | None = None
    type_id: str | None = None
    type_id_code: int | None = None
    seq: int | None = None
    length: int | None = None
    elapsed_ms: int | None = None


class RoleMachineReport(BaseModel):
    role: Role
    machine_artifact_id: str | None = None
    machine_artifact_path: str | None = None
    machine_artifact_size_bytes: int | None = None
    run: MachineRunFrame | None = None
    stat_frames: list[MachineStatFrame] = Field(default_factory=list)
    final_stat: MachineStatFrame | None = None
    packet_frames: list[MachinePacketFrame] = Field(default_factory=list)
    event_frames: list[MachineEventFrame] = Field(default_factory=list)


class MergeReport(BaseModel):
    roles: dict[str, RoleMachineReport] = Field(default_factory=dict)
    decode_diagnostics: list[MachineDecodeDiagnostic] = Field(default_factory=list)
    annotations: list[ReportAnnotation] = Field(default_factory=list)
    session_events: list[SessionEventRecord] = Field(default_factory=list)
    status: ReportStatus = ReportStatus.PENDING
