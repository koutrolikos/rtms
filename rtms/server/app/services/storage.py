from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from rtms.shared.manifest import ArtifactBundleManifest


class FileStorage:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_relative_path(self, relative: str | Path) -> Path:
        raw = str(relative).replace("\\", "/")
        candidate = PurePosixPath(raw)
        if candidate.is_absolute():
            raise ValueError(f"absolute storage paths are not allowed: {relative}")
        parts = [part for part in candidate.parts if part not in ("", ".")]
        if not parts:
            raise ValueError("storage path cannot be empty")
        if any(part == ".." for part in parts):
            raise ValueError(f"storage path cannot escape base directory: {relative}")
        return Path(*parts)

    def ensure_dir(self, relative: str | Path) -> Path:
        path = self.resolve(relative)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolve(self, relative: str | Path) -> Path:
        return self.base_dir / self._normalize_relative_path(relative)

    def save_upload(self, file_obj: BinaryIO, relative_path: str | Path) -> tuple[str, str, int]:
        normalized_relative = self._normalize_relative_path(relative_path)
        destination = self.base_dir / normalized_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        size = 0
        with destination.open("wb") as handle:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
        return normalized_relative.as_posix(), hasher.hexdigest(), size

    def save_bytes(self, data: bytes, relative_path: str | Path) -> tuple[str, str, int]:
        normalized_relative = self._normalize_relative_path(relative_path)
        destination = self.base_dir / normalized_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return normalized_relative.as_posix(), digest, len(data)

    def save_text(self, text: str, relative_path: str | Path) -> tuple[str, str, int]:
        return self.save_bytes(text.encode("utf-8"), relative_path)

    def read_text(self, relative_path: str | Path) -> str:
        return self.resolve(relative_path).read_text(encoding="utf-8")

    def read_bytes(self, relative_path: str | Path) -> bytes:
        return self.resolve(relative_path).read_bytes()

    def parse_bundle_manifest(self, bundle_relative_path: str | Path) -> ArtifactBundleManifest:
        bundle_path = self.resolve(bundle_relative_path)
        with zipfile.ZipFile(bundle_path, "r") as archive:
            with archive.open("manifest.json") as handle:
                payload = json.loads(handle.read().decode("utf-8"))
        return ArtifactBundleManifest.model_validate(payload)

    def extract_bundle(self, bundle_relative_path: str | Path, destination: Path | None = None) -> Path:
        bundle_path = self.resolve(bundle_relative_path)
        extract_dir = destination or Path(tempfile.mkdtemp(prefix="artifact_bundle_"))
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "r") as archive:
            for entry in archive.infolist():
                relative_entry = self._normalize_relative_path(entry.filename)
                target = extract_dir / relative_entry
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
        return extract_dir

    def copy_into(self, source: Path, relative_path: str | Path) -> tuple[str, str, int]:
        normalized_relative = self._normalize_relative_path(relative_path)
        destination = self.base_dir / normalized_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        content = destination.read_bytes()
        return normalized_relative.as_posix(), hashlib.sha256(content).hexdigest(), destination.stat().st_size
