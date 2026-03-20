from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shared.manifest import ArtifactBundleManifest
from shared.schemas import TimeSyncSample
from shared.time_sync import utc_now


class PreparedRoleContext(BaseModel):
    session_id: str
    role_run_id: str
    role: str
    artifact_id: str
    work_dir: str
    bundle_path: str
    extracted_dir: str
    manifest: ArtifactBundleManifest
    probe_serial: str | None = None
    openocd_log_path: str | None = None
    event_log_path: str | None = None
    timing_samples_path: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    latest_time_samples: list[TimeSyncSample] = Field(default_factory=list)


class LocalStateStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.context_dir = self.root / "contexts"
        self.context_dir.mkdir(parents=True, exist_ok=True)

    def context_path(self, session_id: str, role: str) -> Path:
        return self.context_dir / f"{session_id}_{role}.json"

    def save_context(self, context: PreparedRoleContext) -> None:
        self.context_path(context.session_id, context.role).write_text(
            context.model_dump_json(indent=2), encoding="utf-8"
        )

    def load_context(self, session_id: str, role: str) -> PreparedRoleContext | None:
        path = self.context_path(session_id, role)
        if not path.exists():
            return None
        return PreparedRoleContext.model_validate_json(path.read_text(encoding="utf-8"))

    def append_event(self, context: PreparedRoleContext, event_name: str, payload: dict[str, Any]) -> None:
        if context.event_log_path is None:
            return
        path = Path(context.event_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": utc_now().isoformat(),
            "event": event_name,
            **payload,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def write_timing_samples(self, context: PreparedRoleContext) -> None:
        if context.timing_samples_path is None:
            return
        path = Path(context.timing_samples_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([sample.model_dump(mode="json") for sample in context.latest_time_samples], indent=2),
            encoding="utf-8",
        )

