# ble_live (Python)

Library behind the BLE Live MCP server (see `../docs/mcp-server-plan.md`). Phase 0 ships:

- `ble_live.spec` — the protocol tables (fitness fields, meta fields, equipment types, DI
  ranges) as JSON, extracted from the AsciiDoc spec by `scripts/extract_spec.py`.
- `ble_live.config` — the configuration model (structs, fields, enums, DFV assignment) with
  JSON load/save.
- `ble_live.xlsx` — import the historical `live.xlsx` workbook into the model and export it back.
- `ble_live.generator` — renders `ble_live.c` / `ble_live.h` from a configuration and the
  `ble_live_base.c/.h` templates. Output for the repository's `live.xlsx` is identical to what the
  original `live.py` produced; `tests/test_generator_regression.py` guards that.
- `ble_live.data.vectors` — golden frames from the spec's Appendix B and the WYMU v1.7.0 layout.

## Use

```bash
cd mcp
python -m pip install -e .[dev]          # or: pip install openpyxl pytest ruff
ble-live generate ../live.xlsx --templates .. --out ..
ble-live import-xlsx ../live.xlsx -o live.json
ble-live export-xlsx live.json -o live-copy.xlsx
ble-live spec fields meta
python -m pytest
```

`python live.py` at the repository root still works; it is now a shim over this package.

## Regenerating the spec JSON

```bash
python scripts/extract_spec.py "../../application-notes/ble live/ble-live-protocol.adoc" \
    src/ble_live/data/spec.json
```

The JSON is committed so the package does not depend on the docs checkout.
