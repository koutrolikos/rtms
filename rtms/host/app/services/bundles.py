from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from rtms.shared.enums import ArtifactOriginType, Role
from rtms.shared.manifest import ArtifactBundleManifest, BundleFileEntry, FlashSpec
from rtms.shared.time_sync import utc_now


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_artifact_bundle(
    *,
    output_path: Path,
    session_id: str,
    artifact_id: str | None,
    origin_type: ArtifactOriginType,
    producing_host_id: str | None,
    role_hint: Role | None,
    source_repo: str | None,
    git_sha: str | None,
    files: list[tuple[Path, str]],
    flash_image_path: str | None,
    elf_path: str | None,
    rtt_symbol: str | None,
    build_metadata: dict,
) -> ArtifactBundleManifest:
    manifest = ArtifactBundleManifest(
        artifact_id=artifact_id,
        session_id=session_id,
        origin_type=origin_type,
        role_hint=role_hint,
        source_repo=source_repo,
        git_sha=git_sha,
        created_at=utc_now(),
        producing_host_id=producing_host_id,
        build_metadata=build_metadata,
        files=[
            BundleFileEntry(
                path=relative_path,
                size_bytes=path.stat().st_size,
                sha256=sha256_path(path),
                kind="payload",
            )
            for path, relative_path in files
        ],
        flash=FlashSpec(
            flash_image_path=flash_image_path,
            elf_path=elf_path,
            rtt_symbol=rtt_symbol,
            verify_required=True,
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
        for path, relative_path in files:
            archive.write(path, arcname=relative_path)
    return manifest


def create_prebuilt_elf_bundle(
    *,
    output_path: Path,
    session_id: str,
    artifact_id: str | None,
    role_hint: Role,
    elf_path: Path,
    git_sha: str | None,
    source_repo: str | None,
    producing_host_id: str | None = None,
    rtt_symbol: str | None = "_SEGGER_RTT",
    build_metadata: dict | None = None,
) -> ArtifactBundleManifest:
    relative_elf_path = f"firmware/{elf_path.name}"
    return create_artifact_bundle(
        output_path=output_path,
        session_id=session_id,
        artifact_id=artifact_id,
        origin_type=ArtifactOriginType.MANUAL_UPLOAD,
        producing_host_id=producing_host_id,
        role_hint=role_hint,
        source_repo=source_repo,
        git_sha=git_sha,
        files=[(elf_path, relative_elf_path)],
        flash_image_path=relative_elf_path,
        elf_path=relative_elf_path,
        rtt_symbol=rtt_symbol,
        build_metadata=build_metadata or {},
    )


def extract_bundle(bundle_path: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(bundle_path, "r") as archive:
        archive.extractall(destination)
    return destination


def load_manifest(extracted_dir: Path) -> ArtifactBundleManifest:
    return ArtifactBundleManifest.model_validate_json(
        (extracted_dir / "manifest.json").read_text(encoding="utf-8")
    )
