from __future__ import annotations

import logging
import shlex
import struct
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rtms.host.app.core.config import HostSettings
from rtms.host.app.executors.openocd_rtt_capture import OpenOcdRttCapture
from rtms.host.app.storage.local_state import LocalStateStore, PreparedRoleContext
from rtms.shared.enums import Role
from rtms.shared.mlog import (
    MLOG_KIND_EVT,
    MLOG_KIND_PKT,
    MLOG_KIND_RUN,
    MLOG_KIND_STAT,
    build_mlog_frame,
)
from rtms.shared.schemas import JobResult, StartCapturePayload
from rtms.shared.time_sync import utc_now

logger = logging.getLogger(__name__)


class RunningCapture:
    def __init__(
        self,
        *,
        job_id: str,
        context: PreparedRoleContext,
        payload: StartCapturePayload,
        settings: HostSettings,
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
        self.rtt_human_log_path = Path(self.context.work_dir) / "rtt.log"
        self.rtt_machine_log_path = Path(self.context.work_dir) / "rtt.rttbin"
        self.rtt_log_path = self.rtt_human_log_path
        self.capture_command_log_path = Path(self.context.work_dir) / "capture-command.log"

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
            capture_mode = "external_command"
            planned = self.payload.planned_start_at
            delay = (planned - utc_now()).total_seconds()
            if delay > 0:
                time.sleep(delay)
            self.state_store.append_event(
                self.context,
                "capture_started",
                {"planned_start_at": planned.isoformat(), "actual_start_at": utc_now().isoformat()},
            )
            if self.settings.capture.simulate_capture:
                capture_mode = "simulated"
                self._simulate_capture()
            else:
                if self.settings.capture.command_template:
                    self._capture_with_external_command()
                else:
                    capture_mode = "builtin_openocd_rtt"
                    self._capture_with_builtin_openocd_rtt()
            self.state_store.append_event(self.context, "capture_finished", {"finished_at": utc_now().isoformat()})
            self._result = JobResult(
                success=True,
                diagnostics={
                    "rtt_human_log_path": str(self.rtt_human_log_path),
                    "rtt_machine_log_path": str(self.rtt_machine_log_path),
                    "rtt_log_path": str(self.rtt_log_path),
                    "capture_command_log_path": str(self.capture_command_log_path),
                    "event_log_path": self.context.event_log_path,
                    "timing_samples_path": self.context.timing_samples_path,
                    "capture_mode": capture_mode,
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
        self.rtt_human_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.rtt_machine_log_path.parent.mkdir(parents=True, exist_ok=True)
        started_at = time.monotonic()
        sequence = 1
        with self.rtt_human_log_path.open("w", encoding="utf-8") as human_handle:
            with self.rtt_machine_log_path.open("wb") as machine_handle:
                machine_handle.write(_simulated_run_frame(self.payload.role))
                machine_handle.write(_simulated_event_frame(self.payload.role, 0))
                machine_handle.flush()
                while True:
                    if self._stop_requested.is_set():
                        break
                    elapsed = time.monotonic() - started_at
                    t_ms = int(elapsed * 1000.0)
                    human_handle.write(
                        f"[ts={t_ms} ms] [I] [SIM] role={self.payload.role.value} seq={sequence}\n"
                    )
                    human_handle.flush()
                    machine_handle.write(_simulated_packet_frame(self.payload.role, sequence, t_ms))
                    machine_handle.write(_simulated_stat_frame(self.payload.role, sequence, t_ms))
                    machine_handle.flush()
                    sequence += 1
                    if self.payload.duration_seconds and elapsed >= duration:
                        break
                    time.sleep(1.0)

    def _capture_with_external_command(self) -> None:
        command_template = self.settings.capture.command_template
        if command_template is None:
            raise RuntimeError("capture command template missing")
        command = command_template.format(
            role=self.payload.role.value,
            session_id=self.payload.session_id,
            role_run_id=self.payload.role_run_id,
            probe_serial=self.context.probe_serial or "",
            elf_path=str(Path(self.context.extracted_dir) / (self.context.manifest.flash.elf_path or "")),
            rtt_human_log_path=str(self.rtt_human_log_path),
            rtt_machine_log_path=str(self.rtt_machine_log_path),
            rtt_log_path=str(self.rtt_log_path),
            capture_command_log_path=str(self.capture_command_log_path),
            duration_seconds=str(self.payload.duration_seconds or ""),
        )
        for path in (
            self.rtt_human_log_path,
            self.rtt_machine_log_path,
            self.capture_command_log_path,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        with self.capture_command_log_path.open("w", encoding="utf-8") as handle:
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

    def _capture_with_builtin_openocd_rtt(self) -> None:
        runner = OpenOcdRttCapture(
            settings=self.settings,
            context=self.context,
            duration_seconds=self.payload.duration_seconds,
            stop_requested=self._stop_requested,
            rtt_human_log_path=self.rtt_human_log_path,
            rtt_machine_log_path=self.rtt_machine_log_path,
            capture_command_log_path=self.capture_command_log_path,
        )
        self._process = runner.run()


def _simulated_run_frame(role: Role) -> bytes:
    if role == Role.TX:
        payload = struct.pack(
            "<8B8I",
            1,
            1,
            4,
            2,
            1,
            1,
            2,
            3,
            433_200_000,
            434_600_000,
            76_760,
            5_000,
            306_000_000,
            200,
            50,
            20,
        )
    else:
        payload = b"".join(
            [
                struct.pack("<8B", 1, 1, 4, 2, 1, 1, 2, 3),
                struct.pack("<4I", 433_200_000, 434_600_000, 76_760, 5_000),
                struct.pack("<B", 1),
                b"\x00\x00\x00",
                struct.pack("<i", -92),
                struct.pack("<3I", 110, 25, 8),
            ]
        )
    return build_mlog_frame(kind_code=MLOG_KIND_RUN, role=role, t_ms=0, payload=payload)


def _simulated_stat_frame(role: Role, sequence: int, t_ms: int) -> bytes:
    if role == Role.TX:
        payload = struct.pack(
            "<18I",
            sequence,
            sequence,
            max(sequence - 1, 0),
            sequence,
            max(sequence - 1, 0),
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            1 if sequence > 3 else 0,
            14,
            12,
            3,
            sequence * 1800,
            306_000_000,
        )
    else:
        accepted = max(sequence - 1, 0)
        payload = struct.pack(
            "<16I",
            sequence,
            accepted,
            sequence - accepted,
            0,
            0,
            0,
            sequence - accepted,
            0,
            sequence - accepted,
            0,
            0,
            0,
            0,
            4,
            1,
            4,
        )
    return build_mlog_frame(kind_code=MLOG_KIND_STAT, role=role, t_ms=t_ms, payload=payload)


def _simulated_packet_frame(role: Role, sequence: int, t_ms: int) -> bytes:
    if role == Role.TX:
        payload = struct.pack("<4B2I", 0x01, 0x01, sequence % 256, 24, 12 + (sequence % 5), sequence % 4)
    else:
        accepted = 1 if sequence % 3 != 0 else 0
        drop_reason = 0 if accepted else 3
        payload = struct.pack(
            "<6BbBB",
            0x01,
            0x01,
            sequence % 256,
            24,
            accepted,
            drop_reason,
            -48 - (sequence % 4),
            108 - (sequence % 3),
            1,
        )
    return build_mlog_frame(kind_code=MLOG_KIND_PKT, role=role, t_ms=t_ms, payload=payload)


def _simulated_event_frame(role: Role, t_ms: int) -> bytes:
    if role == Role.TX:
        payload = struct.pack("<4B", 1, 4, 1, 0) + struct.pack("<BBHII", 2, 3, 0, 433_200_000, 434_600_000)
    else:
        payload = struct.pack("<4B", 2, 4, 1, 0) + struct.pack("<BBHII", 2, 3, 0, 433_200_000, 434_600_000)
    return build_mlog_frame(kind_code=MLOG_KIND_EVT, role=role, t_ms=t_ms, payload=payload)
