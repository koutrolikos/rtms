from __future__ import annotations

import logging
import shlex
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.app.core.config import AgentSettings
from agent.app.storage.local_state import LocalStateStore, PreparedRoleContext
from shared.enums import Role
from shared.schemas import JobResult, StartCapturePayload
from shared.time_sync import utc_now

logger = logging.getLogger(__name__)


class RunningCapture:
    def __init__(
        self,
        *,
        job_id: str,
        context: PreparedRoleContext,
        payload: StartCapturePayload,
        settings: AgentSettings,
        state_store: LocalStateStore,
    ) -> None:
        self.job_id = job_id
        self.context = context
        self.payload = payload
        self.settings = settings
        self.state_store = state_store
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._done = threading.Event()
        self._stop_requested = threading.Event()
        self._result: JobResult | None = None
        self._process: subprocess.Popen[str] | None = None
        self.rtt_log_path = Path(self.context.work_dir) / "rtt.log"

    def start(self) -> None:
        self._thread.start()

    def stop(self, reason: str = "manual_stop") -> None:
        self._stop_requested.set()
        self.state_store.append_event(self.context, "capture_stop_requested", {"reason": reason})
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=self.settings.capture.terminate_grace_seconds)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def done(self) -> bool:
        return self._done.is_set()

    def result(self) -> JobResult:
        if self._result is None:
            raise RuntimeError("capture result not ready")
        return self._result

    def _run(self) -> None:
        try:
            planned = self.payload.planned_start_at
            delay = (planned - utc_now()).total_seconds()
            if delay > 0:
                time.sleep(delay)
            self.state_store.append_event(
                self.context,
                "capture_started",
                {"planned_start_at": planned.isoformat(), "actual_start_at": utc_now().isoformat()},
            )
            if self.settings.capture.simulate_capture or not self.settings.capture.command_template:
                self._simulate_capture()
            else:
                self._real_capture()
            self.state_store.append_event(self.context, "capture_finished", {"finished_at": utc_now().isoformat()})
            self._result = JobResult(
                success=True,
                diagnostics={
                    "rtt_log_path": str(self.rtt_log_path),
                    "event_log_path": self.context.event_log_path,
                    "timing_samples_path": self.context.timing_samples_path,
                    "capture_mode": "simulated"
                    if self.settings.capture.simulate_capture or not self.settings.capture.command_template
                    else "external_command",
                },
                time_samples=self.context.latest_time_samples,
            )
        except Exception as exc:  # pragma: no cover - defensive wrapper
            logger.exception("capture failed")
            self._result = JobResult(success=False, failure_reason="capture_failed", diagnostics={"error": str(exc)})
        finally:
            self._done.set()

    def _simulate_capture(self) -> None:
        duration = self.payload.duration_seconds or 10
        self.rtt_log_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = time.monotonic()
        sequence = 1
        with self.rtt_log_path.open("w", encoding="utf-8") as handle:
            while True:
                if self._stop_requested.is_set():
                    break
                elapsed = time.monotonic() - started_at
                handle.write(
                    f"[{elapsed:.3f}] event={'tx_packet' if self.payload.role == Role.TX else 'rx_packet'} "
                    f"seq={sequence} rssi={-40 - (sequence % 7)} snr={12 + (sequence % 3)}\n"
                )
                handle.flush()
                sequence += 1
                if self.payload.duration_seconds and elapsed >= duration:
                    break
                time.sleep(1.0)

    def _real_capture(self) -> None:
        command_template = self.settings.capture.command_template
        if command_template is None:
            raise RuntimeError("capture command template missing")
        command = command_template.format(
            role=self.payload.role.value,
            session_id=self.payload.session_id,
            role_run_id=self.payload.role_run_id,
            probe_serial=self.context.probe_serial or "",
            elf_path=str(Path(self.context.extracted_dir) / (self.context.manifest.flash.elf_path or "")),
            rtt_log_path=str(self.rtt_log_path),
            duration_seconds=str(self.payload.duration_seconds or ""),
        )
        self.rtt_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rtt_log_path.open("w", encoding="utf-8") as handle:
            self._process = subprocess.Popen(
                shlex.split(command),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if self.payload.duration_seconds:
                try:
                    self._process.wait(timeout=self.payload.duration_seconds + self.settings.capture.terminate_grace_seconds)
                except subprocess.TimeoutExpired:
                    self.stop(reason="duration_timeout")
            else:
                while self._process.poll() is None and not self._stop_requested.is_set():
                    time.sleep(0.5)
            return_code = self._process.wait()
        if return_code != 0 and not self._stop_requested.is_set():
            raise RuntimeError(f"capture command exited with {return_code}")

