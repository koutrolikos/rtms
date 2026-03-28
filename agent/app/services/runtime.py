from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from agent.app.core.config import AgentSettings
from agent.app.executors.build import BuildExecutor
from agent.app.executors.capture import RunningCapture
from agent.app.executors.openocd import OpenOcdExecutor
from agent.app.services.api_client import ServerClient
from agent.app.services.bundles import create_prebuilt_elf_bundle
from agent.app.services.probes import ProbeInventorySnapshot, scan_probe_inventory
from agent.app.storage.local_state import LocalStateStore
from shared.enums import AgentStatus, ArtifactOriginType, JobType, RawArtifactType, Role
from shared.schemas import (
    AgentHeartbeatRequest,
    AgentRegistrationRequest,
    BuildArtifactPayload,
    JobEnvelope,
    JobResult,
    PrepareRolePayload,
    StartCapturePayload,
    StopCapturePayload,
)

logger = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings
        self.settings.prepare_dirs()
        self.client = ServerClient(settings.server_url, auth=settings.server_basic_auth)
        self.state_store = LocalStateStore(self.settings.data_dir / "state")
        self.build_executor = BuildExecutor(settings)
        self.openocd_executor = OpenOcdExecutor(settings, self.state_store)
        self.agent_id: str | None = None
        self.running_captures: dict[str, RunningCapture] = {}
        self.last_heartbeat_at = 0.0
        self.latest_time_sample = None
        self.last_probe_scan_at = 0.0
        self.probe_inventory = ProbeInventorySnapshot(
            connected_probes=[],
            configured_probe_serial=self.settings.openocd.probe_serial,
            selected_probe_serial=self.settings.openocd.probe_serial,
            selection_reason="probe_scan_pending",
        )

    def register(self) -> None:
        probe_inventory = self.refresh_probe_inventory(force=True)
        response = self.client.register_agent(
            AgentRegistrationRequest(
                name=self.settings.name,
                label=self.settings.label,
                hostname=self.settings.hostname,
                capabilities=self.settings.capabilities,
                ip_address=self.settings.ip_address,
                connected_probe_count=probe_inventory.connected_probe_count,
                software_version=self.settings.software_version,
            )
        )
        self.agent_id = response.agent_id
        self.sample_time_sync()

    def sample_time_sync(self) -> None:
        self.latest_time_sample = self.client.sample_time_sync()

    def heartbeat_if_needed(self) -> None:
        if self.agent_id is None:
            raise RuntimeError("agent not registered")
        now = time.monotonic()
        if now - self.last_heartbeat_at < self.settings.heartbeat_interval_seconds:
            return
        probe_inventory = self.refresh_probe_inventory(force=True)
        self.sample_time_sync()
        self.client.heartbeat(
            AgentHeartbeatRequest(
                agent_id=self.agent_id,
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

    def current_status(self) -> AgentStatus:
        if self.running_captures:
            return AgentStatus.BUSY
        return AgentStatus.IDLE

    def poll_once(self) -> None:
        if self.agent_id is None:
            raise RuntimeError("agent not registered")
        self.heartbeat_if_needed()
        self._collect_finished_captures()
        response = self.client.poll(self.agent_id, self.current_status())
        if response.job is None:
            return
        self.execute_job(response.job)

    def execute_job(self, job: JobEnvelope) -> None:
        logger.info("executing job %s type=%s", job.id, job.type)
        try:
            if job.type == JobType.BUILD_ARTIFACT:
                payload = BuildArtifactPayload.model_validate(job.payload)
                result = self.build_executor.run_build(payload, client=self.client, agent_id=self.agent_id)
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
                JobResult(success=False, failure_reason="agent_exception", diagnostics={"error": str(exc)}),
            )

    def _upload_prepare_side_effects(self, payload: PrepareRolePayload) -> None:
        context = self.state_store.load_context(payload.session_id, payload.role.value)
        if context is None:
            return
        for path, artifact_type in (
            (context.openocd_log_path, RawArtifactType.OPENOCD_LOG),
            (context.event_log_path, RawArtifactType.AGENT_EVENT_LOG),
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
        for job_id, capture in self.running_captures.items():
            if not capture.done():
                continue
            result = capture.result()
            context = capture.context
            upload_error: Exception | None = None
            for path, artifact_type in (
                (capture.rtt_human_log_path, RawArtifactType.RTT_LOG),
                (capture.rtt_machine_log_path, RawArtifactType.RTT_MACHINE_LOG),
                (Path(context.event_log_path) if context.event_log_path else None, RawArtifactType.AGENT_EVENT_LOG),
                (Path(context.timing_samples_path) if context.timing_samples_path else None, RawArtifactType.TIMING_SAMPLES),
            ):
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
            finished.append(job_id)
        for job_id in finished:
            self.running_captures.pop(job_id, None)

    def run(self) -> None:
        logger.info(
            "agent starting with server_url=%s name=%s hostname=%s",
            self.settings.server_url,
            self.settings.name,
            self.settings.hostname,
        )
        self.register()
        logger.info("agent registered as %s", self.agent_id)
        while True:
            self.poll_once()
            time.sleep(self.settings.poll_interval_seconds)

    def close(self) -> None:
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
        if self.agent_id is None:
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
            payload, client=self.client, agent_id=self.agent_id
        )
        if not result.success:
            raise RuntimeError(result.failure_reason)

    def refresh_probe_inventory(self, *, force: bool = False) -> ProbeInventorySnapshot:
        if not (self.settings.capabilities.flash_capable or self.settings.capabilities.capture_capable):
            self.probe_inventory = ProbeInventorySnapshot(
                connected_probes=[],
                configured_probe_serial=self.settings.openocd.probe_serial,
                selected_probe_serial=self.settings.openocd.probe_serial,
                selection_reason="probe_scan_skipped_for_non_hardware_agent",
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
            producing_agent_id=None,
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
            producing_agent_id=None,
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
