from __future__ import annotations

import glob
import json
import logging
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from agent.app.core.config import AgentSettings
from agent.app.services.api_client import ServerClient
from agent.app.services.bundles import create_artifact_bundle
from shared.enums import ArtifactOriginType, RawArtifactType, Role
from shared.high_altitude_cc import HIGH_ALTITUDE_CC_REPO_ID, build_high_altitude_cc_cdefs
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
        repo_context: dict | None = None
        build_log_path: Path | None = None
        checkout_subdir = payload.repo.build_recipe.checkout_subdir
        output_dir: Path | None = None
        resolved_build_command = payload.repo.build_recipe.build_command
        resolved_cdefs_extra: list[str] = []
        try:
            self._checkout(
                repo_root,
                payload.repo.clone_url,
                payload.git_sha,
                default_branch=payload.repo.default_branch,
            )
            repo_context = self._repo_context(repo_root, checkout_subdir=checkout_subdir)
            build_dir = repo_root / checkout_subdir
            if not build_dir.exists():
                raise BuildFailure(
                    "checkout_subdir_missing",
                    {
                        "checkout_subdir": checkout_subdir,
                        "repo_root": str(repo_root),
                    },
                )
            output_dir = self.settings.build_root / payload.session_id / (payload.artifact_id or "local-build")
            output_dir.mkdir(parents=True, exist_ok=True)
            build_log_path = output_dir / "build.log"
            resolved_build_command, resolved_cdefs_extra = self._resolve_build_command(payload)
            self._preflight_command_inputs(resolved_build_command, cwd=build_dir)
            self._run_command(
                resolved_build_command,
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
                    "requested_build_config": (
                        payload.build_config.model_dump(mode="json") if payload.build_config else None
                    ),
                    "resolved_build_command": resolved_build_command,
                    "resolved_cdefs_extra": resolved_cdefs_extra,
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
            diagnostics = {
                "bundle_path": str(bundle_path),
                "manifest": manifest.model_dump(mode="json"),
                "repo_context": repo_context
                or self._repo_context(repo_root, checkout_subdir=checkout_subdir),
                "resolved_build_command": resolved_build_command,
                "resolved_cdefs_extra": resolved_cdefs_extra,
            }
            uploaded_raw_artifacts: list[dict] = []
            if build_log_path.exists():
                try:
                    raw_upload = client.upload_raw_artifact(
                        path=build_log_path,
                        session_id=payload.session_id,
                        artifact_type=RawArtifactType.BUILD_LOG,
                        role=payload.role_hint,
                        metadata={
                            "artifact_id": upload.artifact_id,
                            "git_sha": payload.git_sha,
                            "repo_id": payload.repo.id,
                            "stage": "build",
                        },
                    )
                    uploaded_raw_artifacts.append(
                        {
                            "raw_artifact_id": raw_upload.raw_artifact_id,
                            "storage_path": raw_upload.storage_path,
                            "type": RawArtifactType.BUILD_LOG.value,
                        }
                    )
                except Exception as exc:  # pragma: no cover - best-effort side effect
                    diagnostics["build_log_upload_error"] = str(exc)
            try:
                diagnostics["cleanup_paths"] = self._cleanup_success_paths(repo_root, output_dir)
            except Exception as exc:  # pragma: no cover - best-effort side effect
                diagnostics["cleanup_error"] = str(exc)
            return JobResult(
                success=True,
                artifact_id=upload.artifact_id,
                diagnostics=diagnostics,
                uploaded_raw_artifacts=uploaded_raw_artifacts,
            )
        except BuildFailure as exc:
            diagnostics = dict(exc.diagnostics)
            diagnostics.setdefault(
                "repo_context",
                repo_context or self._repo_context(repo_root, checkout_subdir=checkout_subdir),
            )
            if build_log_path is not None:
                diagnostics.setdefault("build_log_path", str(build_log_path))
            return JobResult(success=False, failure_reason=exc.reason, diagnostics=diagnostics)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return JobResult(
                success=False,
                failure_reason="upload_failed",
                diagnostics={
                    "error": str(exc),
                    "artifact_id": payload.artifact_id,
                    "repo_context": repo_context
                    or self._repo_context(repo_root, checkout_subdir=checkout_subdir),
                    **({"build_log_path": str(build_log_path)} if build_log_path is not None else {}),
                },
            )

    def _resolve_build_command(self, payload: BuildArtifactPayload) -> tuple[str, list[str]]:
        command = payload.repo.build_recipe.build_command
        if payload.repo.id != HIGH_ALTITUDE_CC_REPO_ID:
            return command, []
        if payload.role_hint is None or payload.build_config is None:
            raise BuildFailure(
                "missing_build_config",
                {
                    "repo_id": payload.repo.id,
                    "role_hint": payload.role_hint.value if payload.role_hint else None,
                    "has_build_config": payload.build_config is not None,
                },
            )
        cdefs_extra = build_high_altitude_cc_cdefs(payload.role_hint, payload.build_config)
        role = payload.role_hint.value.lower()
        build_config_json = json.dumps(payload.build_config.model_dump(mode="json"), separators=(",", ":"))
        return (
            " ".join(
                [
                    "range-test-agent",
                    "build-high-altitude-cc",
                    "--source",
                    shlex.quote("."),
                    "--build-dir",
                    shlex.quote("build/debug"),
                    "--role",
                    shlex.quote(role),
                    "--build-config-json",
                    shlex.quote(build_config_json),
                ]
            ),
            cdefs_extra,
        )

    def _cleanup_success_paths(self, repo_root: Path, output_dir: Path) -> list[str]:
        removed: list[str] = []
        for path in (repo_root, output_dir):
            if not path.exists():
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))
        return removed

    def cleanup_stale_build_artifacts(self) -> list[str]:
        removed: list[str] = []
        for root in (self.settings.repo_workspace_root, self.settings.build_root):
            if not root.exists():
                continue
            for path in root.iterdir():
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                removed.append(str(path))
        return removed

    def _checkout(
        self,
        repo_root: Path,
        clone_url: str,
        git_sha: str,
        *,
        default_branch: str | None = None,
    ) -> None:
        repo_root.parent.mkdir(parents=True, exist_ok=True)
        archived_repo_root = self._prepare_repo_root(repo_root)
        if archived_repo_root is not None:
            logger.warning(
                "moved stale repo workspace %s to %s before recloning",
                repo_root,
                archived_repo_root,
            )
        authed_clone_url = self._clone_url_with_auth(clone_url)
        if not (repo_root / ".git").exists():
            branch_arg = f" --branch {shlex.quote(default_branch)}" if default_branch else ""
            self._run_command(
                (
                    f"{self.settings.git_bin} clone{branch_arg} "
                    f"{shlex.quote(authed_clone_url)} {shlex.quote(str(repo_root))}"
                ),
                display_command=(
                    f"{self.settings.git_bin} clone{branch_arg} "
                    f"{shlex.quote(self._redact_clone_url(authed_clone_url))} "
                    f"{shlex.quote(str(repo_root))}"
                ),
            )
        if authed_clone_url != clone_url:
            self._set_origin_url(
                repo_root,
                authed_clone_url,
                display_url=self._redact_clone_url(authed_clone_url),
            )
        try:
            self._run_command(f"{self.settings.git_bin} fetch --all --tags", cwd=repo_root)
            if default_branch:
                self._run_command(
                    f"{self.settings.git_bin} fetch origin {shlex.quote(default_branch)} --tags",
                    cwd=repo_root,
                )
                self._run_command(
                    f"{self.settings.git_bin} checkout {shlex.quote(default_branch)}",
                    cwd=repo_root,
                )
                self._run_command(
                    f"{self.settings.git_bin} reset --hard origin/{shlex.quote(default_branch)}",
                    cwd=repo_root,
                )
            self._run_command(f"{self.settings.git_bin} clean -fdx", cwd=repo_root)
            self._run_command(f"{self.settings.git_bin} checkout {shlex.quote(git_sha)}", cwd=repo_root)
        finally:
            if authed_clone_url != clone_url and (repo_root / ".git").exists():
                self._set_origin_url(repo_root, clone_url)

    def _prepare_repo_root(self, repo_root: Path) -> Path | None:
        if not repo_root.exists():
            return None
        if (repo_root / ".git").exists():
            return None
        if repo_root.is_dir() and not any(repo_root.iterdir()):
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived_repo_root = repo_root.with_name(f"{repo_root.name}.stale-{timestamp}")
        suffix = 1
        while archived_repo_root.exists():
            archived_repo_root = repo_root.with_name(
                f"{repo_root.name}.stale-{timestamp}-{suffix}"
            )
            suffix += 1
        shutil.move(str(repo_root), str(archived_repo_root))
        return archived_repo_root

    def _clone_url_with_auth(self, clone_url: str) -> str:
        if not self.settings.github_token:
            return clone_url
        parsed = urlsplit(clone_url)
        if parsed.scheme not in {"http", "https"}:
            return clone_url
        if parsed.username or parsed.password:
            return clone_url
        host = parsed.hostname or ""
        netloc = f"x-access-token:{quote(self.settings.github_token, safe='')}@{host}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    def _set_origin_url(self, repo_root: Path, clone_url: str, *, display_url: str | None = None) -> None:
        shown_url = display_url or clone_url
        self._run_command(
            f"{self.settings.git_bin} remote set-url origin {shlex.quote(clone_url)}",
            cwd=repo_root,
            display_command=f"{self.settings.git_bin} remote set-url origin {shlex.quote(shown_url)}",
        )

    def _redact_clone_url(self, clone_url: str) -> str:
        parsed = urlsplit(clone_url)
        if not parsed.password and not parsed.username:
            return clone_url
        host = parsed.hostname or ""
        netloc = f"{parsed.username or 'user'}:***@{host}"
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    def _repo_context(self, repo_root: Path, *, checkout_subdir: str = ".") -> dict:
        checkout_root = repo_root / checkout_subdir
        context = {
            "repo_root": str(repo_root),
            "exists": repo_root.exists(),
            "top_level_entries": [],
            "head_sha": None,
            "checkout_subdir": checkout_subdir,
            "checkout_root": str(checkout_root),
            "checkout_subdir_exists": checkout_root.exists(),
            "checkout_subdir_entries": [],
        }
        if not repo_root.exists():
            return context
        try:
            context["top_level_entries"] = sorted(item.name for item in repo_root.iterdir())[:50]
        except Exception:
            pass
        if checkout_root.exists() and checkout_root.is_dir():
            try:
                context["checkout_subdir_entries"] = sorted(
                    item.name for item in checkout_root.iterdir()
                )[:50]
            except Exception:
                pass
        if (repo_root / ".git").exists():
            try:
                completed = subprocess.run(
                    [self.settings.git_bin, "rev-parse", "HEAD"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                if completed.returncode == 0:
                    context["head_sha"] = completed.stdout.strip()
            except Exception:
                pass
        return context

    def _preflight_command_inputs(self, command: str, *, cwd: Path) -> None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return
        first_segment: list[str] = []
        for token in tokens:
            if token in {"&&", "||", ";", "|"}:
                break
            first_segment.append(token)
        if not first_segment:
            return

        referenced_inputs: list[dict[str, str]] = []
        missing_inputs: list[dict[str, str]] = []

        def record_required_input(kind: str, raw_path: str) -> None:
            resolved = Path(raw_path)
            if not resolved.is_absolute():
                resolved = cwd / resolved
            entry = {
                "kind": kind,
                "path": raw_path,
                "resolved_path": str(resolved),
            }
            referenced_inputs.append(entry)
            if not resolved.exists():
                missing_inputs.append(entry)

        executable = Path(first_segment[0]).name
        index = 1
        if executable in {"make", "gmake"}:
            while index < len(first_segment):
                token = first_segment[index]
                if token in {"-f", "--file"} and index + 1 < len(first_segment):
                    record_required_input("makefile", first_segment[index + 1])
                    index += 2
                    continue
                if token.startswith("-f") and token not in {"-f", "--file"}:
                    record_required_input("makefile", token[2:])
                elif token in {"-C", "--directory"} and index + 1 < len(first_segment):
                    record_required_input("working_dir", first_segment[index + 1])
                    index += 2
                    continue
                elif token.startswith("-C") and token not in {"-C", "--directory"}:
                    record_required_input("working_dir", token[2:])
                index += 1
        elif executable == "cmake":
            while index < len(first_segment):
                token = first_segment[index]
                if token in {"-S", "--source"} and index + 1 < len(first_segment):
                    record_required_input("source_dir", first_segment[index + 1])
                    index += 2
                    continue
                if token == "--build" and index + 1 < len(first_segment):
                    record_required_input("build_dir", first_segment[index + 1])
                    index += 2
                    continue
                index += 1

        if missing_inputs:
            raise BuildFailure(
                "build_inputs_missing",
                {
                    "command": command,
                    "cwd": str(cwd),
                    "missing_inputs": missing_inputs,
                    "referenced_inputs": referenced_inputs,
                },
            )

    def _run_command(
        self,
        command: str,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 900,
        log_path: Path | None = None,
        display_command: str | None = None,
    ) -> None:
        merged_env = os.environ.copy()
        if self.settings.github_token:
            merged_env["GITHUB_TOKEN"] = self.settings.github_token
        if env:
            merged_env.update(env)
        shown_command = display_command or command
        logger.info("running build command: %s", shown_command)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd) if cwd else None,
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(
                "utf-8", errors="replace"
            )
            stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(
                "utf-8", errors="replace"
            )
            if log_path is not None:
                log_path.write_text(
                    f"$ {shown_command}\n\nstdout:\n{stdout}\n\nstderr:\n{stderr}\n",
                    encoding="utf-8",
                )
            raise BuildFailure(
                "command_timed_out",
                {
                    "command": shown_command,
                    "cwd": str(cwd) if cwd else None,
                    "timeout_seconds": timeout_seconds,
                    "stdout": stdout[-4000:],
                    "stderr": stderr[-4000:],
                },
            ) from exc
        if log_path is not None:
            log_path.write_text(
                f"$ {shown_command}\n\nstdout:\n{stdout}\n\nstderr:\n{stderr}\n",
                encoding="utf-8",
            )
        if completed.returncode != 0:
            raise BuildFailure(
                "build_failed",
                {
                    "command": shown_command,
                    "cwd": str(cwd) if cwd else None,
                    "return_code": completed.returncode,
                    "stdout": stdout[-4000:],
                    "stderr": stderr[-4000:],
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
