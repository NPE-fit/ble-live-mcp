import pytest

from ble_live import emulator as emu


def test_mock_round_trip():
    client = emu.connect("mock")
    assert client.info and client.info[0].startswith("#")
    state = client.get()
    assert state["power"] == 20 and state["equip"] == 11 and state["pattern"] is None
    assert state["serial"] == 0xEBC4B6B1 and state["auto"] == 1 and state["step"] == 550

    assert client.set_auto(False) == {"auto": 0}
    assert client.set_value("power", 123) == {"power": 123}
    assert client.set_value("EQUIP", 0x78) == {"equip": 120}
    assert client.set_step_ms(600) == {"step": 600}
    assert client.set_serial(0x100126FB) == {"serial": 0x100126FB}
    assert client.set_pattern([0xFF, 0x40, 0x00, 0x48, 0x08]).startswith("OK pattern=5")
    state = client.get()
    assert state["pattern"] == [0xFF, 0x40, 0x00, 0x48, 0x08] and state["power"] == 123
    assert client.set_pattern(None) == "OK pattern=default"
    assert client.reset() == "OK resetting"
    assert client.get()["auto"] == 1


def test_mock_errors_match_firmware_messages():
    client = emu.connect("mock")
    with pytest.raises(emu.EmulatorError, match="at most 255"):
        client.set_value("events", 300)
    with pytest.raises(emu.EmulatorError, match="unknown field"):
        client.set_value("speed", 1)
    with pytest.raises(emu.EmulatorError, match="20..5000"):
        client.set_step_ms(5)
    with pytest.raises(emu.EmulatorError, match="not a DPI, DFD or DFV"):
        client.set_pattern([0x80])
    with pytest.raises(emu.EmulatorError, match="unknown command"):
        client.command("frobnicate")
    with pytest.raises(emu.EmulatorError, match="not a number"):
        client.command("set power abc")


def test_parse_kv():
    parsed = emu.parse_kv("OK power=32 serial=0xEBC4B6B1 pattern=FF,40,00 auto=0 note=x")
    assert parsed == {
        "power": 32,
        "serial": 0xEBC4B6B1,
        "pattern": [0xFF, 0x40, 0x00],
        "auto": 0,
        "note": "x",
    }
    assert emu.parse_kv("OK pattern=default")["pattern"] is None


class FakePort:
    def __init__(self, device, vid, pid, description="x"):
        self.device, self.vid, self.pid = device, vid, pid
        self.description, self.serial_number = description, None


def test_list_ports_hides_lg_monitor_and_flags_dks(monkeypatch):
    import types

    fake = types.SimpleNamespace(
        comports=lambda: [
            FakePort("COM3", 0x8086, 0x7E73),
            FakePort("COM6", 0x1366, 0x1025, "JLink CDC UART Port"),
            FakePort("COM40", 0x043E, 0x9A39, "LG Monitor Controls"),
            FakePort("COM24", 0x1915, 0x8600),
        ]
    )
    monkeypatch.setitem(__import__("sys").modules, "serial.tools.list_ports", fake)
    ports = emu.list_ports()
    devices = [p["device"] for p in ports]
    assert "COM40" not in devices
    assert devices[:2] == ["COM24", "COM6"] or devices[:2] == ["COM6", "COM24"]
    assert all(p["likely_dk"] for p in ports if p["device"] in ("COM6", "COM24"))
    assert not next(p for p in ports if p["device"] == "COM3")["likely_dk"]
