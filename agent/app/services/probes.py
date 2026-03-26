from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STLINK_VENDOR_IDS = {"0483"}
_STLINK_NAME_TOKENS = ("stlink", "st-link")
_STLINK_MANUFACTURER_TOKENS = ("stmicro", "st micro")


@dataclass
class ConnectedProbe:
    serial: str | None
    description: str
    vendor_id: str | None = None
    product_id: str | None = None
    manufacturer: str | None = None
    product: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "serial": self.serial,
            "description": self.description,
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "manufacturer": self.manufacturer,
            "product": self.product,
        }


@dataclass
class ProbeInventorySnapshot:
    connected_probes: list[ConnectedProbe]
    configured_probe_serial: str | None
    selected_probe_serial: str | None
    selection_reason: str
    scan_error: str | None = None

    @property
    def connected_probe_count(self) -> int:
        return len(self.connected_probes)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "configured_probe_serial": self.configured_probe_serial,
            "selected_probe_serial": self.selected_probe_serial,
            "selection_reason": self.selection_reason,
            "connected_probe_count": self.connected_probe_count,
            "connected_probes": [probe.as_dict() for probe in self.connected_probes],
            **({"probe_scan_error": self.scan_error} if self.scan_error else {}),
        }


def scan_probe_inventory(*, configured_probe_serial: str | None, scan_enabled: bool = True) -> ProbeInventorySnapshot:
    configured_probe_serial = normalize_probe_serial(configured_probe_serial)
    if not scan_enabled:
        return ProbeInventorySnapshot(
            connected_probes=[],
            configured_probe_serial=configured_probe_serial,
            selected_probe_serial=configured_probe_serial,
            selection_reason="scan_disabled" if configured_probe_serial else "scan_disabled_no_probe",
        )
    try:
        probes = _discover_connected_probes()
        return _build_snapshot(configured_probe_serial=configured_probe_serial, probes=probes)
    except Exception as exc:  # pragma: no cover - best effort around platform commands
        logger.exception("probe discovery failed")
        return ProbeInventorySnapshot(
            connected_probes=[],
            configured_probe_serial=configured_probe_serial,
            selected_probe_serial=configured_probe_serial,
            selection_reason="configured_probe_unverified" if configured_probe_serial else "probe_scan_failed",
            scan_error=str(exc),
        )


def _build_snapshot(
    *,
    configured_probe_serial: str | None,
    probes: list[ConnectedProbe],
) -> ProbeInventorySnapshot:
    normalized_probes = _dedupe_probes([_normalize_connected_probe(probe) for probe in probes])
    if configured_probe_serial:
        serials = {probe.serial for probe in normalized_probes if probe.serial}
        return ProbeInventorySnapshot(
            connected_probes=normalized_probes,
            configured_probe_serial=configured_probe_serial,
            selected_probe_serial=configured_probe_serial,
            selection_reason=(
                "configured_probe_connected"
                if configured_probe_serial in serials or not normalized_probes
                else "configured_probe_not_detected"
            ),
        )
    if not normalized_probes:
        return ProbeInventorySnapshot(
            connected_probes=[],
            configured_probe_serial=None,
            selected_probe_serial=None,
            selection_reason="no_probes_detected",
        )
    if len(normalized_probes) == 1:
        return ProbeInventorySnapshot(
            connected_probes=normalized_probes,
            configured_probe_serial=None,
            selected_probe_serial=normalized_probes[0].serial,
            selection_reason="auto_selected_single_probe",
        )
    return ProbeInventorySnapshot(
        connected_probes=normalized_probes,
        configured_probe_serial=None,
        selected_probe_serial=None,
        selection_reason="multiple_probes_detected",
    )


def normalize_probe_serial(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate:
        return None
    compact = re.sub(r"[\s:-]", "", candidate)
    if compact.lower().startswith("0x"):
        compact = compact[2:]
    if re.fullmatch(r"[0-9A-Fa-f]+", compact):
        return compact.upper()
    if _is_ascii_printable(candidate):
        return candidate
    try:
        raw = candidate.encode("latin-1")
    except UnicodeEncodeError:
        return candidate
    return raw.hex().upper()


def _discover_connected_probes() -> list[ConnectedProbe]:
    system = platform.system()
    if system == "Linux":
        return _discover_linux_probes()
    if system == "Darwin":
        return _discover_macos_probes()
    if system == "Windows":
        return _discover_windows_probes()
    logger.warning("probe discovery is unsupported on platform=%s", system)
    return []


def _discover_linux_probes() -> list[ConnectedProbe]:
    probes: list[ConnectedProbe] = []
    usb_root = Path("/sys/bus/usb/devices")
    if not usb_root.exists():
        return probes
    for entry in usb_root.iterdir():
        vendor_id = _read_text(entry / "idVendor")
        product_id = _read_text(entry / "idProduct")
        manufacturer = _read_text(entry / "manufacturer")
        product = _read_text(entry / "product")
        serial = _read_text(entry / "serial")
        if not _looks_like_stlink(
            vendor_id=vendor_id,
            product_id=product_id,
            manufacturer=manufacturer,
            product=product,
            name=entry.name,
        ):
            continue
        probes.append(
            ConnectedProbe(
                serial=serial,
                description=_probe_description(manufacturer=manufacturer, product=product, fallback=entry.name),
                vendor_id=_normalize_hex_token(vendor_id),
                product_id=_normalize_hex_token(product_id),
                manufacturer=manufacturer,
                product=product,
            )
        )
    return _dedupe_probes(probes)


def _discover_macos_probes() -> list[ConnectedProbe]:
    completed = subprocess.run(
        ["system_profiler", "SPUSBDataType", "-json"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "system_profiler failed")
    payload = json.loads(completed.stdout or "{}")
    probes: list[ConnectedProbe] = []
    _walk_macos_usb_tree(payload, probes)
    return _dedupe_probes(probes)


def _discover_windows_probes() -> list[ConnectedProbe]:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_PnPEntity "
                "| Where-Object { $_.PNPDeviceID -like 'USB\\\\VID_0483&PID_*' } "
                "| Select-Object Name, Manufacturer, PNPDeviceID "
                "| ConvertTo-Json -Compress"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "powershell probe discovery failed")
    raw = completed.stdout.strip()
    if not raw:
        return []
    decoded = json.loads(raw)
    items = decoded if isinstance(decoded, list) else [decoded]
    probes: list[ConnectedProbe] = []
    for item in items:
        device_id = str(item.get("PNPDeviceID") or "")
        name = item.get("Name")
        manufacturer = item.get("Manufacturer")
        vendor_id = _normalize_hex_token(device_id)
        product_id = _extract_windows_product_id(device_id)
        if not _looks_like_stlink(
            vendor_id=vendor_id,
            product_id=product_id,
            manufacturer=manufacturer,
            product=name,
            name=name,
        ):
            continue
        probes.append(
            ConnectedProbe(
                serial=_extract_windows_serial(device_id),
                description=_probe_description(manufacturer=manufacturer, product=name, fallback=device_id),
                vendor_id=vendor_id,
                product_id=product_id,
                manufacturer=manufacturer,
                product=name,
            )
        )
    return _dedupe_probes(probes)


def _walk_macos_usb_tree(node: Any, probes: list[ConnectedProbe]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_macos_usb_tree(item, probes)
        return
    if not isinstance(node, dict):
        return
    vendor_id = _normalize_hex_token(node.get("vendor_id"))
    product_id = _normalize_hex_token(node.get("product_id"))
    manufacturer = node.get("manufacturer")
    product = node.get("_name") or node.get("product_name")
    serial = node.get("serial_num")
    if _looks_like_stlink(
        vendor_id=vendor_id,
        product_id=product_id,
        manufacturer=manufacturer,
        product=product,
        name=product,
    ):
        probes.append(
            ConnectedProbe(
                serial=serial,
                description=_probe_description(manufacturer=manufacturer, product=product, fallback="ST-Link"),
                vendor_id=vendor_id,
                product_id=product_id,
                manufacturer=manufacturer,
                product=product,
            )
        )
    for value in node.values():
        _walk_macos_usb_tree(value, probes)


def _looks_like_stlink(
    *,
    vendor_id: str | None,
    product_id: str | None,
    manufacturer: str | None,
    product: str | None,
    name: str | None,
) -> bool:
    normalized_vendor = _normalize_hex_token(vendor_id)
    if normalized_vendor not in _STLINK_VENDOR_IDS:
        return False
    haystack = " ".join(filter(None, [manufacturer, product, name, product_id])).lower()
    if any(token in haystack for token in _STLINK_NAME_TOKENS):
        return True
    if manufacturer and any(token in manufacturer.lower() for token in _STLINK_MANUFACTURER_TOKENS):
        return True
    return False


def _dedupe_probes(probes: list[ConnectedProbe]) -> list[ConnectedProbe]:
    seen: set[tuple[str | None, str, str | None, str | None]] = set()
    deduped: list[ConnectedProbe] = []
    for probe in probes:
        key = (probe.serial, probe.description, probe.vendor_id, probe.product_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(probe)
    return deduped


def _probe_description(*, manufacturer: str | None, product: str | None, fallback: str) -> str:
    description = " ".join(part for part in (manufacturer, product) if part).strip()
    return description or fallback


def _normalize_connected_probe(probe: ConnectedProbe) -> ConnectedProbe:
    return ConnectedProbe(
        serial=normalize_probe_serial(probe.serial),
        description=probe.description,
        vendor_id=probe.vendor_id,
        product_id=probe.product_id,
        manufacturer=probe.manufacturer,
        product=probe.product,
    )


def _is_ascii_printable(value: str) -> bool:
    return all(32 <= ord(char) <= 126 for char in value)


def _normalize_hex_token(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.search(r"0x([0-9A-Fa-f]+)", str(value))
    if match:
        return match.group(1).lower()
    match = re.search(r"VID_([0-9A-Fa-f]{4})", str(value))
    if match:
        return match.group(1).lower()
    normalized = str(value).strip().lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    if re.fullmatch(r"[0-9a-f]+", normalized):
        return normalized
    return None


def _extract_windows_product_id(value: str) -> str | None:
    match = re.search(r"PID_([0-9A-Fa-f]{4})", value)
    if not match:
        return None
    return match.group(1).lower()


def _extract_windows_serial(value: str) -> str | None:
    match = re.search(r"\\([^\\]+)$", value)
    if not match:
        return None
    candidate = match.group(1).strip()
    if not candidate or "&" in candidate:
        return None
    return candidate


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
