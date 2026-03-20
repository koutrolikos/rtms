from pathlib import Path

from agent.app.services.bundles import create_artifact_bundle, load_manifest
from shared.enums import ArtifactOriginType, Role


def test_bundle_manifest_round_trip(tmp_path: Path) -> None:
    payload = tmp_path / "app.elf"
    payload.write_bytes(b"firmware")
    bundle = tmp_path / "bundle.zip"
    create_artifact_bundle(
        output_path=bundle,
        session_id="session-1",
        artifact_id="artifact-1",
        origin_type=ArtifactOriginType.GITHUB_BUILD,
        producing_agent_id="agent-1",
        role_hint=Role.TX,
        source_repo="org/repo",
        git_sha="abc123",
        files=[(payload, "build/app.elf")],
        flash_image_path="build/app.elf",
        elf_path="build/app.elf",
        rtt_symbol="_SEGGER_RTT",
        build_metadata={"flavor": "release"},
    )
    extract_dir = tmp_path / "extract"
    from agent.app.services.bundles import extract_bundle

    extract_bundle(bundle, extract_dir)
    manifest = load_manifest(extract_dir)
    assert manifest.artifact_id == "artifact-1"
    assert manifest.flash.flash_image_path == "build/app.elf"
    assert manifest.role_hint == Role.TX

