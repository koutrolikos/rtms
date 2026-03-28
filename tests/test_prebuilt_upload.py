from pathlib import Path

from rtms.host.app.core.config import HostSettings
from rtms.host.app.services.bundles import extract_bundle, load_manifest
from rtms.host.app.services.runtime import HostRuntime
from rtms.shared.enums import Role
from rtms.shared.schemas import ArtifactUploadResult


def test_upload_prebuilt_artifact_creates_manual_bundle(tmp_path: Path) -> None:
    elf_path = tmp_path / "High-Altitude-CC.elf"
    elf_path.write_bytes(b"firmware")
    runtime = HostRuntime(
        HostSettings(
            server_url="http://172.20.10.3:8000",
            data_dir=tmp_path / "host_data",
        )
    )
    uploaded = {}

    def fake_upload_artifact_bundle(**kwargs):
        uploaded.update(kwargs)
        return ArtifactUploadResult(
            artifact_id="artifact-123",
            storage_path="artifacts/session-1/artifact-123/bundle.zip",
            sha256="abc123",
            manifest=load_manifest(
                extract_bundle(kwargs["bundle_path"], tmp_path / "extracted")
            ),
        )

    runtime.client.upload_artifact_bundle = fake_upload_artifact_bundle  # type: ignore[method-assign]

    artifact_id = runtime.upload_prebuilt_artifact(
        session_id="session-1",
        role=Role.TX,
        elf_path=str(elf_path),
        git_sha="eb1f1d5bf845bae78bb6e1427b145a75f970a079",
        source_repo="koutrolikos/High-Altitude-CC",
        rtt_symbol="_SEGGER_RTT",
        dirty_worktree=True,
    )

    manifest = load_manifest(extract_bundle(uploaded["bundle_path"], tmp_path / "verify"))
    assert artifact_id == "artifact-123"
    assert uploaded["origin_type"].value == "manual_upload"
    assert uploaded["role_hint"] == Role.TX
    assert manifest.git_sha == "eb1f1d5bf845bae78bb6e1427b145a75f970a079"
    assert manifest.build_metadata["dirty_worktree"] is True
    assert manifest.flash.flash_image_path == "firmware/High-Altitude-CC.elf"

    runtime.close()
