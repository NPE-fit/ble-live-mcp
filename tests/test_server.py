"""In-process checks of the MCP server: tool registry, a decode round trip, resources."""

import asyncio
import json

import pytest

from ble_live import server

EXPECTED_TOOLS = {
    "spec_search",
    "spec_section",
    "list_fields",
    "list_quirks",
    "decode_frame",
    "decode_capture",
    "session_state",
    "reset_session",
    "encode_frame",
    "validate_advert",
    "plan_rotation",
    "analyze_rotation",
    "check_design",
    "generate_firmware",
    "example_config",
}


def run(coro):
    return asyncio.run(coro)


def structured(result):
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent
    text = next(c.text for c in result.content if getattr(c, "type", "") == "text")
    return json.loads(text)


def call(name, **args):
    result = run(server.mcp.call_tool(name, args))
    assert not getattr(result, "isError", False), getattr(result, "content", result)
    return structured(result)


def test_tool_registry():
    names = {t.name for t in run(server.mcp.list_tools())}
    assert EXPECTED_TOOLS <= names
    prompts = {p.name for p in run(server.mcp.list_prompts())}
    assert {"design_sensor", "debug_receiver", "review_layout"} <= prompts


def test_bad_form_is_a_tool_error():
    with pytest.raises(Exception, match="decode_frame"):
        run(server.mcp.call_tool("decode_frame", {"hex": "40 08 10 11", "form": "nope"}))


def test_decode_wymu_frames():
    call("reset_session", session="w")
    dfd = call("decode_frame", hex="B1 B6 C4 EB 40 08 10 11", session="w")
    assert dfd["kind"] == "DFD" and dfd["field_names"] == [
        "Power",
        "Power Event Count",
        "Accumulated Power",
    ]
    dfv = call("decode_frame", hex="0C FE B1 B6 C4 EB 00 07 20 00 05 A0 00", session="w")
    assert dfv["kind"] == "DFV" and dfv["values"] == {
        "power": 32,
        "power_event_count": 5,
        "accumulated_power": 160,
    }
    state = call("session_state", session="w")
    assert state["devices"]["0xEBC4B6B1"]["dfds"] == {"0": [8, 16, 17]}
    capture = call(
        "decode_capture",
        text=(
            "0.0 B1 B6 C4 EB FF 01 03\n"
            "0.5 B1 B6 C4 EB 48 00 02 07 08 0A\n"
            "1.0 B1 B6 C4 EB 08 1F 01 2C 01 02 02 0B\n"
        ),
        session="w",
    )
    assert capture["records"] == 3 and capture["errors"] == 0
    assert capture["devices"]["0xEBC4B6B1"]["last_values"]["equipment_type"] == 11


def test_encode_validate_plan():
    enc = call(
        "encode_frame",
        device_id=1,
        kind="DFV",
        dfv_index=0,
        field_indexes=[8, 16, 17],
        values={"8": 32, "16": 5, "17": 160},
    )
    assert enc["payload_hex"] == "07 20 00 05 A0 00"
    assert enc["structure_bytes"] == 13
    budget = call("validate_advert", live_payload_bytes=6, manufacturer_data_bytes=9)
    assert budget["fits"] and budget["total_bytes"] == 29
    plan = call("plan_rotation", dfv_indexes=[0, 8, 9], interval_ms=556.25)
    assert plan["stats"]["problems"] == [] and plan["pattern"][0] == 0xFF


def test_design_tools_with_example_config():
    cfg = call("example_config")
    report = call("check_design", config=cfg, interval_ms=546.25, manufacturer_data_bytes=6)
    assert report["errors"] == 0 and report["warnings"] >= 1
    fw = call("generate_firmware", config=cfg)
    assert "BLE_LIVE_FITNESS_DATA_POWER = 8" in fw["files"]["ble_live.h"]
    assert "static uint8_t tx_pattern[]" in fw["files"]["ble_live.c"]


def test_resources():
    toc = json.loads(next(iter(run(server.mcp.read_resource("spec://ble-live/toc")))).content)
    assert toc["spec_version"] == "1.7.0" and any(s["id"] == "dfd-packets" for s in toc["sections"])
    section = next(
        iter(run(server.mcp.read_resource("spec://ble-live/section/dfd-packets")))
    ).content
    assert "Data Field Definition" in section
    meta = json.loads(next(iter(run(server.mcp.read_resource("fields://meta")))).content)
    assert any(r["name"] == "Equipment Type" for r in meta)
    quirks = json.loads(next(iter(run(server.mcp.read_resource("quirks://receivers")))).content)
    assert any(q["id"] == "gatewaykit-beta-dfd-heuristic" for q in quirks["quirks"])
    search = call("spec_search", query="data presence identifier truncated")
    assert search["results"] and search["results"][0]["id"].startswith("dpi")
