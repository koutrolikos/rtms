from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from rtms.host.app.core.config import HostSettings
from rtms.host.app.executors.build import BuildExecutor
from rtms.host.app.executors.capture import RunningCapture
from rtms.host.app.executors.openocd import OpenOcdExecutor
from rtms.host.app.services.api_client import ServerClient, ServerConnectionError
from rtms.host.app.services.bundles import create_prebuilt_elf_bundle
from rtms.host.app.services.probes import ProbeInventorySnapshot, scan_probe_inventory
from rtms.host.app.storage.local_state import LocalStateStore
from rtms.shared.enums import HostStatus, ArtifactOriginType, JobType, RawArtifactType, Role
from rtms.shared.schemas import (
    HostHeartbeatRequest,
    HostRegistrationRequest,
    BuildArtifactPayload,
    JobEnvelope,
    JobResult,
    PrepareRolePayload,
    StartCapturePayload,
    StopCapturePayload,
)

logger = logging.getLogger(__name__)


TERMINAL_SESSION_STATUSES = {"report_ready", "failed", "cancelled"}


class HostRuntime:
    def __init__(self, settings: HostSettings) -> None:
        self.settings = settings
        self.settings.prepare_dirs()
        self.client = ServerClient(settings.server_url, auth=settings.server_basic_auth)
        self.state_store = LocalStateStore(self.settings.data_dir / "state")
        self.build_executor = BuildExecutor(settings)
        self.openocd_executor = OpenOcdExecutor(settings, self.state_store)
        self.host_id: str | None = None
        self.running_captures: dict[str, RunningCapture] = {}
        self.last_heartbeat_at = 0.0
        self.latest_time_sample = None
        self.last_probe_scan_at = 0.0
        self.last_cleanup_sweep_at = 0.0
        self.probe_inventory = ProbeInventorySnapshot(
            connected_probes=[],
            configured_probe_serial=self.settings.openocd.probe_serial,
            selected_probe_serial=self.settings.openocd.probe_serial,
            selection_reason="probe_scan_pending",
        )

    def register(self) -> None:
        probe_inventory = self.refresh_probe_inventory(force=True)
        response = self.client.register_host(
            HostRegistrationRequest(
                name=self.settings.name,
                label=self.settings.label,
                hostname=self.settings.hostname,
                capabilities=self.settings.capabilities,
                ip_address=self.settings.ip_address,
                connected_probe_count=probe_inventory.connected_probe_count,
                software_version=self.settings.software_version,
            )
        )
        self.host_id = response.host_id
        self.sample_time_sync()
        self._cleanup_local_sessions(force=True)

    def sample_time_sync(self) -> None:
        self.latest_time_sample = self.client.sample_time_sync()

    def heartbeat_if_needed(self) -> None:
        if self.host_id is None:
            raise RuntimeError("host not registered")
        now = time.monotonic()
        if now - self.last_heartbeat_at < self.settings.heartbeat_interval_seconds:
            return
        probe_inventory = self.refresh_probe_inventory(force=True)
        self.sample_time_sync()
        self.client.heartbeat(
            HostHeartbeatRequest(
                host_id=self.host_id,
                status=self.current_status(),
                ip_address=self.settings.ip_address,
                connected_probe_count=probe_inventory.connected_probe_count,
                latest_time_sample=self.latest_time_sample,
                diagnostics={
                    "running_capture_jobs": list(self.running_captures.keys()),
                    **probe_inventory.diagnostics(),
                },
            )
        )
        self.last_heartbeat_at = now

    def current_status(self) -> HostStatus:
        if self.running_captures:
            return HostStatus.BUSY
        return HostStatus.IDLE

    def poll_once(self) -> None:
        if self.host_id is None:
            raise RuntimeError("host not registered")
        self.heartbeat_if_needed()
        self._collect_finished_captures()
        self._cleanup_local_sessions()
        response = self.client.poll(self.host_id, self.current_status())
        if response.job is None:
            return
        self.execute_job(response.job)

    def execute_job(self, job: JobEnvelope) -> None:
        logger.info("executing job %s type=%s", job.id, job.type)
        try:
            if job.type == JobType.BUILD_ARTIFACT:
                payload = BuildArtifactPayload.model_validate(job.payload)
                result = self.build_executor.run_build(payload, client=self.client, host_id=self.host_id)
                self.client.report_job_result(job.id, result)
                return
            if job.type == JobType.PREPARE_ROLE:
                payload = PrepareRolePayload.model_validate(job.payload)
                bundle_path = self.settings.downloads_root / payload.session_id / f"{payload.role.value.lower()}_{payload.artifact_id}.zip"
                self.client.download_artifact(payload.artifact_download_url, bundle_path)
                probe_inventory = self.refresh_probe_inventory(force=True)
                self.sample_time_sync()
                result = self.openocd_executor.prepare(
                    payload,
                    bundle_path=bundle_path,
                    time_samples=[self.latest_time_sample],
                    probe_inventory=probe_inventory,
                )
                if result.success:
                    self._delete_file(bundle_path)
                    try:
                        self._upload_prepare_side_effects(payload)
                    except Exception as exc:
                        result = JobResult(
                            success=False,
                            failure_reason="upload_failed",
                            diagnostics={**result.diagnostics, "error": str(exc)},
                            time_samples=result.time_samples,
                        )
                self.client.report_job_result(job.id, result)
                return
            if job.type == JobType.START_CAPTURE:
                payload = StartCapturePayload.model_validate(job.payload)
                context = self.state_store.load_context(payload.session_id, payload.role.value)
                if context is None:
                    self.client.report_job_result(
                        job.id,
                        JobResult(success=False, failure_reason="capture_failed", diagnostics={"error": "missing prepared context"}),
                    )
                    return
                self.sample_time_sync()
                context.latest_time_samples.append(self.latest_time_sample)
                self.state_store.write_timing_samples(context)
                capture = RunningCapture(
                    job_id=job.id,
                    context=context,
                    payload=payload,
                    settings=self.settings,
                    state_store=self.state_store,
                )
                self.running_captures[job.id] = capture
                capture.start()
                return
            if job.type == JobType.STOP_CAPTURE:
                payload = StopCapturePayload.model_validate(job.payload)
                stopped = False
                for capture in self.running_captures.values():
                    if (
                        capture.payload.session_id == payload.session_id
                        and capture.payload.role == payload.role
                    ):
                        capture.stop(reason=payload.reason)
                        stopped = True
                self.client.report_job_result(
                    job.id,
                    JobResult(success=stopped, failure_reason=None if stopped else "capture_not_running"),
                )
                return
            self.client.report_job_result(job.id, JobResult(success=False, failure_reason="unknown_job_type"))
        except Exception as exc:  # pragma: no cover - defensive wrapper
            logger.exception("job %s failed unexpectedly", job.id)
            self.client.report_job_result(
                job.id,
                JobResult(success=False, failure_reason="host_exception", diagnostics={"error": str(exc)}),
            )

    def _upload_prepare_side_effects(self, payload: PrepareRolePayload) -> None:
        context = self.state_store.load_context(payload.session_id, payload.role.value)
        if context is None:
            return
        for path, artifact_type in (
            (context.openocd_log_path, RawArtifactType.OPENOCD_LOG),
            (context.event_log_path, RawArtifactType.HOST_EVENT_LOG),
            (context.timing_samples_path, RawArtifactType.TIMING_SAMPLES),
        ):
            if path and Path(path).exists():
                self.client.upload_raw_artifact(
                    path=Path(path),
                    session_id=payload.session_id,
                    artifact_type=artifact_type,
                    role=payload.role,
                    metadata={"stage": "prepare"},
                )

    def _collect_finished_captures(self) -> None:
        finished: list[str] = []
        for job_id, capture in list(self.running_captures.items()):
            if not capture.done():
                continue
            result = capture.result()
            context = capture.context
            upload_error: Exception | None = None
            for path, artifact_type in self._capture_upload_targets(capture):
                if path and Path(path).exists():
                    try:
                        self.client.upload_raw_artifact(
                            path=Path(path),
                            session_id=context.session_id,
                            artifact_type=artifact_type,
                            role=Role(context.role),
                            metadata={"stage": "capture"},
                        )
                    except Exception as exc:  # pragma: no cover - defensive wrapper
                        upload_error = exc
                        break
            if upload_error is not None:
                result = JobResult(
                    success=False,
                    failure_reason="upload_failed",
                    diagnostics={"error": str(upload_error), **result.diagnostics},
                    time_samples=result.time_samples,
                )
            self.client.report_job_result(job_id, result)
            if upload_error is None:
                self._cleanup_local_role(
                    session_id=context.session_id,
                    role=Role(context.role),
                    bundle_path=Path(context.bundle_path),
                )
            finished.append(job_id)
        for job_id in finished:
            self.running_captures.pop(job_id, None)

    def _capture_upload_targets(self, capture: RunningCapture) -> list[tuple[Path | None, RawArtifactType]]:
        context = capture.context
        return [
            (capture.rtt_human_log_path, RawArtifactType.RTT_LOG),
            (capture.rtt_machine_log_path, RawArtifactType.RTT_MACHINE_LOG),
            (capture.capture_command_log_path, RawArtifactType.CAPTURE_COMMAND_LOG),
            (Path(context.event_log_path) if context.event_log_path else None, RawArtifactType.HOST_EVENT_LOG),
            (Path(context.timing_samples_path) if context.timing_samples_path else None, RawArtifactType.TIMING_SAMPLES),
        ]

    def run(self) -> None:
        logger.info(
            "host starting with server_url=%s name=%s hostname=%s",
            self.settings.server_url,
            self.settings.name,
            self.settings.hostname,
        )
        self.register()
        logger.info("host registered as %s", self.host_id)
        while True:
            self.poll_once()
            time.sleep(self.settings.poll_interval_seconds)

    def close(self) -> None:
        try:
            self._cleanup_local_sessions(force=True)
        except Exception:  # pragma: no cover - best-effort shutdown cleanup
            logger.exception("failed to clean local session state during shutdown")
        try:
            self.build_executor.cleanup_stale_build_artifacts()
        except Exception:  # pragma: no cover - best-effort shutdown cleanup
            logger.exception("failed to clean stale build artifacts during shutdown")
        self.client.close()

    def build_local_upload(
        self,
        *,
        session_id: str,
        repo_id: str,
        git_sha: str,
        role: Role | None = None,
    ) -> None:
        if self.host_id is None:
            self.register()
        repos = {repo.id: repo for repo in self.client.list_repos()}
        repo = repos[repo_id]
        payload = BuildArtifactPayload(
            artifact_id="",
            session_id=session_id,
            role_hint=role,
            repo=repo,
            git_sha=git_sha,
        )
        result = self.build_executor.run_local_build_upload(
            payload, client=self.client, host_id=self.host_id
        )
        if not result.success:
            raise RuntimeError(result.failure_reason)

    def refresh_probe_inventory(self, *, force: bool = False) -> ProbeInventorySnapshot:
        if not (self.settings.capabilities.flash_capable or self.settings.capabilities.capture_capable):
            self.probe_inventory = ProbeInventorySnapshot(
                connected_probes=[],
                configured_probe_serial=self.settings.openocd.probe_serial,
                selected_probe_serial=self.settings.openocd.probe_serial,
                selection_reason="probe_scan_skipped_for_non_hardware_host",
            )
            return self.probe_inventory
        now = time.monotonic()
        if (
            not force
            and self.last_probe_scan_at
            and (now - self.last_probe_scan_at) < self.settings.openocd.probe_scan_interval_seconds
        ):
            return self.probe_inventory
        self.probe_inventory = scan_probe_inventory(
            configured_probe_serial=self.settings.openocd.probe_serial,
            scan_enabled=self.settings.openocd.probe_scan_enabled,
        )
        self.last_probe_scan_at = now
        return self.probe_inventory

    def upload_prebuilt_artifact(
        self,
        *,
        session_id: str,
        role: Role,
        elf_path: str,
        git_sha: str | None,
        source_repo: str | None,
        rtt_symbol: str | None,
        dirty_worktree: bool,
    ) -> str:
        resolved_elf_path = Path(elf_path).expanduser().resolve()
        if not resolved_elf_path.exists():
            raise FileNotFoundError(f"prebuilt ELF not found: {resolved_elf_path}")
        if not resolved_elf_path.is_file():
            raise ValueError(f"prebuilt ELF path is not a file: {resolved_elf_path}")
        output_dir = self.settings.build_root / session_id / f"prebuilt-{role.value.lower()}"
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = output_dir / f"{role.value.lower()}-prebuilt-bundle.zip"
        manifest = create_prebuilt_elf_bundle(
            output_path=bundle_path,
            session_id=session_id,
            artifact_id=None,
            role_hint=role,
            elf_path=resolved_elf_path,
            git_sha=git_sha,
            source_repo=source_repo,
            producing_host_id=None,
            rtt_symbol=rtt_symbol,
            build_metadata={
                "artifact_kind": "prebuilt_elf",
                "source_path": str(resolved_elf_path),
                "dirty_worktree": dirty_worktree,
            },
        )
        upload = self.client.upload_artifact_bundle(
            bundle_path=bundle_path,
            session_id=session_id,
            artifact_id=None,
            origin_type=ArtifactOriginType.MANUAL_UPLOAD,
            producing_host_id=None,
            role_hint=role,
            source_repo=source_repo,
            git_sha=git_sha,
        )
        logger.info(
            "uploaded prebuilt artifact artifact_id=%s role=%s source=%s",
            upload.artifact_id,
            role.value,
            resolved_elf_path,
        )
        logger.debug("uploaded prebuilt manifest: %s", manifest.model_dump(mode="json"))
        return upload.artifact_id

    def _cleanup_local_sessions(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self.last_cleanup_sweep_at:
            interval = max(self.settings.heartbeat_interval_seconds, self.settings.poll_interval_seconds)
            if (now - self.last_cleanup_sweep_at) < interval:
                return
        self.last_cleanup_sweep_at = now
        active_sessions = {
            capture.context.session_id
            for capture in self.running_captures.values()
            if not capture.done()
        }
        for session_id in sorted(self._iter_local_session_ids()):
            if session_id in active_sessions:
                continue
            self._cleanup_local_session(session_id)

    def _iter_local_session_ids(self) -> set[str]:
        session_ids: set[str] = set()
        for root in (self.settings.sessions_root, self.settings.downloads_root):
            if not root.exists():
                continue
            for path in root.iterdir():
                if path.is_dir():
                    session_ids.add(path.name)
        if self.state_store.context_dir.exists():
            for path in self.state_store.context_dir.glob("*.json"):
                stem = path.stem
                if "_" not in stem:
                    continue
                session_id, _role = stem.rsplit("_", 1)
                session_ids.add(session_id)
        return session_ids

    def _cleanup_local_session(self, session_id: str) -> None:
        try:
            session_payload = self.client.get_session_status(session_id)
        except ServerConnectionError as exc:
            logger.warning("cleanup sweep skipped for session %s: %s", session_id, exc)
            return
        if session_payload is None:
            self._delete_local_session(session_id)
            return
        status = str(session_payload.get("status") or "")
        if status not in TERMINAL_SESSION_STATUSES:
            return
        try:
            self._upload_remaining_session_artifacts(session_id)
        except Exception as exc:  # pragma: no cover - best-effort retry path
            logger.warning("cleanup sweep upload failed for session %s: %s", session_id, exc)
            return
        self._delete_local_session(session_id)

    def _upload_remaining_session_artifacts(self, session_id: str) -> None:
        session_dir = self.settings.sessions_root / session_id
        if not session_dir.exists():
            return
        for role_dir in sorted(path for path in session_dir.iterdir() if path.is_dir()):
            role = self._role_from_dir_name(role_dir.name)
            if role is None:
                continue
            capture_stage = any(
                (role_dir / filename).exists()
                for filename in ("rtt.log", "rtt.rttbin", "capture-command.log")
            )
            for filename, artifact_type in (
                ("openocd.log", RawArtifactType.OPENOCD_LOG),
                ("host_events.jsonl", RawArtifactType.HOST_EVENT_LOG),
                ("timing_samples.json", RawArtifactType.TIMING_SAMPLES),
                ("rtt.log", RawArtifactType.RTT_LOG),
                ("rtt.rttbin", RawArtifactType.RTT_MACHINE_LOG),
                ("capture-command.log", RawArtifactType.CAPTURE_COMMAND_LOG),
            ):
                path = role_dir / filename
                if not path.exists():
                    continue
                stage = "capture" if capture_stage and artifact_type not in {RawArtifactType.OPENOCD_LOG} else "prepare"
                self.client.upload_raw_artifact(
                    path=path,
                    session_id=session_id,
                    artifact_type=artifact_type,
                    role=role,
                    metadata={"stage": stage, "source": "cleanup_sweep"},
                )

    def _cleanup_local_role(self, *, session_id: str, role: Role, bundle_path: Path | None) -> None:
        if bundle_path is not None:
            self._delete_file(bundle_path)
        session_dir = self.settings.sessions_root / session_id
        role_dir = session_dir / role.value.lower()
        self._delete_tree(role_dir)
        context_path = self.state_store.context_path(session_id, role.value)
        self._delete_file(context_path)
        downloads_dir = self.settings.downloads_root / session_id
        if downloads_dir.exists():
            for candidate in downloads_dir.glob(f"{role.value.lower()}_*.zip"):
                self._delete_file(candidate)
        self._remove_empty_dir(downloads_dir)
        self._remove_empty_dir(session_dir)

    def _delete_local_session(self, session_id: str) -> None:
        self._delete_tree(self.settings.sessions_root / session_id)
        self._delete_tree(self.settings.downloads_root / session_id)
        for context_path in self.state_store.context_dir.glob(f"{session_id}_*.json"):
            self._delete_file(context_path)

    def _delete_tree(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return
        self._delete_file(path)

    def _delete_file(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _remove_empty_dir(self, path: Path) -> None:
        current = path
        protected_roots = {
            self.settings.sessions_root,
            self.settings.downloads_root,
            self.state_store.context_dir,
            self.state_store.root,
        }
        while current.exists() and current not in protected_roots:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _role_from_dir_name(self, value: str) -> Role | None:
        try:
            return Role(value.upper())
        except ValueError:
            return None
