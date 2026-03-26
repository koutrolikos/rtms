from __future__ import annotations

from pathlib import Path

import pytest

from agent.app.core.config import AgentSettings
from agent.app.executors.capture import RunningCapture
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


def test_capture_fails_when_command_template_missing_and_simulation_disabled(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
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
    assert result.success is False
    assert result.failure_reason == "capture_failed"
    assert "capture command template missing" in result.diagnostics["error"]


def test_runtime_uploads_machine_rtt_artifact_after_capture(tmp_path: Path) -> None:
    settings = AgentSettings(
        server_url="http://172.20.10.3:8000",
        data_dir=tmp_path / "agent_data",
    )
    runtime = AgentRuntime(settings)
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
            self.context = _prepared_context(tmp_path, Role.RX)
            self.rtt_human_log_path = tmp_path / "session" / "rx" / "rtt.log"
            self.rtt_machine_log_path = tmp_path / "session" / "rx" / "rtt.rttbin"
            self.rtt_human_log_path.parent.mkdir(parents=True, exist_ok=True)
            self.rtt_human_log_path.write_text("human", encoding="utf-8")
            self.rtt_machine_log_path.write_bytes(b"MLOG")

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
    assert reported_results[0][0] == "job-1"


def _prepared_context(tmp_path: Path, role: Role) -> PreparedRoleContext:
    work_dir = tmp_path / "session" / role.value.lower()
    return PreparedRoleContext(
        session_id="session-1",
        role_run_id=f"role-run-{role.value.lower()}",
        role=role.value,
        artifact_id="artifact-1",
        work_dir=str(work_dir),
        bundle_path=str(tmp_path / "bundle.zip"),
        extracted_dir=str(tmp_path / "bundle"),
        manifest=ArtifactBundleManifest(
            artifact_id="artifact-1",
            session_id="session-1",
            origin_type=ArtifactOriginType.MANUAL_UPLOAD,
            created_at=utc_now(),
            files=[],
            flash=FlashSpec(elf_path="firmware.elf", flash_image_path="firmware.elf"),
        ),
    )
