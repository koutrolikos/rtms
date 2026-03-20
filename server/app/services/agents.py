from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from server.app.core.config import ServerSettings
from server.app.models.entities import AgentHost
from shared.enums import AgentStatus
from shared.schemas import AgentHeartbeatRequest, AgentRegistrationRequest
from shared.time_sync import utc_now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def register_agent(db: Session, request: AgentRegistrationRequest) -> AgentHost:
    agent = db.query(AgentHost).filter(AgentHost.name == request.name).one_or_none()
    if agent is None:
        agent = AgentHost(
            name=request.name,
            label=request.label,
            hostname=request.hostname,
            status=AgentStatus.IDLE.value,
            ip_address=request.ip_address,
            capabilities_json=request.capabilities.model_dump(),
            connected_probe_count=request.connected_probe_count,
            last_reported_location=request.location_text,
            software_version=request.software_version,
        )
        db.add(agent)
    else:
        agent.label = request.label
        agent.hostname = request.hostname
        agent.ip_address = request.ip_address
        agent.capabilities_json = request.capabilities.model_dump()
        agent.connected_probe_count = request.connected_probe_count
        agent.last_reported_location = request.location_text
        agent.software_version = request.software_version
        agent.status = AgentStatus.IDLE.value
    agent.last_seen_at = utc_now()
    db.commit()
    db.refresh(agent)
    return agent


def heartbeat_agent(db: Session, request: AgentHeartbeatRequest) -> AgentHost:
    agent = db.get(AgentHost, request.agent_id)
    if agent is None:
        raise ValueError(f"unknown agent {request.agent_id}")
    agent.status = request.status.value
    agent.ip_address = request.ip_address
    agent.connected_probe_count = request.connected_probe_count
    diagnostics = dict(agent.diagnostics_json or {})
    diagnostics["active_session_id"] = request.active_session_id
    diagnostics["heartbeat"] = request.diagnostics
    if request.latest_time_sample is not None:
        diagnostics["latest_time_sample"] = request.latest_time_sample.model_dump(mode="json")
    agent.diagnostics_json = diagnostics
    agent.last_seen_at = utc_now()
    db.commit()
    db.refresh(agent)
    return agent


def visible_agent_status(agent: AgentHost, settings: ServerSettings) -> str:
    age = (utc_now() - _as_utc(agent.last_seen_at)).total_seconds()
    if age > settings.agent_offline_seconds:
        return AgentStatus.OFFLINE.value
    return agent.status
