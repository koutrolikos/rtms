from rtms.host.app.services.probes import (
    ConnectedProbe,
    _discover_macos_probes,
    _parse_ioreg_usb_host_devices,
    normalize_probe_serial,
    scan_probe_inventory,
)


def test_scan_probe_inventory_auto_selects_single_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "rtms.host.app.services.probes._discover_connected_probes",
        lambda: [ConnectedProbe(serial="123456", description="ST-Link/V2-1")],
    )

    snapshot = scan_probe_inventory(configured_probe_serial=None)

    assert snapshot.connected_probe_count == 1
    assert snapshot.selected_probe_serial == "123456"
    assert snapshot.selection_reason == "auto_selected_single_probe"


def test_scan_probe_inventory_prefers_configured_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "rtms.host.app.services.probes._discover_connected_probes",
        lambda: [ConnectedProbe(serial="123456", description="ST-Link/V2-1")],
    )

    snapshot = scan_probe_inventory(configured_probe_serial="abcdef")

    assert snapshot.connected_probe_count == 1
    assert snapshot.selected_probe_serial == "ABCDEF"
    assert snapshot.selection_reason == "configured_probe_not_detected"


def test_normalize_probe_serial_converts_non_printable_stlink_bytes_to_hex() -> None:
    assert normalize_probe_serial('Tÿp\x06fuUU\x13D"\x87') == "54FF70066675555513442287"


def test_scan_probe_inventory_normalizes_probe_serials_before_selection(monkeypatch) -> None:
    monkeypatch.setattr(
        "rtms.host.app.services.probes._discover_connected_probes",
        lambda: [ConnectedProbe(serial='Tÿp\x06fuUU\x13D"\x87', description="ST-Link/V2-1")],
    )

    snapshot = scan_probe_inventory(configured_probe_serial="54ff70066675555513442287")

    assert snapshot.connected_probe_count == 1
    assert snapshot.connected_probes[0].serial == "54FF70066675555513442287"
    assert snapshot.selected_probe_serial == "54FF70066675555513442287"
    assert snapshot.selection_reason == "configured_probe_connected"


def test_scan_probe_inventory_requires_explicit_choice_when_multiple_probes_detected(monkeypatch) -> None:
    monkeypatch.setattr(
        "rtms.host.app.services.probes._discover_connected_probes",
        lambda: [
            ConnectedProbe(serial="123456", description="ST-Link A"),
            ConnectedProbe(serial="654321", description="ST-Link B"),
        ],
    )

    snapshot = scan_probe_inventory(configured_probe_serial=None)

    assert snapshot.connected_probe_count == 2
    assert snapshot.selected_probe_serial is None
    assert snapshot.selection_reason == "multiple_probes_detected"


def test_parse_ioreg_usb_host_devices_extracts_stlink_probe() -> None:
    output = """
+-o STM32 STLink  <class IOUSBHostDevice, id 0x123, registered, matched, active, busy 0 (0 ms), retain 20>
  | {
  |   "idVendor" = 0x0483
  |   "idProduct" = 0x3748
  |   "USB Vendor Name" = "STMicroelectronics"
  |   "USB Product Name" = "STM32 STLink"
  |   "USB Serial Number" = "54FF70066675555513442287"
  | }
"""

    probes = _parse_ioreg_usb_host_devices(output)

    assert len(probes) == 1
    assert probes[0].serial == "54FF70066675555513442287"
    assert probes[0].vendor_id == "0483"
    assert probes[0].product_id == "3748"


def test_discover_macos_probes_falls_back_to_ioreg_when_system_profiler_is_empty(monkeypatch) -> None:
    class Completed:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "system_profiler":
            return Completed(returncode=0, stdout='{"SPUSBDataType": []}')
        if command[0] == "ioreg":
            return Completed(
                returncode=0,
                stdout="""
+-o STM32 STLink  <class IOUSBHostDevice, id 0x123, registered, matched, active, busy 0 (0 ms), retain 20>
  | {
  |   "idVendor" = 1155
  |   "idProduct" = 14152
  |   "USB Vendor Name" = "STMicroelectronics"
  |   "USB Product Name" = "STM32 STLink"
  |   "USB Serial Number" = "54FF70066675555513442287"
  | }
""",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("rtms.host.app.services.probes.subprocess.run", fake_run)

    probes = _discover_macos_probes()

    assert len(probes) == 1
    assert probes[0].serial == "54FF70066675555513442287"
    assert [command[0] for command in calls] == ["system_profiler", "ioreg"]
