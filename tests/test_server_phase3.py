"""Phase 3 server tools against the in-process mock emulator."""

import asyncio

from ble_live import server
from ble_live.hardware_tools import HARDWARE_TOOLS


def call(name, **args):
    result = asyncio.run(server.mcp.call_tool(name, args))
    assert not getattr(result, "isError", False), result
    return result.structured_content


def test_hardware_tools_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert set(HARDWARE_TOOLS) <= names


def test_mock_emulator_flow():
    opened = call("emulator_connect", port="mock")
    assert opened["state"]["equip"] == 11 and opened["banner"]
    done = call("emulator_set", port="mock", field="power", value=250)
    assert done["auto"] == {"auto": 0} and done["set"] == {"power": 250}
    plan = call("plan_rotation", dfv_indexes=[0, 8, 9], interval_ms=556.25)
    applied = call("emulator_set_pattern", port="mock", pattern=plan["pattern"])
    assert applied["reply"].startswith("OK pattern=")
    status = call("emulator_status", port="mock")["state"]
    assert status["power"] == 250 and status["pattern"] == plan["pattern"] and status["auto"] == 0
    assert call("emulator_set_step", port="mock", ms=556)["step"] == 556
    assert call("emulator_set_serial", port="mock", serial_number=0x1234)["serial"] == 0x1234
    assert (
        call("emulator_command", port="mock", line="pattern default")["reply"]
        == "OK pattern=default"
    )
    assert call("emulator_auto", port="mock", on=True)["auto"] == 1
    assert call("emulator_disconnect", port="mock")["closed"]
    ports = call("list_serial_ports")["ports"]
    assert all(not (p["vid"] == "043E" and p["pid"] == "9A39") for p in ports)
