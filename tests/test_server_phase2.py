"""Phase 2 server tools: keys and 0xFD, scaffolds, xlsx files."""

import asyncio

from ble_live import crypto, server
from ble_live.codec import bytes_to_hex

PRIVATE_HEX = bytes_to_hex(bytes(range(16)))
DEVICE = 0xEBC4B6B1


def call(name, **args):
    result = asyncio.run(server.mcp.call_tool(name, args))
    assert not getattr(result, "isError", False), result
    return result.structured_content


def test_tool_registry_phase2():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {
        "set_session_key",
        "derive_key",
        "encrypt_payload",
        "decrypt_payload",
        "scaffold_receiver",
        "import_xlsx_file",
        "export_xlsx_file",
    } <= names
    prompts = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    assert "build_receiver" in prompts


def test_encrypted_flow_through_tools():
    call("reset_session", session="e")
    derived = call("derive_key", private_key_hex=PRIVATE_HEX, device_id=DEVICE)
    assert derived["key_hex"] == bytes_to_hex(crypto.derive_key(bytes(range(16)), DEVICE))

    dfd = call("encode_frame", device_id=DEVICE, kind="DFD", dfv_index=0, field_indexes=[8, 16, 17])
    enc = call(
        "encode_frame",
        device_id=DEVICE,
        kind="DFV",
        dfv_index=0,
        field_indexes=[8, 16, 17],
        values={"8": 32, "16": 5, "17": 160},
        encrypted=True,
        iv_counter=9,
        private_key_hex=PRIVATE_HEX,
    )
    assert enc["payload_encrypted"] and enc["ad_structure_hex"].split()[1] == "FD"
    assert enc["structure_bytes"] == 2 + 4 + 4 + 1 + 6

    call("decode_frame", hex=dfd["ad_structure_hex"], session="e")
    locked = call("decode_frame", hex=enc["ad_structure_hex"], session="e")
    assert "no key" in locked["error"]

    state = call("set_session_key", session="e", private_key_hex=PRIVATE_HEX)
    assert state["private_key_set"]
    opened = call("decode_frame", hex=enc["ad_structure_hex"], session="e")
    assert opened["decrypted"] and opened["values"]["power"] == 32
    assert call("session_state", session="e")["keyed_devices"] == ["0xEBC4B6B1"]

    plain = call(
        "decrypt_payload",
        key_hex=derived["key_hex"],
        iv_counter=9,
        payload_hex=enc["data_hex"][27:],
    )
    assert plain["result_hex"] == "07 20 00 05 A0 00"


def test_scan_format_through_tool():
    call("reset_session", session="s")
    text = (
        "0.0 4E 1B 00 09 FE B1 B6 C4 EB 40 08 10 11 FF\n"
        "0.5 0C FE B1 B6 C4 EB 00 07 20 00 05 A0 00 00 00\n"
    )
    report = call("decode_capture", text=text, session="s", format="scan")
    assert report["errors"] == 0 and report["records"] == 2
    assert report["devices"]["0xEBC4B6B1"]["last_values"]["accumulated_power"] == 160


def test_scaffold_and_xlsx_tools(tmp_path):
    py = call("scaffold_receiver", language="python")
    assert py["filename"] == "ble_live_receiver.py" and "class LiveDecoder" in py["source"]
    cfg = call("example_config")
    out = call("export_xlsx_file", config=cfg, path=str(tmp_path / "copy.xlsx"))
    assert out["objects"] == 3
    again = call("import_xlsx_file", path=str(tmp_path / "copy.xlsx"))
    assert again == cfg
