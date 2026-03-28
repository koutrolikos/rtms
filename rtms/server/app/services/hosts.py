from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from rtms.server.app.core.config import ServerSettings
from rtms.server.app.models.entities import Host
from rtms.shared.enums import HostStatus
from rtms.shared.schemas import HostHeartbeatRequest, HostRegistrationRequest
from rtms.shared.time_sync import utc_now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def register_host(db: Session, request: HostRegistrationRequest) -> Host:
    host = db.query(Host).filter(Host.name == request.name).one_or_none()
    if host is None:
        host = Host(
            name=request.name,
            label=request.label,
            hostname=request.hostname,
            status=HostStatus.IDLE.value,
            ip_address=request.ip_address,
            capabilities=request.capabilities.model_dump(),
            connected_probe_count=request.connected_probe_count,
            last_reported_location=request.location_text,
            software_version=request.software_version,
        )
        db.add(host)
    else:
        host.label = request.label
        host.hostname = request.hostname
        host.ip_address = request.ip_address
        host.capabilities = request.capabilities.model_dump()
        host.connected_probe_count = request.connected_probe_count
        host.last_reported_location = request.location_text
        host.software_version = request.software_version
        host.status = HostStatus.IDLE.value
    host.last_seen_at = utc_now()
    db.commit()
    db.refresh(host)
    return host


def heartbeat_host(db: Session, request: HostHeartbeatRequest) -> Host:
    host = db.get(Host, request.host_id)
    if host is None:
        raise ValueError(f"unknown host {request.host_id}")
    host.status = request.status.value
    host.ip_address = request.ip_address
    host.connected_probe_count = request.connected_probe_count
    diagnostics = dict(host.diagnostics or {})
    diagnostics["active_session_id"] = request.active_session_id
    diagnostics["heartbeat"] = request.diagnostics
    if request.latest_time_sample is not None:
        diagnostics["latest_time_sample"] = request.latest_time_sample.model_dump(mode="json")
    host.diagnostics = diagnostics
    host.last_seen_at = utc_now()
    db.commit()
    db.refresh(host)
    return host


def visible_host_status(host: Host, settings: ServerSettings) -> str:
    age = (utc_now() - _as_utc(host.last_seen_at)).total_seconds()
    if age > settings.host_offline_seconds:
        return HostStatus.OFFLINE.value
    return host.status
