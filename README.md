# BLE Live MCP server

An [MCP](https://modelcontextprotocol.io) server that gives AI coding assistants (Claude Code,
Cursor, VS Code, Claude Desktop) grounded knowledge of NPE's **BLE Live** protocol and working
tools to build sensors and receivers for it. BLE Live puts real-time fitness data (power, heart
rate, cadence, R-R intervals, ...) in the BLE advertising packet, so any scanner can read it
without a connection. The [protocol specification](https://cdn.shopify.com/s/files/1/0558/2074/6861/files/ble-live-protocol.pdf)
is the source of the tables and text the server serves.

The same code is a plain Python library (`ble_live`) with a CLI, usable without any MCP client.

## Install

Python 3.11 or newer.

```bash
pip install "git+https://github.com/NPE-fit/ble-live-mcp.git@main"
```

With the bench extras (serial port to the nRF52 emulator, Bluetooth scanning):

```bash
pip install "ble-live-mcp[bench] @ git+https://github.com/NPE-fit/ble-live-mcp.git@main"
```

Or run it without installing, with [uv](https://docs.astral.sh/uv/):

```bash
uvx --from "git+https://github.com/NPE-fit/ble-live-mcp.git@main" ble-live-mcp
```

`main` holds tagged releases (`v0.1.0` is the first); `develop` is the integration branch.

## Connect your assistant

**Claude Code**

```bash
claude mcp add ble-live -- ble-live-mcp
```

or, project-wide, commit this `.mcp.json` next to your code:

```json
{ "mcpServers": { "ble-live": { "command": "ble-live-mcp" } } }
```

**Cursor, VS Code (Copilot agent mode), Claude Desktop, Windsurf** — add the same server to
the client's MCP configuration:

```json
{
  "mcpServers": {
    "ble-live": {
      "command": "ble-live-mcp",
      "env": { "BLE_LIVE_ROOT": "/path/to/npe-ble-live-reference-design" }
    }
  }
}
```

`BLE_LIVE_ROOT` is optional. It points at a checkout of NPE's BLE Live reference design, whose
`ble_live_base.c/.h` templates the `generate_firmware` tool fills in. Every other tool works
without it. Ask NPE for the reference design.

Then ask your assistant things like:

- "Decode this advert from nRF Connect: `Unknown EIR (0xFE): B1 B6 C4 EB 40 08 10 11`"
- "I'm building a rower that broadcasts stroke rate and power. Design its BLE Live packets,
  check the 31-byte budget and plan a rotation for a 211 ms advertising interval."
- "Scaffold a Kotlin receiver for Android and show me how to feed it scan records."
- "Why does my gateway see the device but never decodes values? Here is a capture..."

The server ships with prompts for exactly these workflows: `design_sensor`, `debug_receiver`,
`review_layout`, `build_receiver`.

## What the server provides

| Kind | Name | What it does |
|---|---|---|
| resource | `spec://ble-live/toc`, `spec://ble-live/section/{id}` | spec text by section |
| resource | `fields://fitness`, `fields://meta`, `fields://equipment` | field tables with sizes and units |
| resource | `spec://ble-live/di-ranges`, `vectors://frames`, `quirks://receivers` | data identifier map, golden frames, how real receivers deviate from the PDF |
| resource | `refdesign://templates/{name}` | the reference design templates (needs `BLE_LIVE_ROOT`) |
| tool | `spec_search`, `spec_section`, `list_fields`, `list_quirks` | look things up |
| tool | `decode_frame`, `decode_capture`, `session_state`, `reset_session` | stateful decoding of single frames or captures (hex lines, nRF Connect text, or `scan` over any hex dump) |
| tool | `set_session_key`, `derive_key`, `encrypt_payload`, `decrypt_payload` | 0xFD encrypted frames; keys stay in memory |
| tool | `encode_frame`, `validate_advert` | build DPI/DFD/DFV frames; check the 31-byte advertising budget |
| tool | `plan_rotation`, `analyze_rotation` | transmit patterns that meet the spec's 4 s definition rule |
| tool | `check_design`, `example_config`, `import_xlsx_file`, `export_xlsx_file`, `generate_firmware` | lint a sensor configuration, convert the reference design's workbook, render `ble_live.c/.h` |
| tool | `scaffold_receiver` | decoder skeleton in Python, C, C#, Kotlin or Swift with the tables and quirks baked in |
| tool | `list_serial_ports`, `emulator_*` | drive NPE's nRF52 emulator firmware over its serial command channel (`port="mock"` for an in-process stand-in) |
| tool | `scan_live` | capture frames from the air: a GEM3 gateway's multicast stream, or this PC's Bluetooth adapter (Windows) |

Decoding is stateful because a value packet (DFV) is only meaningful with its definition
packet (DFD): feed definitions, or a whole capture, before values, in the same `session`.
Encrypted (0xFD) frames decrypt once the session has the shared private key.

## Command line

```bash
ble-live spec fields meta                      # the meta field table as JSON
ble-live import-xlsx live.xlsx -o live.json    # reference design workbook -> JSON config
ble-live export-xlsx live.json -o live.xlsx
ble-live generate live.json --templates /path/to/reference-design --out .
ble-live-mcp --help                            # transports and hosting options
```

## Hosting a shared instance

```bash
ble-live-mcp --transport http --host 0.0.0.0 --port 8765 --read-only --token "$BLE_LIVE_MCP_TOKEN"
docker build -t ble-live-mcp . && docker run -p 8765:8765 -e BLE_LIVE_MCP_TOKEN=change-me ble-live-mcp
```

`--read-only` removes the tools that touch the host's files, serial ports or radios. A bearer
token is required unless the server binds to localhost. The endpoint is `/mcp` (streamable
HTTP). Mount a reference design checkout and set `BLE_LIVE_ROOT` to enable `generate_firmware`.

## Development

```bash
git clone https://github.com/NPE-fit/ble-live-mcp.git && cd ble-live-mcp
pip install -e ".[dev,bench]"
pytest            # ~110 tests; the generator regression test runs when BLE_LIVE_ROOT is set
ruff check . && ruff format --check .
```

The spec tables and text (`src/ble_live/data/spec*.json`) are extracted from the AsciiDoc
source of the specification with `scripts/extract_spec.py` and `scripts/extract_sections.py`;
the JSON is committed so the package does not depend on that source. `data/quirks.json`
records how real receivers deviate from the PDF; `data/vectors.json` holds golden frames from
the spec's Appendix B and NPE's WYMU sensor; `data/examples/power_meter.json` is the sample
configuration `example_config` returns.

Branching follows NPE's standard: `main` holds releases (annotated `vX.Y.Z` tags), `develop`
is the integration branch, work happens on `feature/*` and `bugfix/*` branches.

## License

To be confirmed by North Pole Engineering, Inc. Until a license file is added, all rights are
reserved. The BLE Live specification is published separately by NPE; the firmware templates
that `generate_firmware` needs are part of NPE's reference design and are not included here.
