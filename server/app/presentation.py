from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from jinja2 import Environment

from shared.time_sync import utc_now


def register_template_helpers(environment: Environment) -> None:
    environment.filters["basename"] = basename
    environment.filters["human_bytes"] = human_bytes
    environment.filters["human_relative_time"] = human_relative_time
    environment.filters["humanize_token"] = humanize_token
    environment.filters["short_id"] = short_id
    environment.filters["short_sha"] = short_sha
    environment.globals.update(
        clamp01=clamp01,
        describe_source=describe_source,
        summarize_artifact_metadata=summarize_artifact_metadata,
        summarize_capabilities=summarize_capabilities,
        summarize_event_payload=summarize_event_payload,
        summarize_job_diagnostics=summarize_job_diagnostics,
        summarize_time_correction=summarize_time_correction,
    )


def humanize_token(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).replace("_", " ")


def basename(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).replace("\\", "/")
    return PurePosixPath(text).name


def human_bytes(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    if size >= 10:
        return f"{size:.0f} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def human_relative_time(value: Any) -> str:
    if value in (None, ""):
        return "-"
    if not isinstance(value, datetime):
        return str(value)
    normalized = _as_utc(value)
    delta = utc_now() - normalized
    total_seconds = int(abs(delta.total_seconds()))
    if total_seconds < 60:
        label = "just now" if delta.total_seconds() >= 0 else "in moments"
        return label
    if total_seconds < 3600:
        minutes = total_seconds // 60
        suffix = "ago" if delta.total_seconds() >= 0 else "from now"
        return f"{minutes}m {suffix}"
    if total_seconds < 86400:
        hours = total_seconds // 3600
        suffix = "ago" if delta.total_seconds() >= 0 else "from now"
        return f"{hours}h {suffix}"
    days = total_seconds // 86400
    suffix = "ago" if delta.total_seconds() >= 0 else "from now"
    return f"{days}d {suffix}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def short_id(value: Any, length: int = 8) -> str:
    if value in (None, ""):
        return ""
    return str(value)[:length]


def short_sha(value: Any, length: int = 12) -> str:
    if value in (None, ""):
        return ""
    return str(value)[:length]


def clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return number


def describe_source(source_type: Any, source_ref: Any = None, host_labels: dict[str, str] | None = None) -> str:
    base = humanize_token(source_type) or "unknown"
    if source_ref in (None, ""):
        return base
    source_ref_text = str(source_ref)
    label = host_labels.get(source_ref_text) if host_labels else None
    if label:
        return f"{base} | {label}"
    if _looks_like_identifier(source_ref_text):
        return f"{base} | {short_id(source_ref_text)}"
    return f"{base} | {_truncate(source_ref_text, 40)}"


def summarize_capabilities(capabilities: dict[str, Any] | None) -> list[str]:
    payload = capabilities or {}
    items: list[str] = []
    if payload.get("build_capable"):
        items.append("Build")
    if payload.get("flash_capable"):
        items.append("Flash")
    if payload.get("capture_capable"):
        items.append("Capture")
    return items


def summarize_artifact_metadata(metadata: dict[str, Any] | None) -> list[str]:
    payload = metadata or {}
    if not isinstance(payload, dict):
        return [_truncate(payload)]

    lines: list[str] = []
    if payload.get("role_hint"):
        lines.append(f"Bundle role {payload['role_hint']}")
    if payload.get("repo_id"):
        lines.append(f"Repo {payload['repo_id']}")
    if payload.get("source_repo"):
        lines.append(f"Source {payload['source_repo']}")
    if payload.get("auto_assign_role"):
        lines.append(f"Auto-assign {payload['auto_assign_role']}")

    flash = payload.get("flash") or {}
    if isinstance(flash, dict):
        image_path = flash.get("flash_image_path") or flash.get("elf_path")
        if image_path:
            lines.append(f"Flash image {basename(image_path)}")

    files = payload.get("files") or []
    if isinstance(files, list) and files:
        lines.append(f"{len(files)} bundled file(s)")

    build_metadata = payload.get("build_metadata") or {}
    if isinstance(build_metadata, dict):
        lines.extend(_summarize_build_metadata(build_metadata))

    requested_build_config = payload.get("requested_build_config")
    if isinstance(requested_build_config, dict):
        lines.extend(_summarize_build_config(requested_build_config))

    uploaded_raw_artifacts = payload.get("uploaded_raw_artifacts") or []
    if isinstance(uploaded_raw_artifacts, list) and uploaded_raw_artifacts:
        lines.append(f"{len(uploaded_raw_artifacts)} raw upload(s)")

    build_log_upload_error = payload.get("build_log_upload_error")
    if build_log_upload_error:
        lines.append(f"Build log upload error: {_truncate(build_log_upload_error)}")

    if not lines:
        lines.extend(_summarize_mapping(payload, ignore={"files", "flash", "build_metadata"}))
    return _finalize_lines(lines)


def summarize_job_diagnostics(job_type: Any, diagnostics: dict[str, Any] | None) -> list[str]:
    payload = diagnostics or {}
    if not isinstance(payload, dict):
        return [_truncate(payload)]

    lines: list[str] = []
    if payload.get("error"):
        lines.append(f"Error: {_truncate(payload['error'])}")
    if payload.get("hint"):
        lines.append(f"Hint: {_truncate(payload['hint'])}")
    if payload.get("probe_serial"):
        lines.append(f"Probe {payload['probe_serial']}")
    if payload.get("flash_result") or payload.get("verify_result"):
        flash_result = payload.get("flash_result", "-")
        verify_result = payload.get("verify_result", "-")
        lines.append(f"Flash {flash_result} | Verify {verify_result}")
    if payload.get("return_code") is not None:
        lines.append(f"Return code {payload['return_code']}")
    if payload.get("capture_mode"):
        lines.append(f"Capture mode {humanize_token(payload['capture_mode'])}")

    path_labels = [
        ("bundle_path", "Bundle"),
        ("build_log_path", "Build log"),
        ("openocd_log_path", "OpenOCD log"),
        ("rtt_human_log_path", "RTT human log"),
        ("rtt_machine_log_path", "RTT machine log"),
        ("capture_command_log_path", "Capture command log"),
        ("event_log_path", "Agent events"),
        ("timing_samples_path", "Timing samples"),
    ]
    if not payload.get("rtt_human_log_path") and payload.get("rtt_log_path"):
        path_labels.append(("rtt_log_path", "RTT log"))

    for key, label in path_labels:
        if payload.get(key):
            lines.append(f"{label} {basename(payload[key])}")

    repo_context = payload.get("repo_context") or {}
    if isinstance(repo_context, dict) and repo_context.get("head_sha"):
        lines.append(f"Checked out {short_sha(repo_context['head_sha'])}")

    if payload.get("build_log_upload_error"):
        lines.append(f"Build log upload failed: {_truncate(payload['build_log_upload_error'])}")
    if payload.get("cleanup_error"):
        lines.append(f"Cleanup error: {_truncate(payload['cleanup_error'])}")

    if not lines:
        ignore = {"manifest", "repo_context", "stdout", "stderr"}
        lines.extend(_summarize_mapping(payload, ignore=ignore))
        if payload.get("stderr"):
            lines.append(f"stderr: {_truncate(payload['stderr'])}")
        elif payload.get("stdout"):
            lines.append(f"stdout: {_truncate(payload['stdout'])}")
    return _finalize_lines(lines)


def summarize_event_payload(event_type: Any, payload: dict[str, Any] | None) -> list[str]:
    body = payload or {}
    if not isinstance(body, dict):
        return [_truncate(body)]

    event_name = str(event_type or "")
    if event_name == "state_change":
        previous = humanize_token(body.get("from") or "none")
        current = humanize_token(body.get("to") or "unknown")
        return [f"State {previous} -> {current}"]
    if event_name == "job_update":
        lines: list[str] = []
        header: list[str] = []
        if body.get("role"):
            header.append(str(body["role"]))
        if body.get("job_type"):
            header.append(humanize_token(body["job_type"]))
        if body.get("status"):
            header.append(humanize_token(body["status"]))
        if body.get("success") is not None:
            header.append("ok" if body["success"] else "failed")
        if header:
            lines.append(" | ".join(header))
        if body.get("artifact_id"):
            lines.append(f"Artifact {short_id(body['artifact_id'])}")
        if body.get("job_id"):
            lines.append(f"Job {short_id(body['job_id'])}")
        if body.get("failure_reason"):
            lines.append(f"Reason: {_truncate(body['failure_reason'])}")
        return _finalize_lines(lines)
    if event_name == "annotation" and body.get("text"):
        return [_truncate(body["text"], 140)]
    if event_name == "capture" and body.get("planned_start_at"):
        return [f"Planned start {body['planned_start_at']}"]
    if event_name == "upload":
        artifact_type = humanize_token(body.get("type") or "upload")
        role = body.get("role")
        return [f"{artifact_type} | {role}" if role else artifact_type]
    if event_name == "diagnostic":
        if body.get("failure_reason"):
            return [f"Failure: {_truncate(body['failure_reason'])}"]
        if body.get("error"):
            return [f"Error: {_truncate(body['error'])}"]
    return _finalize_lines(_summarize_mapping(body))


def summarize_time_correction(correction: dict[str, Any] | None) -> list[str]:
    payload = correction or {}
    if not isinstance(payload, dict):
        return [_truncate(payload)]

    lines: list[str] = []
    if payload.get("source"):
        lines.append(f"Source {humanize_token(payload['source'])}")
    if payload.get("offset_ms") is not None:
        lines.append(f"Median offset {payload['offset_ms']} ms")
    if payload.get("sample_count") is not None:
        lines.append(f"{payload['sample_count']} sample(s)")

    diagnostics = payload.get("diagnostics") or {}
    if isinstance(diagnostics, dict) and diagnostics.get("median_offset_ms") is not None:
        value = diagnostics["median_offset_ms"]
        line = f"Measured median {value} ms"
        if line not in lines:
            lines.append(line)

    if not lines:
        lines.extend(_summarize_mapping(payload, ignore={"diagnostics"}))
    return _finalize_lines(lines)


def _summarize_build_metadata(build_metadata: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if build_metadata.get("repo_id"):
        lines.append(f"Repo {build_metadata['repo_id']}")
    if build_metadata.get("artifact_kind"):
        lines.append(f"Artifact kind {humanize_token(build_metadata['artifact_kind'])}")
    if build_metadata.get("source_path"):
        lines.append(f"Source {basename(build_metadata['source_path'])}")
    if build_metadata.get("dirty_worktree") is not None:
        lines.append("Dirty worktree yes" if build_metadata["dirty_worktree"] else "Dirty worktree no")

    requested_build_config = build_metadata.get("requested_build_config")
    if isinstance(requested_build_config, dict):
        lines.extend(_summarize_build_config(requested_build_config))
    return lines


def _summarize_build_config(build_config: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    header: list[str] = []
    detail_labels = {
        0: "Summary",
        1: "Packet",
    }
    machine_log_detail = build_config.get("machine_log_detail")
    if machine_log_detail is not None:
        header.append(f"Detail {detail_labels.get(machine_log_detail, machine_log_detail)}")
    if build_config.get("machine_log_stat_period_ms") is not None:
        header.append(f"Stat period {build_config['machine_log_stat_period_ms']} ms")
    if header:
        lines.append("Config " + " | ".join(header))
    return lines


def _summarize_mapping(payload: dict[str, Any], ignore: set[str] | None = None) -> list[str]:
    ignored = ignore or set()
    lines: list[str] = []
    for key, value in payload.items():
        if key in ignored or value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            lines.extend(_summarize_nested_mapping(key, value))
            continue
        if isinstance(value, list):
            if all(not isinstance(item, (dict, list)) for item in value):
                joined = ", ".join(_truncate(item, 24) for item in value[:3])
                suffix = "" if len(value) <= 3 else ", ..."
                lines.append(f"{_label(key)}: {joined}{suffix}")
            else:
                lines.append(f"{_label(key)}: {len(value)} item(s)")
            continue
        lines.append(f"{_label(key)}: {_truncate(value)}")
    return lines


def _summarize_nested_mapping(prefix: str, value: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    scalar_items = [
        f"{_label(key)} {_truncate(item, 24)}"
        for key, item in value.items()
        if item not in (None, "", [], {}) and not isinstance(item, (dict, list))
    ]
    if scalar_items:
        lines.append(f"{_label(prefix)}: {' | '.join(scalar_items[:3])}")
    return lines


def _finalize_lines(lines: list[str], *, max_lines: int = 5) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = " ".join(str(line).split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
        if len(output) >= max_lines:
            break
    return output


def _label(value: Any) -> str:
    return humanize_token(value).strip().capitalize()


def _truncate(value: Any, limit: int = 96) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _looks_like_identifier(value: str) -> bool:
    if len(value) < 8:
        return False
    return all(character.isalnum() or character == "-" for character in value)


def _json_default(value: Any) -> str:
    return str(value)


def pretty_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, indent=2, default=_json_default)
    except TypeError:
        return json.dumps(str(value), indent=2)
