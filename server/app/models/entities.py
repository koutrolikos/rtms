from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from shared.time_sync import utc_now


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class AgentHost(Base):
    __tablename__ = "agent_hosts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hostname: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capabilities_json: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    connected_probe_count: Mapped[int] = mapped_column(Integer, default=0)
    software_version: Mapped[str] = mapped_column(String(64), default="0.1.0")
    last_reported_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stop_mode: Mapped[str] = mapped_column(String(32), default="default_duration")
    default_duration_minutes: Mapped[int] = mapped_column(Integer, default=5)
    selected_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initial_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_mode: Mapped[str] = mapped_column(String(32), default="none")
    location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rx_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tx_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rx_artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    merge_status: Mapped[str] = mapped_column(String(32), default="pending")
    report_status: Mapped[str] = mapped_column(String(32), default="pending")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    origin_type: Mapped[str] = mapped_column(String(64))
    source_repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_sha: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    producing_agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    role_compatibility_json: Mapped[list] = mapped_column(JSON, default=list)
    hash_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SessionRoleRun(Base):
    __tablename__ = "session_role_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(8), index=True)
    agent_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), default="idle", index=True)
    hidden_probe_identity: Mapped[str | None] = mapped_column(String(256), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    flash_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flash_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    flash_result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verify_result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capture_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capture_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    local_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    corrected_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)


class RawArtifact(Base):
    __tablename__ = "raw_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(Text)
    hash_sha256: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), index=True, unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    html_storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnostics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

