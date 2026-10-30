# ble_live (Python) and the BLE Live MCP server

Library and MCP server behind `../docs/mcp-server-plan.md`.

## MCP server

The server gives an AI coding assistant grounded BLE Live knowledge and working tools over stdio.

```bash
cd mcp
python -m pip install -e .          # installs ble-live and ble-live-mcp
```

Claude Code / Cursor / VS Code configuration (adjust the path):

```json
{
  "mcpServers": {
    "ble-live": {
      "command": "ble-live-mcp",
      "env": { "BLE_LIVE_ROOT": "C:/path/to/ble_live" }
    }
  }
}
```

Without an install: `"command": "python", "args": ["-m", "ble_live.server"]` with
`PYTHONPATH` pointing at `mcp/src`. The repository's `.mcp.json` does exactly that, so Claude
Code opened on this checkout loads the server automatically (it needs `pip install mcp openpyxl`).
`BLE_LIVE_ROOT` is only needed when the package does not run from inside this checkout; it
locates `ble_live_base.c/.h` and `live.xlsx` for `generate_firmware` and `example_config`.

| Surface | Name | What it does |
|---|---|---|
| resource | `spec://ble-live/toc`, `spec://ble-live/section/{id}` | spec text (AsciiDoc) by section |
| resource | `fields://fitness`, `fields://meta`, `fields://equipment` | field tables with sizes and units |
| resource | `spec://ble-live/di-ranges`, `vectors://frames`, `quirks://receivers` | DI map, golden frames, receiver deviations |
| resource | `refdesign://templates/{name}` | `ble_live_base.c` / `.h` |
| tool | `spec_search`, `spec_section`, `list_fields`, `list_quirks` | look things up |
| tool | `decode_frame`, `decode_capture`, `session_state`, `reset_session` | stateful decoding of frames; captures as hex lines, nRF Connect text, or `scan` over any hex dump |
| tool | `set_session_key`, `derive_key`, `encrypt_payload`, `decrypt_payload` | 0xFD encrypted frames: shared private key or per-device keys, kept in memory only |
| tool | `encode_frame`, `validate_advert` | build DPI/DFD/DFV frames (optionally encrypted); 31-byte budget |
| tool | `plan_rotation`, `analyze_rotation` | transmit patterns that meet the 4 s DFD rule |
| tool | `check_design`, `example_config`, `import_xlsx_file`, `export_xlsx_file`, `generate_firmware` | lint a configuration, convert workbooks, render `ble_live.c/.h` |
| tool | `scaffold_receiver` | decoder skeleton in python, c, csharp, kotlin or swift with the tables and quirks baked in |
| tool | `list_serial_ports`, `emulator_connect`, `emulator_status`, `emulator_set`, `emulator_auto`, `emulator_set_pattern`, `emulator_set_step`, `emulator_set_serial`, `emulator_command`, `emulator_disconnect` | drive the nRF52 emulator over its serial command channel (`port="mock"` for an in-process stand-in) |
| tool | `scan_live` | capture from the air for N seconds: a GEM3's multicast stream or this PC's Bluetooth adapter (Windows) |
| prompt | `design_sensor`, `debug_receiver`, `review_layout`, `build_receiver` | guided workflows |

### Emulator command channel

The demo firmware (`ble_live_demo/examples/ble_peripheral/ble_app_hrs`) exposes a line-based
CLI on the DK's virtual COM port, 115200 8N1 (`emulator_cli.h` documents it): `get`, `set
<field> <value>`, `auto on|off`, `step <ms>`, `pattern default|<hex,...>`, `serial <value>`,
`reset`. Replies are one `OK ...` or `ERR ...` line. Logging moved to RTT so the UART is free.
With a board on `COM6`:

```text
emulator_connect(port="COM6") → emulator_set(port="COM6", field="power", value=250)
plan_rotation(...) → emulator_set_pattern(port="COM6", pattern=[...]) → scan_live("bluetooth", 10)
```

The firmware builds for all four configurations with SEGGER Embedded Studio 6.22a (`emBuild
-config Debug ...emProject`); it has not yet been run on a board.

### Hosted instance

```bash
ble-live-mcp --transport http --host 0.0.0.0 --port 8765 --read-only --token "$BLE_LIVE_MCP_TOKEN"
docker build -t ble-live-mcp -f mcp/Dockerfile .      # from the repository root
```

`--read-only` removes the tools that touch the server's files, serial ports or radios
(`import_xlsx_file`, `export_xlsx_file`, the emulator tools, `scan_live`). A bearer token is
required unless the server binds to localhost. The endpoint is `/mcp` (streamable HTTP, JSON
responses, stateless). Where to host it (Azure Container Apps in the NPE Products subscription
is the natural fit) and whether the proprietary templates may ship in it are open owner decisions.

Decoding is stateful because a DFV is only meaningful with its DFD: feed DFDs (or a whole
capture) before DFVs, in the same `session`. 0xFD frames decrypt once the session has the
shared private key (`set_session_key`); the key derivation and cipher match the reference
design, GatewayKit and the WASP .NET library.

## Library

- `ble_live.spec` — protocol tables (`data/spec.json`), spec text (`data/spec_sections.json`),
  receiver quirks (`data/quirks.json`), golden frames (`data/vectors.json`).
- `ble_live.codec` — AD structure parsing, `LiveFrame`, stateful `Decoder` (with a `KeyRing`
  for 0xFD), encoders, `encrypt_frame`, advert budget.
- `ble_live.crypto` — AES-128-CTR in plain Python (FIPS-197 and NIST SP 800-38A vectors in the
  tests), key derivation, `KeyRing`.
- `ble_live.capture` — hex-line, nRF Connect and scan capture parsing with diagnostics.
- `ble_live.rotation` — `analyze(pattern, interval_ms)` and `plan(dfv_indexes, interval_ms)`.
- `ble_live.design` — `check_design(config)` lints.
- `ble_live.scaffold` — receiver skeletons; the Python one is executed and the C one compiled
  (gcc) against the golden vectors in the tests.
- `ble_live.config` / `ble_live.xlsx` — configuration model, JSON, `live.xlsx` import and export.
- `ble_live.generator` — renders `ble_live.c/.h` from a configuration and the templates. Output for
  the repository's `live.xlsx` matches the committed files byte for byte
  (`tests/test_generator_regression.py`).

```bash
ble-live generate ../live.xlsx --templates .. --out ..
ble-live import-xlsx ../live.xlsx -o live.json
ble-live export-xlsx live.json -o live-copy.xlsx
ble-live spec fields meta
python -m pytest
ruff check . && ruff format --check .
```

`python live.py` at the repository root still works; it is a shim over this package.

## Regenerating the spec data

```bash
ADOC="../../application-notes/ble live/ble-live-protocol.adoc"
python scripts/extract_spec.py "$ADOC" src/ble_live/data/spec.json
python scripts/extract_sections.py "$ADOC" src/ble_live/data/spec_sections.json
```

Both JSON files are committed so the package does not depend on the docs checkout.

## Not yet

Running the emulator firmware and `scan_live` against real hardware (no DK or GEM3 was
attached when this was written; the mock transport and loopback multicast are what the tests
cover), a GEM3-specific record parser (the structure scan does without one), btsnoop/PCAP
input, deploying the hosted instance, and a run of the WASP .NET library against captured
frames. The .NET field sizes were compared by reading its source (`tests/test_dotnet_parity.py`).
