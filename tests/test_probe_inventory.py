from agent.app.services.probes import ConnectedProbe, normalize_probe_serial, scan_probe_inventory


def test_scan_probe_inventory_auto_selects_single_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.app.services.probes._discover_connected_probes",
        lambda: [ConnectedProbe(serial="123456", description="ST-Link/V2-1")],
    )

    snapshot = scan_probe_inventory(configured_probe_serial=None)

    assert snapshot.connected_probe_count == 1
    assert snapshot.selected_probe_serial == "123456"
    assert snapshot.selection_reason == "auto_selected_single_probe"


def test_scan_probe_inventory_prefers_configured_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.app.services.probes._discover_connected_probes",
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
        "agent.app.services.probes._discover_connected_probes",
        lambda: [ConnectedProbe(serial='Tÿp\x06fuUU\x13D"\x87', description="ST-Link/V2-1")],
    )

    snapshot = scan_probe_inventory(configured_probe_serial="54ff70066675555513442287")

    assert snapshot.connected_probe_count == 1
    assert snapshot.connected_probes[0].serial == "54FF70066675555513442287"
    assert snapshot.selected_probe_serial == "54FF70066675555513442287"
    assert snapshot.selection_reason == "configured_probe_connected"


def test_scan_probe_inventory_requires_explicit_choice_when_multiple_probes_detected(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.app.services.probes._discover_connected_probes",
        lambda: [
            ConnectedProbe(serial="123456", description="ST-Link A"),
            ConnectedProbe(serial="654321", description="ST-Link B"),
        ],
    )

    snapshot = scan_probe_inventory(configured_probe_serial=None)

    assert snapshot.connected_probe_count == 2
    assert snapshot.selected_probe_serial is None
    assert snapshot.selection_reason == "multiple_probes_detected"
