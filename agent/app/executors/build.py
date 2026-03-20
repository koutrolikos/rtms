from __future__ import annotations

import glob
import logging
import os
import shlex
import subprocess
from pathlib import Path

from agent.app.core.config import AgentSettings
from agent.app.services.api_client import ServerClient
from agent.app.services.bundles import create_artifact_bundle
from shared.enums import ArtifactOriginType, Role
from shared.schemas import BuildArtifactPayload, JobResult

logger = logging.getLogger(__name__)


class BuildFailure(RuntimeError):
    def __init__(self, reason: str, diagnostics: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


class BuildExecutor:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def run_build(self, payload: BuildArtifactPayload, *, client: ServerClient, agent_id: str) -> JobResult:
        return self._run_build(payload, client=client, agent_id=agent_id, origin_type=ArtifactOriginType.GITHUB_BUILD)

    def run_local_build_upload(
        self, payload: BuildArtifactPayload, *, client: ServerClient, agent_id: str
    ) -> JobResult:
        return self._run_build(
            payload,
            client=client,
            agent_id=agent_id,
            origin_type=ArtifactOriginType.LOCAL_AGENT_BUILD,
        )

    def _run_build(
        self,
        payload: BuildArtifactPayload,
        *,
        client: ServerClient,
        agent_id: str,
        origin_type: ArtifactOriginType,
    ) -> JobResult:
        repo_root = self.settings.repo_workspace_root / payload.repo.id
        try:
            self._checkout(repo_root, payload.repo.clone_url, payload.git_sha)
            build_dir = repo_root / payload.repo.build_recipe.checkout_subdir
            output_dir = self.settings.build_root / payload.session_id / payload.artifact_id
            output_dir.mkdir(parents=True, exist_ok=True)
            build_log_path = output_dir / "build.log"
            self._run_command(
                payload.repo.build_recipe.build_command,
                cwd=build_dir,
                env=payload.repo.build_recipe.env,
                timeout_seconds=payload.repo.build_recipe.timeout_seconds,
                log_path=build_log_path,
            )
            file_entries = self._collect_files(repo_root, payload)
            bundle_path = output_dir / "bundle.zip"
            manifest = create_artifact_bundle(
                output_path=bundle_path,
                session_id=payload.session_id,
                artifact_id=payload.artifact_id,
                origin_type=origin_type,
                producing_agent_id=agent_id,
                role_hint=payload.role_hint,
                source_repo=payload.repo.full_name,
                git_sha=payload.git_sha,
                files=file_entries["files"],
                flash_image_path=file_entries["flash_image"],
                elf_path=file_entries["elf_path"],
                rtt_symbol=payload.repo.build_recipe.rtt_symbol,
                build_metadata={
                    "repo_id": payload.repo.id,
                    "build_log": str(build_log_path),
                },
            )
            upload = client.upload_artifact_bundle(
                bundle_path=bundle_path,
                session_id=payload.session_id,
                artifact_id=payload.artifact_id,
                origin_type=origin_type,
                producing_agent_id=agent_id,
                role_hint=payload.role_hint,
                source_repo=payload.repo.full_name,
                git_sha=payload.git_sha,
            )
            return JobResult(
                success=True,
                artifact_id=upload.artifact_id,
                diagnostics={
                    "bundle_path": str(bundle_path),
                    "manifest": manifest.model_dump(mode="json"),
                    "build_log_path": str(build_log_path),
                },
            )
        except BuildFailure as exc:
            return JobResult(success=False, failure_reason=exc.reason, diagnostics=exc.diagnostics)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return JobResult(
                success=False,
                failure_reason="upload_failed",
                diagnostics={"error": str(exc), "artifact_id": payload.artifact_id},
            )

    def _checkout(self, repo_root: Path, clone_url: str, git_sha: str) -> None:
        repo_root.parent.mkdir(parents=True, exist_ok=True)
        if not (repo_root / ".git").exists():
            self._run_command(f"{self.settings.git_bin} clone {shlex.quote(clone_url)} {shlex.quote(str(repo_root))}")
        self._run_command(f"{self.settings.git_bin} fetch --all --tags", cwd=repo_root)
        self._run_command(f"{self.settings.git_bin} checkout {shlex.quote(git_sha)}", cwd=repo_root)

    def _run_command(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 900,
        log_path: Path | None = None,
    ) -> None:
        merged_env = os.environ.copy()
        if self.settings.github_token:
            merged_env["GITHUB_TOKEN"] = self.settings.github_token
        if env:
            merged_env.update(env)
        logger.info("running build command: %s", command)
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd) if cwd else None,
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if log_path is not None:
            log_path.write_text(
                f"$ {command}\n\nstdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}\n",
                encoding="utf-8",
            )
        if completed.returncode != 0:
            raise BuildFailure(
                "build_failed",
                {
                    "command": command,
                    "cwd": str(cwd) if cwd else None,
                    "return_code": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                },
            )

    def _collect_files(self, repo_root: Path, payload: BuildArtifactPayload) -> dict:
        recipe = payload.repo.build_recipe
        matched_files: list[tuple[Path, str]] = []
        for pattern in recipe.artifact_globs:
            for match in glob.glob(str(repo_root / pattern), recursive=True):
                path = Path(match)
                if path.is_file():
                    matched_files.append((path, str(path.relative_to(repo_root))))
        if not matched_files:
            raise BuildFailure("artifact_files_missing", {"patterns": recipe.artifact_globs})
        elf_path = self._first_match(repo_root, recipe.elf_glob)
        flash_image = self._first_match(repo_root, recipe.flash_image_glob)
        if flash_image is None and elf_path is None:
            raise BuildFailure("flash_image_missing", {"elf_glob": recipe.elf_glob, "flash_image_glob": recipe.flash_image_glob})
        return {
            "files": matched_files,
            "elf_path": str(elf_path.relative_to(repo_root)) if elf_path else None,
            "flash_image": str(flash_image.relative_to(repo_root)) if flash_image else None,
        }

    def _first_match(self, root: Path, pattern: str | None) -> Path | None:
        if not pattern:
            return None
        matches = glob.glob(str(root / pattern), recursive=True)
        for match in matches:
            path = Path(match)
            if path.is_file():
                return path
        return None
