from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.core.config import AgentSettings
from agent.app.executors.capture import RunningCapture
from agent.app.services.api_client import ServerConnectionError
from agent.app.services.runtime import AgentRuntime
from agent.app.storage.local_state import LocalStateStore, PreparedRoleContext
from shared.enums import ArtifactOriginType, RawArtifactType, Role, StopMode
from shared.manifest import ArtifactBundleManifest, FlashSpec
from shared.schemas import JobResult, StartCapturePayload
from shared.time_sync import utc_now


def test_simulated_capture_writes_human_and_machine_logs(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
        capture={"simulate_capture": True},
    )
    state_store = LocalStateStore(tmp_path / "state")
    capture = RunningCapture(
        job_id="job-1",
        context=_prepared_context(tmp_path, Role.TX),
        payload=StartCapturePayload(
            session_id="session-1",
            role_run_id="role-run-1",
            role=Role.TX,
            planned_start_at=utc_now(),
            duration_seconds=1,
            stop_mode=StopMode.FIXED_DURATION,
        ),
        settings=settings,
        state_store=state_store,
    )

    capture._run()

    result = capture.result()
    assert result.success is True
    assert capture.rtt_human_log_path.exists()
    assert capture.rtt_machine_log_path.exists()
    assert "role=TX" in capture.rtt_human_log_path.read_text(encoding="utf-8")
    assert capture.rtt_machine_log_path.read_bytes().startswith(b"MLOG")
    assert result.diagnostics["rtt_human_log_path"].endswith("rtt.log")
    assert result.diagnostics["rtt_machine_log_path"].endswith("rtt.rttbin")
    assert result.diagnostics["capture_mode"] == "simulated"


def test_capture_uses_builtin_openocd_when_command_template_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
    )
    state_store = LocalStateStore(tmp_path / "state")
    invoked: list[str] = []

    def fake_builtin_capture(self) -> None:
        invoked.append("builtin")

    monkeypatch.setattr(RunningCapture, "_capture_with_builtin_openocd_rtt", fake_builtin_capture)
    capture = RunningCapture(
        job_id="job-1",
        context=_prepared_context(tmp_path, Role.TX),
        payload=StartCapturePayload(
            session_id="session-1",
            role_run_id="role-run-1",
            role=Role.TX,
            planned_start_at=utc_now(),
            duration_seconds=1,
            stop_mode=StopMode.FIXED_DURATION,
        ),
        settings=settings,
        state_store=state_store,
    )

    capture._run()

    result = capture.result()
    assert result.success is True
    assert result.diagnostics["capture_mode"] == "builtin_openocd_rtt"
    assert invoked == ["builtin"]


def test_runtime_uploads_machine_rtt_artifact_after_capture(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
    )
    runtime = AgentRuntime(settings)
    context = _prepared_context(tmp_path, Role.RX)
    runtime.state_store.save_context(context)
    _write_session_files(context, include_capture_logs=True)
    uploads: list[dict] = []
    reported_results: list[tuple[str, JobResult]] = []

    class FakeClient:
        def upload_raw_artifact(self, **kwargs):
            uploads.append(kwargs)

        def report_job_result(self, job_id, result):
            reported_results.append((job_id, result))

        def close(self):
            return None

    class FakeCapture:
        def __init__(self) -> None:
            self.context = context
            self.rtt_human_log_path = Path(context.work_dir) / "rtt.log"
            self.rtt_machine_log_path = Path(context.work_dir) / "rtt.rttbin"
            self.capture_command_log_path = Path(context.work_dir) / "capture-command.log"

        def done(self) -> bool:
            return True

        def result(self) -> JobResult:
            return JobResult(success=True, diagnostics={})

    runtime.client = FakeClient()
    runtime.running_captures = {"job-1": FakeCapture()}

    runtime._collect_finished_captures()

    artifact_types = [item["artifact_type"] for item in uploads]
    assert RawArtifactType.RTT_LOG in artifact_types
    assert RawArtifactType.RTT_MACHINE_LOG in artifact_types
    assert RawArtifactType.CAPTURE_COMMAND_LOG in artifact_types
    assert RawArtifactType.AGENT_EVENT_LOG in artifact_types
    assert RawArtifactType.TIMING_SAMPLES in artifact_types
    assert reported_results[0][0] == "job-1"
    assert not (settings.sessions_root / context.session_id / Role.RX.value.lower()).exists()
    assert runtime.state_store.load_context(context.session_id, context.role) is None
    assert not (settings.downloads_root / context.session_id).exists()


def test_cleanup_sweep_uploads_terminal_session_logs_and_removes_local_state(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
    )
    runtime = AgentRuntime(settings)
    context = _prepared_context(tmp_path, Role.TX)
    runtime.state_store.save_context(context)
    _write_session_files(context, include_capture_logs=True)
    uploads: list[dict] = []

    class FakeClient:
        def get_session_status(self, session_id: str):
            return {"id": session_id, "status": "failed"}

        def upload_raw_artifact(self, **kwargs):
            uploads.append(kwargs)

        def close(self):
            return None

    runtime.client = FakeClient()

    runtime._cleanup_local_sessions(force=True)

    artifact_types = [item["artifact_type"] for item in uploads]
    assert RawArtifactType.OPENOCD_LOG in artifact_types
    assert RawArtifactType.AGENT_EVENT_LOG in artifact_types
    assert RawArtifactType.TIMING_SAMPLES in artifact_types
    assert RawArtifactType.RTT_LOG in artifact_types
    assert RawArtifactType.RTT_MACHINE_LOG in artifact_types
    assert RawArtifactType.CAPTURE_COMMAND_LOG in artifact_types
    assert not (settings.sessions_root / context.session_id).exists()
    assert not (settings.downloads_root / context.session_id).exists()
    assert runtime.state_store.load_context(context.session_id, context.role) is None


def test_cleanup_sweep_deletes_local_session_when_server_session_missing(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
    )
    runtime = AgentRuntime(settings)
    context = _prepared_context(tmp_path, Role.RX)
    runtime.state_store.save_context(context)
    _write_session_files(context, include_capture_logs=False)
    upload_calls: list[dict] = []

    class FakeClient:
        def get_session_status(self, session_id: str):
            return None

        def upload_raw_artifact(self, **kwargs):
            upload_calls.append(kwargs)

        def close(self):
            return None

    runtime.client = FakeClient()

    runtime._cleanup_local_sessions(force=True)

    assert upload_calls == []
    assert not (settings.sessions_root / context.session_id).exists()
    assert not (settings.downloads_root / context.session_id).exists()
    assert runtime.state_store.load_context(context.session_id, context.role) is None


def test_cleanup_sweep_keeps_local_state_when_server_is_unreachable(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
    )
    runtime = AgentRuntime(settings)
    context = _prepared_context(tmp_path, Role.TX)
    runtime.state_store.save_context(context)
    _write_session_files(context, include_capture_logs=False)

    class FakeClient:
        def get_session_status(self, session_id: str):
            raise ServerConnectionError(f"Could not connect to the control server at {session_id}")

        def close(self):
            return None

    runtime.client = FakeClient()

    runtime._cleanup_local_sessions(force=True)

    assert (settings.sessions_root / context.session_id / Role.TX.value.lower()).exists()
    assert (settings.downloads_root / context.session_id).exists()
    assert runtime.state_store.load_context(context.session_id, context.role) is not None


def _prepared_context(tmp_path: Path, role: Role) -> PreparedRoleContext:
    agent_root = tmp_path / "agent_data"
    work_dir = agent_root / "sessions" / "session-1" / role.value.lower()
    bundle_path = agent_root / "downloads" / "session-1" / f"{role.value.lower()}_artifact-1.zip"
    return PreparedRoleContext(
        session_id="session-1",
        role_run_id=f"role-run-{role.value.lower()}",
        role=role.value,
        artifact_id="artifact-1",
        work_dir=str(work_dir),
        bundle_path=str(bundle_path),
        extracted_dir=str(work_dir / "bundle"),
        manifest=ArtifactBundleManifest(
            artifact_id="artifact-1",
            session_id="session-1",
            origin_type=ArtifactOriginType.MANUAL_UPLOAD,
            created_at=utc_now(),
            files=[],
            flash=FlashSpec(elf_path="firmware.elf", flash_image_path="firmware.elf"),
        ),
        openocd_log_path=str(work_dir / "openocd.log"),
        event_log_path=str(work_dir / "agent_events.jsonl"),
        timing_samples_path=str(work_dir / "timing_samples.json"),
    )


def _write_session_files(context: PreparedRoleContext, *, include_capture_logs: bool) -> None:
    work_dir = Path(context.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    Path(context.bundle_path).parent.mkdir(parents=True, exist_ok=True)
    Path(context.bundle_path).write_bytes(b"bundle")
    Path(context.openocd_log_path).write_text("openocd", encoding="utf-8")
    Path(context.event_log_path).write_text('{"event":"artifact_ready"}\n', encoding="utf-8")
    Path(context.timing_samples_path).write_text("[]", encoding="utf-8")
    if include_capture_logs:
        (work_dir / "rtt.log").write_text("human", encoding="utf-8")
        (work_dir / "rtt.rttbin").write_bytes(b"MLOG")
        (work_dir / "capture-command.log").write_text("capture", encoding="utf-8")
