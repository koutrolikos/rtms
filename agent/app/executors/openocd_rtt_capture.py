from __future__ import annotations

import codecs
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path

from agent.app.core.config import AgentSettings
from agent.app.services.probes import normalize_probe_serial
from agent.app.storage.local_state import PreparedRoleContext

logger = logging.getLogger(__name__)


class OpenOcdRttCapture:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        context: PreparedRoleContext,
        duration_seconds: int | None,
        stop_requested: threading.Event,
        rtt_human_log_path: Path,
        rtt_machine_log_path: Path,
        capture_command_log_path: Path,
    ) -> None:
        self.settings = settings
        self.context = context
        self.duration_seconds = duration_seconds
        self.stop_requested = stop_requested
        self.rtt_human_log_path = rtt_human_log_path
        self.rtt_machine_log_path = rtt_machine_log_path
        self.capture_command_log_path = capture_command_log_path
        self.human_port = _reserve_local_tcp_port()
        self.machine_port = _reserve_local_tcp_port()

    def run(self) -> subprocess.Popen[str]:
        with self.capture_command_log_path.open("w", encoding="utf-8") as handle:
            process = subprocess.Popen(
                self._command(),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                human_socket = self._connect_to_rtt_port(self.human_port, process)
                machine_socket = self._connect_to_rtt_port(self.machine_port, process)
                with human_socket, machine_socket:
                    finished_by_duration = self._stream_rtt(process, human_socket, machine_socket)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=self.settings.capture.terminate_grace_seconds)
                    except subprocess.TimeoutExpired:
                        process.kill()
            return_code = process.wait()
        if return_code != 0 and not self.stop_requested.is_set() and not finished_by_duration:
            raise RuntimeError(f"built-in capture openocd exited with {return_code}")
        return process

    def _command(self) -> list[str]:
        command = [
            self.settings.openocd.openocd_bin,
            "-f",
            self.settings.openocd.interface_cfg,
            "-f",
            self.settings.openocd.target_cfg,
        ]
        probe_serial = normalize_probe_serial(self.context.probe_serial)
        if probe_serial:
            command.extend(["-c", f"adapter serial {probe_serial}"])
        command.extend(self.settings.openocd.extra_args)
        command.extend(
            [
                "-c",
                "gdb_port disabled",
                "-c",
                "tcl_port disabled",
                "-c",
                "telnet_port disabled",
                "-c",
                "init",
                "-c",
                "reset run",
                "-c",
                (
                    "rtt setup "
                    f"{self.settings.capture.builtin_rtt_search_address} "
                    f"{self.settings.capture.builtin_rtt_search_size_bytes} "
                    f"\"{self.settings.capture.builtin_rtt_id}\""
                ),
                "-c",
                f"rtt polling_interval {self.settings.capture.builtin_rtt_polling_interval_ms}",
                "-c",
                "rtt start",
                "-c",
                (
                    "rtt server start "
                    f"{self.human_port} {self.settings.capture.builtin_rtt_human_channel}"
                ),
                "-c",
                (
                    "rtt server start "
                    f"{self.machine_port} {self.settings.capture.builtin_rtt_machine_channel}"
                ),
            ]
        )
        return command

    def _connect_to_rtt_port(self, port: int, process: subprocess.Popen[str]) -> socket.socket:
        deadline = time.monotonic() + self.settings.capture.builtin_startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.stop_requested.is_set():
                raise RuntimeError("capture stopped before RTT ports became ready")
            return_code = process.poll()
            if return_code is not None:
                raise RuntimeError(f"built-in capture openocd exited with {return_code} before RTT startup")
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                sock.settimeout(0.5)
                return sock
            except OSError:
                time.sleep(0.2)
        raise RuntimeError(f"timed out waiting for RTT port {port} to accept connections")

    def _stream_rtt(
        self,
        process: subprocess.Popen[str],
        human_socket: socket.socket,
        machine_socket: socket.socket,
    ) -> bool:
        self.rtt_human_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.rtt_machine_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.rtt_human_log_path.open("w", encoding="utf-8") as human_handle:
            with self.rtt_machine_log_path.open("wb") as machine_handle:
                stream_stop = threading.Event()
                threads = [
                    threading.Thread(
                        target=_drain_socket_to_text_file,
                        args=(human_socket, human_handle, self.stop_requested, stream_stop),
                        daemon=True,
                    ),
                    threading.Thread(
                        target=_drain_socket_to_binary_file,
                        args=(machine_socket, machine_handle, self.stop_requested, stream_stop),
                        daemon=True,
                    ),
                ]
                for thread in threads:
                    thread.start()
                finished_by_duration = False
                deadline = (
                    time.monotonic() + self.duration_seconds
                    if self.duration_seconds
                    else None
                )
                while True:
                    if self.stop_requested.is_set():
                        break
                    if deadline is not None and time.monotonic() >= deadline:
                        finished_by_duration = True
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.2)
                stream_stop.set()
                for thread in threads:
                    thread.join(timeout=2.0)
                if process.poll() is None and finished_by_duration:
                    process.terminate()
                return finished_by_duration


def _reserve_local_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _drain_socket_to_text_file(
    sock: socket.socket,
    handle,
    stop_requested: threading.Event,
    stream_stop: threading.Event,
) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    while not stop_requested.is_set() and not stream_stop.is_set():
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        handle.write(decoder.decode(chunk))
        handle.flush()
    tail = decoder.decode(b"", final=True)
    if tail:
        handle.write(tail)
        handle.flush()


def _drain_socket_to_binary_file(
    sock: socket.socket,
    handle,
    stop_requested: threading.Event,
    stream_stop: threading.Event,
) -> None:
    while not stop_requested.is_set() and not stream_stop.is_set():
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        handle.write(chunk)
        handle.flush()
