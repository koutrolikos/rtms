from enum import StrEnum


class Role(StrEnum):
    TX = "TX"
    RX = "RX"


class AgentStatus(StrEnum):
    OFFLINE = "offline"
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"


class SessionState(StrEnum):
    DRAFT = "draft"
    SELECTING_ARTIFACTS = "selecting_artifacts"
    AWAITING_HOSTS = "awaiting_hosts"
    BUILDING_ARTIFACTS = "building_artifacts"
    DISTRIBUTING_ARTIFACTS = "distributing_artifacts"
    PREPARING_ROLES = "preparing_roles"
    READY_TO_CAPTURE = "ready_to_capture"
    CAPTURING = "capturing"
    MERGING = "merging"
    REPORT_READY = "report_ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    BUILD_ARTIFACT = "build_artifact"
    PREPARE_ROLE = "prepare_role"
    START_CAPTURE = "start_capture"
    STOP_CAPTURE = "stop_capture"


class RoleRunState(StrEnum):
    IDLE = "idle"
    ASSIGNED = "assigned"
    ARTIFACT_PENDING = "artifact_pending"
    ARTIFACT_READY = "artifact_ready"
    FLASHING = "flashing"
    FLASH_VERIFIED = "flash_verified"
    PREPARE_CAPTURE = "prepare_capture"
    CAPTURE_READY = "capture_ready"
    CAPTURING = "capturing"
    COMPLETED = "completed"
    FAILED = "failed"


class StopMode(StrEnum):
    DEFAULT_DURATION = "default_duration"
    FIXED_DURATION = "fixed_duration"
    MANUAL = "manual"


class LocationMode(StrEnum):
    MANUAL = "manual"
    BROWSER_GEO = "browser_geo"
    NONE = "none"


class ArtifactOriginType(StrEnum):
    GITHUB_BUILD = "github_build"
    LOCAL_AGENT_BUILD = "local_agent_build"
    MANUAL_UPLOAD = "manual_upload"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class RawArtifactType(StrEnum):
    RTT_LOG = "rtt_log"
    RTT_MACHINE_LOG = "rtt_machine_log"
    OPENOCD_LOG = "openocd_log"
    AGENT_EVENT_LOG = "agent_event_log"
    TIMING_SAMPLES = "timing_samples"
    BUILD_LOG = "build_log"
    PARSER_OUTPUT = "parser_output"
    OTHER = "other"


class ReportStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class EventSourceType(StrEnum):
    SERVER = "server"
    OPERATOR = "operator"
    AGENT = "agent"
    TX = "tx"
    RX = "rx"


class EventType(StrEnum):
    STATE_CHANGE = "state_change"
    JOB_UPDATE = "job_update"
    ANNOTATION = "annotation"
    CAPTURE = "capture"
    UPLOAD = "upload"
    DIAGNOSTIC = "diagnostic"


class TimestampKind(StrEnum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"
    NONE = "none"
