from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from rtms.shared.enums import ArtifactOriginType, Role


class BundleFileEntry(BaseModel):
    path: str
    size_bytes: int
    sha256: str
    kind: str


class FlashSpec(BaseModel):
    flash_image_path: str | None = None
    elf_path: str | None = None
    verify_required: bool = True
    rtt_symbol: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ArtifactBundleManifest(BaseModel):
    schema_version: str = "1"
    artifact_id: str | None = None
    session_id: str | None = None
    origin_type: ArtifactOriginType
    role_hint: Role | None = None
    source_repo: str | None = None
    git_sha: str | None = None
    created_at: datetime
    producing_host_id: str | None = None
    build_metadata: dict[str, Any] = Field(default_factory=dict)
    files: list[BundleFileEntry] = Field(default_factory=list)
    flash: FlashSpec = Field(default_factory=FlashSpec)

