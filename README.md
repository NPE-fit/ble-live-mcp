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
Code opened on this checkout loads the server automatically (it needs `pip install mcp openpyxl`). `BLE_LIVE_ROOT` is only needed when the package does not run
from inside this checkout; it locates `ble_live_base.c/.h` and `live.xlsx` for
`generate_firmware` and `example_config`.

| Surface | Name | What it does |
|---|---|---|
| resource | `spec://ble-live/toc`, `spec://ble-live/section/{id}` | spec text (AsciiDoc) by section |
| resource | `fields://fitness`, `fields://meta`, `fields://equipment` | field tables with sizes and units |
| resource | `spec://ble-live/di-ranges`, `vectors://frames`, `quirks://receivers` | DI map, golden frames, receiver deviations |
| resource | `refdesign://templates/{name}` | `ble_live_base.c` / `.h` |
| tool | `spec_search`, `spec_section`, `list_fields`, `list_quirks` | look things up |
| tool | `decode_frame`, `decode_capture`, `session_state`, `reset_session` | stateful decoding of frames, hex lines or nRF Connect text |
| tool | `encode_frame`, `validate_advert` | build DPI/DFD/DFV frames; 31-byte budget |
| tool | `plan_rotation`, `analyze_rotation` | transmit patterns that meet the 4 s DFD rule |
| tool | `check_design`, `example_config`, `generate_firmware` | lint a configuration, start from the WYMU one, render `ble_live.c/.h` |
| prompt | `design_sensor`, `debug_receiver`, `review_layout` | guided workflows |

Decoding is stateful because a DFV is only meaningful with its DFD: feed DFDs (or a whole
capture) before DFVs, in the same `session`.

## Library

- `ble_live.spec` — protocol tables (`data/spec.json`), spec text (`data/spec_sections.json`),
  receiver quirks (`data/quirks.json`), golden frames (`data/vectors.json`).
- `ble_live.codec` — AD structure parsing, `LiveFrame`, stateful `Decoder`, encoders, advert budget.
- `ble_live.capture` — hex-line and nRF Connect capture parsing with diagnostics.
- `ble_live.rotation` — `analyze(pattern, interval_ms)` and `plan(dfv_indexes, interval_ms)`.
- `ble_live.design` — `check_design(config)` lints.
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

0xFD decryption, GEM3 multicast and btsnoop capture formats, receiver scaffolds, and the nRF52
emulator bridge are Phase 2 and 3 in the plan. The decoder has not yet been cross-checked against
the WASP .NET library on captured frames.
