from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from agent.app.core.config import AgentSettings
from agent.app.services.bundles import extract_bundle, load_manifest
from agent.app.services.probes import ProbeInventorySnapshot, normalize_probe_serial, scan_probe_inventory
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
        probe_inventory: ProbeInventorySnapshot | None = None,
    ) -> JobResult:
        probe_inventory = probe_inventory or scan_probe_inventory(
            configured_probe_serial=self.settings.openocd.probe_serial,
            scan_enabled=self.settings.openocd.probe_scan_enabled,
        )
        probe_serial = normalize_probe_serial(
            self.settings.openocd.probe_serial or probe_inventory.selected_probe_serial
        )
        if not self.settings.openocd.simulate_hardware:
            if probe_inventory.selection_reason == "multiple_probes_detected" and probe_serial is None:
                return JobResult(
                    success=False,
                    failure_reason="probe_selection_failed",
                    diagnostics={
                        **probe_inventory.diagnostics(),
                        "hint": "Connect a single probe or set RANGE_TEST_PROBE_SERIAL on this agent.",
                    },
                    time_samples=time_samples,
                )
            if probe_inventory.selection_reason in {"no_probes_detected", "probe_scan_failed"} and probe_serial is None:
                return JobResult(
                    success=False,
                    failure_reason="probe_not_found",
                    diagnostics={
                        **probe_inventory.diagnostics(),
                        "hint": "Connect a probe to this agent before starting the session.",
                    },
                    time_samples=time_samples,
                )
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
            probe_serial=probe_serial,
            openocd_log_path=str(work_dir / "openocd.log"),
            event_log_path=str(event_log_path),
            timing_samples_path=str(timing_samples_path),
            diagnostics=probe_inventory.diagnostics(),
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
                    "probe_selection_reason": probe_inventory.selection_reason,
                    "connected_probe_count": probe_inventory.connected_probe_count,
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
                    "probe_selection_reason": probe_inventory.selection_reason,
                    "connected_probe_count": probe_inventory.connected_probe_count,
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
        probe_serial = normalize_probe_serial(context.probe_serial)
        if probe_serial:
            command.extend(["-c", f"adapter serial {probe_serial}"])
        command.extend(self.settings.openocd.extra_args)
        command.extend(["-c", self._program_command(image_path)])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.settings.openocd.flash_timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise PrepareFailure(
                "openocd_launch_failed",
                {
                    "error": str(exc),
                    "command": command,
                    "openocd_bin": self.settings.openocd.openocd_bin,
                    "hint": (
                        "OpenOCD executable was not found. Install OpenOCD on the agent host "
                        "or set RANGE_TEST_OPENOCD_BIN to the full path of the binary, for example "
                        r"C:\Program Files\OpenOCD\bin\openocd.exe"
                    ),
                },
            ) from exc
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
            diagnostics = {
                "return_code": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "openocd_target_cfg": self.settings.openocd.target_cfg,
            }
            if "cannot identify target as a stm32 family" in stderr or "auto_probe failed" in stderr:
                diagnostics["hint"] = (
                    "OpenOCD target config may not match the MCU family. "
                    "STM32G474 targets should use target/stm32g4x.cfg."
                )
            raise PrepareFailure(
                "flash_failed",
                diagnostics,
            )
        text = f"{completed.stdout}\n{completed.stderr}".lower()
        if not any(marker in text for marker in self.settings.openocd.verify_markers):
            raise PrepareFailure("verify_failed", {"stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]})

    def _program_command(self, image_path: Path) -> str:
        normalized_path = str(image_path).replace("\\", "/")
        return f"program {{{normalized_path}}} verify reset exit"

    def _resolve_rtt_symbol(self, manifest: ArtifactBundleManifest) -> None:
        if manifest.flash.rtt_symbol:
            return
        if manifest.flash.elf_path:
            return
        raise PrepareFailure("rtt_symbol_lookup_failed", {"message": "manifest missing RTT symbol and ELF path"})
