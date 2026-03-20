from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from agent.app.core.config import AgentSettings
from agent.app.services.bundles import extract_bundle, load_manifest
from agent.app.storage.local_state import LocalStateStore, PreparedRoleContext
from shared.enums import RawArtifactType, Role
from shared.manifest import ArtifactBundleManifest
from shared.schemas import JobResult, PrepareRolePayload
from shared.time_sync import utc_now

logger = logging.getLogger(__name__)


class PrepareFailure(RuntimeError):
    def __init__(self, reason: str, diagnostics: dict | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


class OpenOcdExecutor:
    def __init__(self, settings: AgentSettings, state_store: LocalStateStore) -> None:
        self.settings = settings
        self.state_store = state_store

    def prepare(
        self,
        payload: PrepareRolePayload,
        *,
        bundle_path: Path,
        time_samples,
    ) -> JobResult:
        work_dir = self.settings.sessions_root / payload.session_id / payload.role.value.lower()
        work_dir.mkdir(parents=True, exist_ok=True)
        extracted_dir = extract_bundle(bundle_path, work_dir / "bundle")
        manifest = load_manifest(extracted_dir)
        event_log_path = work_dir / "agent_events.jsonl"
        timing_samples_path = work_dir / "timing_samples.json"
        context = PreparedRoleContext(
            session_id=payload.session_id,
            role_run_id=payload.role_run_id,
            role=payload.role.value,
            artifact_id=payload.artifact_id,
            work_dir=str(work_dir),
            bundle_path=str(bundle_path),
            extracted_dir=str(extracted_dir),
            manifest=manifest,
            probe_serial=self.settings.openocd.probe_serial,
            openocd_log_path=str(work_dir / "openocd.log"),
            event_log_path=str(event_log_path),
            timing_samples_path=str(timing_samples_path),
            diagnostics={},
            latest_time_samples=list(time_samples),
        )
        self.state_store.append_event(context, "artifact_ready", {"bundle_path": str(bundle_path)})
        self.state_store.write_timing_samples(context)
        try:
            flash_image = self._resolve_flash_image(manifest, extracted_dir)
            self._flash_and_verify(flash_image, context)
            self._resolve_rtt_symbol(manifest)
            context.diagnostics.update(
                {
                    "flash_image": str(flash_image),
                    "flash_result": "ok",
                    "verify_result": "ok",
                    "prepared_at": utc_now().isoformat(),
                }
            )
            self.state_store.append_event(context, "flash_verified", context.diagnostics)
            self.state_store.save_context(context)
            return JobResult(
                success=True,
                state_hint="capture_ready",
                diagnostics={
                    "probe_serial": context.probe_serial,
                    "flash_result": "ok",
                    "verify_result": "ok",
                    "openocd_log_path": context.openocd_log_path,
                    "event_log_path": context.event_log_path,
                    "timing_samples_path": context.timing_samples_path,
                },
                time_samples=time_samples,
            )
        except PrepareFailure as exc:
            self.state_store.append_event(context, "prepare_failed", {"reason": exc.reason, **exc.diagnostics})
            self.state_store.save_context(context)
            return JobResult(success=False, failure_reason=exc.reason, diagnostics=exc.diagnostics, time_samples=time_samples)

    def _resolve_flash_image(self, manifest: ArtifactBundleManifest, extracted_dir: Path) -> Path:
        if manifest.flash.flash_image_path:
            path = extracted_dir / manifest.flash.flash_image_path
            if path.exists():
                return path
        if manifest.flash.elf_path:
            path = extracted_dir / manifest.flash.elf_path
            if path.exists():
                return path
        raise PrepareFailure("download_artifact_failed", {"manifest": manifest.model_dump(mode="json")})

    def _flash_and_verify(self, image_path: Path, context: PreparedRoleContext) -> None:
        log_path = Path(context.openocd_log_path or "")
        if self.settings.openocd.simulate_hardware:
            log_path.write_text(f"simulated openocd flash for {image_path}\nverified OK\n", encoding="utf-8")
            return
        command = [
            self.settings.openocd.openocd_bin,
            "-f",
            self.settings.openocd.interface_cfg,
            "-f",
            self.settings.openocd.target_cfg,
        ]
        if context.probe_serial:
            command.extend(["-c", f"hla_serial {context.probe_serial}"])
        command.extend(self.settings.openocd.extra_args)
        command.extend(["-c", f"program {shlex.quote(str(image_path))} verify reset exit"])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.settings.openocd.flash_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise PrepareFailure("openocd_launch_failed", {"error": str(exc), "command": command}) from exc
        except subprocess.TimeoutExpired as exc:
            raise PrepareFailure("flash_failed", {"error": "timeout", "command": command}) from exc
        log_path.write_text(
            "\n".join(
                [
                    "$ " + " ".join(command),
                    "",
                    "stdout:",
                    completed.stdout,
                    "",
                    "stderr:",
                    completed.stderr,
                ]
            ),
            encoding="utf-8",
        )
        if completed.returncode != 0:
            stderr = completed.stderr.lower()
            if "no device" in stderr or "not found" in stderr:
                raise PrepareFailure("probe_not_found", {"stderr": completed.stderr[-4000:]})
            raise PrepareFailure(
                "flash_failed",
                {
                    "return_code": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                },
            )
        text = f"{completed.stdout}\n{completed.stderr}".lower()
        if not any(marker in text for marker in self.settings.openocd.verify_markers):
            raise PrepareFailure("verify_failed", {"stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]})

    def _resolve_rtt_symbol(self, manifest: ArtifactBundleManifest) -> None:
        if manifest.flash.rtt_symbol:
            return
        if manifest.flash.elf_path:
            return
        raise PrepareFailure("rtt_symbol_lookup_failed", {"message": "manifest missing RTT symbol and ELF path"})

