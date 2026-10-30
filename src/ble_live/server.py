"""The BLE Live MCP server: spec resources, codec and design tools, workflow prompts.

Run with `ble-live-mcp` (stdio). The tool functions are plain Python so they can be tested
without a transport; `mcp` wires them up.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__, crypto, rotation, spec
from .capture import decode_capture as _decode_capture
from .codec import (
    AdvertLayout,
    Decoder,
    LiveFrame,
    advert_budget,
    bytes_to_hex,
    category_for_dfv,
    encode_dfd,
    encode_dfv,
    encode_dpi,
    encrypt_frame,
    hex_to_bytes,
)
from .config import LiveConfig
from .design import check_design as _check_design
from .generator import generate
from .scaffold import LANGUAGES, scaffold
from .xlsx import export_xlsx, import_xlsx

INSTRUCTIONS = """BLE Live is NPE's format for real-time fitness data in BLE advertising packets
(AD type 0xFE plain, 0xFD encrypted). A device announces which DFV packets it sends (DPI),
defines each packet's fields (DFD), then sends values (DFV). Decoding a DFV needs its DFD, so
decode tools keep per-session state: feed DFDs (or a whole capture) before DFVs. For 0xFD
frames give the session the shared private key with set_session_key first.

Start with spec_search or the fields:// resources for definitions, decode_frame /
decode_capture for data from the air, check_design + plan_rotation + validate_advert when
designing a sensor, generate_firmware to produce ble_live.c/.h, scaffold_receiver for a
decoder skeleton in another language. quirks://receivers lists how real receivers deviate
from the PDF; read it before choosing equipment types or DFD orders."""

mcp = MCPServer(
    "ble-live",
    instructions=INSTRUCTIONS,
    version=__version__,
)

_sessions: dict[str, Decoder] = {}


def _decoder(session: str) -> Decoder:
    return _sessions.setdefault(session, Decoder())


def repo_root() -> Path | None:
    """Where ble_live_base.c/.h live: $BLE_LIVE_ROOT or the checkout this package sits in."""
    env = os.environ.get("BLE_LIVE_ROOT")
    candidates = [Path(env)] if env else []
    candidates.append(Path(__file__).resolve().parents[3])
    for c in candidates:
        if (c / "ble_live_base.c").exists() and (c / "ble_live_base.h").exists():
            return c
    return None


def _require_root() -> Path:
    root = repo_root()
    if root is None:
        raise FileNotFoundError("templates not found; set BLE_LIVE_ROOT to the ble_live checkout")
    return root


# -- resources --------------------------------------------------------------------------------


@mcp.resource("spec://ble-live/toc", mime_type="application/json")
def resource_toc() -> dict[str, Any]:
    """Table of contents of the BLE Live spec with section ids."""
    return {"spec_version": spec.version(), "sections": spec.toc()}


@mcp.resource("spec://ble-live/section/{section_id}", mime_type="text/plain")
def resource_section(section_id: str) -> str:
    """One spec section as AsciiDoc text."""
    s = spec.section(section_id)
    return f"{'=' * s['level']} {s['title']}\n\n{s['body']}"


@mcp.resource("spec://ble-live/di-ranges", mime_type="application/json")
def resource_di_ranges() -> list[dict[str, Any]]:
    """Data Identifier range map: DFV 0-63, DFD 64-127, DFA 254, DPI 255."""
    return spec.load_spec()["di_ranges"]


@mcp.resource("fields://{category}", mime_type="application/json")
def resource_fields(category: str) -> list[dict[str, Any]]:
    """Field table: fitness, meta or equipment."""
    return spec.fields(category)


@mcp.resource("vectors://frames", mime_type="application/json")
def resource_vectors() -> dict[str, Any]:
    """Golden frames from the spec's Appendix B and the WYMU v1.7.0 layout."""
    return spec.load_vectors()


@mcp.resource("quirks://receivers", mime_type="application/json")
def resource_quirks() -> dict[str, Any]:
    """How real receivers (WASP, GatewayKit, iOS) deviate from the PDF, with design rules."""
    return spec.load_quirks()


@mcp.resource("refdesign://templates/{name}", mime_type="text/x-c")
def resource_template(name: str) -> str:
    """Reference design template: ble_live_base.c or ble_live_base.h."""
    if name not in ("ble_live_base.c", "ble_live_base.h"):
        raise ValueError("name is ble_live_base.c or ble_live_base.h")
    return (_require_root() / name).read_text(encoding="utf-8")


# -- tools: understanding ---------------------------------------------------------------------


@mcp.tool()
def spec_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the BLE Live spec text. Returns section ids (read them with spec_section) and
    excerpts."""
    return {"spec_version": spec.version(), "results": spec.search(query, limit)}


@mcp.tool()
def spec_section(section_id: str) -> dict[str, Any]:
    """Return one spec section (AsciiDoc) by id from spec_search or spec://ble-live/toc."""
    s = spec.section(section_id)
    return {"id": s["id"], "title": s["title"], "level": s["level"], "body": s["body"]}


@mcp.tool()
def list_fields(category: str) -> dict[str, Any]:
    """Field table for 'fitness' (DFV 0-7), 'meta' (DFV 8-15) or 'equipment' (equipment types)."""
    return {"category": category, "fields": spec.fields(category)}


@mcp.tool()
def list_quirks() -> dict[str, Any]:
    """Known receiver deviations from the spec and the design rules they impose."""
    return spec.load_quirks()


# -- tools: codec -----------------------------------------------------------------------------


@mcp.tool()
def decode_frame(hex: str, session: str = "default", form: str = "auto") -> dict[str, Any]:
    """Decode one BLE Live frame.

    hex: an AD structure (length, 0xFE/0xFD, data), a whole advertising payload, or structure
    data starting at the device id (nRF Connect's 'Unknown EIR (0xFE):' value). form: auto, ad,
    advert, data, data-encrypted. DFVs decode only after their DFD was seen in the same session;
    0xFD frames decrypt only after set_session_key.
    """
    return _decoder(session).decode_bytes(hex_to_bytes(hex), form).to_dict()


@mcp.tool()
def decode_capture(
    text: str,
    session: str = "default",
    format: str = "auto",
    dedupe: bool = True,
    max_frames: int = 200,
) -> dict[str, Any]:
    """Decode many frames. format: 'auto' (hex lines with an optional leading timestamp in
    seconds or HH:MM:SS, or nRF Connect text with 'Unknown EIR (0xFE): ...'), 'nrf-connect',
    'hex-lines', or 'scan' (any hex dump such as GEM3 datagrams; every 0xFE/0xFD structure found
    in a line is decoded). Returns per-device state, decoded frames and diagnostics (DFV before
    DFD, DFD gaps over 4 s, DPI without DFD)."""
    return _decode_capture(text, _decoder(session), format, dedupe, max_frames)


@mcp.tool()
def session_state(session: str = "default") -> dict[str, Any]:
    """Per-device DPI, DFDs and last values learned by this decode session, and which devices
    have keys."""
    dec = _decoder(session)
    return {
        "devices": {f"0x{k:08X}": v.to_dict() for k, v in dec.devices.items()},
        "private_key_set": dec.keys.private_key is not None,
        "keyed_devices": [f"0x{d:08X}" for d in dec.keys.device_ids],
    }


@mcp.tool()
def reset_session(session: str = "default") -> dict[str, Any]:
    """Forget a decode session's learned DFDs and keys."""
    _sessions.pop(session, None)
    return {"session": session, "reset": True}


@mcp.tool()
def set_session_key(
    session: str = "default",
    private_key_hex: str | None = None,
    key_hex: str | None = None,
    device_id: int | None = None,
    nonce_hex: str | None = None,
) -> dict[str, Any]:
    """Give a decode session key material for 0xFD frames: the shared 16-byte private key
    (each device's key is derived from it on first sight), or one device's derived 16-byte key
    with its device_id. nonce_hex overrides the default 01..0C. Keys live only in this server
    process and are never written anywhere."""
    dec = _decoder(session)
    if nonce_hex:
        dec.keys.nonce = hex_to_bytes(nonce_hex)
    if private_key_hex:
        private = hex_to_bytes(private_key_hex)
        if len(private) != crypto.KEY_BYTES:
            raise ValueError("the private key is 16 bytes")
        dec.keys.private_key = private
    if key_hex:
        if device_id is None:
            raise ValueError("key_hex needs device_id")
        dec.keys.set_key(device_id, hex_to_bytes(key_hex))
    return {
        "session": session,
        "private_key_set": dec.keys.private_key is not None,
        "keyed_devices": [f"0x{d:08X}" for d in dec.keys.device_ids],
    }


@mcp.tool()
def derive_key(private_key_hex: str, device_id: int) -> dict[str, Any]:
    """Device key for 0xFD: SHA-256(private key || device id little-endian)[0:16], as the
    reference design, GatewayKit and the WASP .NET library compute it."""
    key = crypto.derive_key(hex_to_bytes(private_key_hex), device_id)
    return {"device_id_hex": f"0x{device_id:08X}", "key_hex": bytes_to_hex(key)}


@mcp.tool()
def encrypt_payload(
    key_hex: str, iv_counter: int, payload_hex: str, nonce_hex: str | None = None
) -> dict[str, Any]:
    """Encrypt a BLE Live payload (flag byte and values) with a device key: AES-128-CTR, counter
    block = nonce || iv_counter little-endian. The same operation decrypts."""
    nonce = hex_to_bytes(nonce_hex) if nonce_hex else crypto.DEFAULT_NONCE
    out = crypto.crypt_payload(hex_to_bytes(key_hex), iv_counter, hex_to_bytes(payload_hex), nonce)
    return {"iv_counter": iv_counter, "result_hex": bytes_to_hex(out)}


@mcp.tool()
def decrypt_payload(
    key_hex: str, iv_counter: int, payload_hex: str, nonce_hex: str | None = None
) -> dict[str, Any]:
    """Decrypt a 0xFD payload with a device key (see encrypt_payload; CTR is symmetric)."""
    return encrypt_payload(key_hex, iv_counter, payload_hex, nonce_hex)


@mcp.tool()
def encode_frame(
    device_id: int,
    kind: str,
    dfv_index: int | None = None,
    field_indexes: list[int] | None = None,
    values: dict[str, int | list[int]] | None = None,
    dfv_indexes: list[int] | None = None,
    encrypted: bool = False,
    iv_counter: int = 0,
    key_hex: str | None = None,
    private_key_hex: str | None = None,
) -> dict[str, Any]:
    """Build a BLE Live frame.

    kind 'DPI': dfv_indexes present. kind 'DFD': dfv_index + field_indexes (spec indexes for the
    category of that DFV). kind 'DFV': dfv_index + field_indexes (the DFD) + values keyed by
    field index as strings; a field listed twice in the DFD takes a list, R-R intervals (254)
    take the whole list. encrypted builds the 0xFD form; with key_hex (device key) or
    private_key_hex (shared key, derived per device) the payload is really encrypted,
    otherwise only the header is shaped.
    """
    kind = kind.upper()
    if kind == "DPI":
        di, payload = spec.DI_DPI, encode_dpi(dfv_indexes or [])
    elif kind == "DFD":
        if dfv_index is None or not field_indexes:
            raise ValueError("DFD needs dfv_index and field_indexes")
        di, payload = dfv_index | spec.DI_DFD_BIT, encode_dfd(field_indexes)
    elif kind == "DFV":
        if dfv_index is None or not field_indexes:
            raise ValueError("DFV needs dfv_index and field_indexes (its DFD)")
        payload = encode_dfv(
            category_for_dfv(dfv_index),
            field_indexes,
            {int(k): v for k, v in (values or {}).items()},
        )
        di = dfv_index
    else:
        raise ValueError("kind is DPI, DFD or DFV")
    frame = LiveFrame(device_id, di, payload)
    sealed = False
    if encrypted:
        key = None
        if key_hex:
            key = hex_to_bytes(key_hex)
        elif private_key_hex:
            key = crypto.derive_key(hex_to_bytes(private_key_hex), device_id)
        if key is not None:
            frame = encrypt_frame(frame, key, iv_counter)
            sealed = True
        else:
            frame = LiveFrame(device_id, di, payload, True, iv_counter)
    return {
        "kind": kind,
        "data_identifier": di,
        "payload_hex": bytes_to_hex(payload),
        "encrypted": encrypted,
        "payload_encrypted": sealed,
        "data_hex": bytes_to_hex(frame.to_data()),
        "ad_structure_hex": bytes_to_hex(frame.to_bytes()),
        "structure_bytes": frame.size,
    }


@mcp.tool()
def validate_advert(
    live_payload_bytes: int,
    flags: bool = True,
    manufacturer_data_bytes: int | None = None,
    name_bytes: int = 0,
    uuids16: int = 0,
    uuids128: int = 0,
    appearance: bool = False,
    tx_power: bool = False,
    encrypted: bool = False,
) -> dict[str, Any]:
    """31-byte advertising budget for a BLE Live frame (payload bytes after the data
    identifier) beside flags, manufacturer data (bytes after the company id), name, UUIDs."""
    return advert_budget(
        AdvertLayout(
            flags=flags,
            manufacturer_data_bytes=manufacturer_data_bytes,
            name_bytes=name_bytes,
            uuids16=uuids16,
            uuids128=uuids128,
            appearance=appearance,
            tx_power=tx_power,
            live_payload_bytes=live_payload_bytes,
            encrypted=encrypted,
        )
    )


# -- tools: design ----------------------------------------------------------------------------


@mcp.tool()
def plan_rotation(
    dfv_indexes: list[int],
    interval_ms: float,
    dfd_gap_limit_s: float = 4.0,
    dpi_period_s: float = 20.0,
    meta_period_s: float = 20.0,
    phase: str = "slow",
) -> dict[str, Any]:
    """Propose a transmit pattern (data identifiers) for these DFV indexes at an advertising
    interval: every DFD within the gap limit, DPI and meta DFVs once per cycle, fitness DFVs in
    the remaining slots. phase 'fast' gives the start-up rotation."""
    pattern = rotation.plan(
        dfv_indexes, interval_ms, dfd_gap_limit_s, dpi_period_s, meta_period_s, phase
    )
    return {
        "pattern": pattern,
        "pattern_hex": [f"0x{d:02X}" for d in pattern],
        "labels": rotation.describe(pattern),
        "stats": rotation.analyze(pattern, interval_ms, dfd_gap_limit_s).to_dict(),
    }


@mcp.tool()
def analyze_rotation(
    pattern: list[int], interval_ms: float, dfd_gap_limit_s: float = 4.0
) -> dict[str, Any]:
    """Measure an existing pattern of data identifiers: DFD gaps, DFV rates, DPI period,
    problems."""
    return {
        "labels": rotation.describe(pattern),
        "stats": rotation.analyze(pattern, interval_ms, dfd_gap_limit_s).to_dict(),
    }


@mcp.tool()
def check_design(
    config: dict[str, Any],
    interval_ms: float | None = None,
    manufacturer_data_bytes: int | None = None,
    name_bytes: int = 0,
    uuids16: int = 0,
    uuids128: int = 0,
) -> dict[str, Any]:
    """Lint a sensor configuration (the JSON form of live.xlsx, see import_xlsx_file): spec
    rules, field sizes, GatewayKit DFD heuristic, per-frame advert budget, and the generated
    rotation at interval_ms."""
    cfg = LiveConfig.from_dict(config)
    layout = AdvertLayout(
        manufacturer_data_bytes=manufacturer_data_bytes,
        name_bytes=name_bytes,
        uuids16=uuids16,
        uuids128=uuids128,
    )
    findings = _check_design(cfg, interval_ms, layout)
    return {
        "errors": sum(f.severity == "error" for f in findings),
        "warnings": sum(f.severity == "warning" for f in findings),
        "findings": [f.to_dict() for f in findings],
    }


@mcp.tool()
def generate_firmware(config: dict[str, Any]) -> dict[str, Any]:
    """Render ble_live.c and ble_live.h from a configuration using the reference design
    templates (BLE_LIVE_ROOT or the checkout this server runs from)."""
    root = _require_root()
    cfg = LiveConfig.from_dict(config)
    problems = cfg.validate()
    if problems:
        return {"errors": problems}
    return {"templates": str(root), "files": generate(cfg, root)}


@mcp.tool()
def example_config() -> dict[str, Any]:
    """The WYMU v1.7.0 configuration (power meter) as a starting point for check_design and
    generate_firmware."""
    root = repo_root()
    if root is None or not (root / "live.xlsx").exists():
        raise FileNotFoundError("live.xlsx not found; set BLE_LIVE_ROOT to the ble_live checkout")
    cfg = import_xlsx(root / "live.xlsx")
    cfg.spec_version = spec.version()
    return cfg.to_dict()


@mcp.tool()
def import_xlsx_file(path: str) -> dict[str, Any]:
    """Read a live.xlsx workbook into the configuration JSON used by check_design and
    generate_firmware."""
    cfg = import_xlsx(path)
    cfg.spec_version = cfg.spec_version or spec.version()
    return cfg.to_dict()


@mcp.tool()
def export_xlsx_file(config: dict[str, Any], path: str) -> dict[str, Any]:
    """Write a configuration as a live.xlsx workbook (for people who edit the spreadsheet)."""
    cfg = LiveConfig.from_dict(config)
    export_xlsx(cfg, path)
    return {"wrote": str(Path(path).resolve()), "objects": len(cfg.objects)}


@mcp.tool()
def scaffold_receiver(language: str) -> dict[str, Any]:
    """A self-contained BLE Live decoder skeleton with the spec tables, the DPI/DFD/DFV state
    machine, a 0xFD decryption hook and the receiver quirks as comments. language: python, c,
    csharp, kotlin or swift (python and c are tested against the golden vectors)."""
    if language not in LANGUAGES:
        raise ValueError(f"language is one of {LANGUAGES}")
    return scaffold(language)


# -- prompts ----------------------------------------------------------------------------------


@mcp.prompt()
def design_sensor(equipment: str, measurements: str) -> str:
    """Walk through designing a BLE Live sensor: fields, DFV grouping, budget, rotation, code."""
    return f"""I am building a {equipment} that measures: {measurements}.
Help me design its BLE Live broadcast:
1. Read quirks://receivers, then pick fitness and meta field indexes from fields://fitness
   and fields://meta.
2. Group them into DFV packets (fitness 0-7, meta 8-15, at most 8 fields each) and choose an
   Equipment Type, noting the numbering ambiguity.
3. Build the configuration JSON (start from example_config) and run check_design with my
   advertising interval and manufacturer data size.
4. Run validate_advert for the largest frame and plan_rotation for my interval.
5. Run generate_firmware and explain how to feed values through the generated setters."""


@mcp.prompt()
def debug_receiver() -> str:
    """Diagnose a receiver that sees the device but decodes no values."""
    return """My receiver sees the BLE Live device but does not decode values. Ask me for a capture
(nRF Connect 'Unknown EIR (0xFE)' lines, hex lines with timestamps, or a raw dump for the scan
format), run decode_capture on it, and check: is the DPI present, is every announced DFD seen
within 4 s, do DFVs arrive before their DFD, do frame sizes fit 31 bytes, are frames 0xFD and
the key missing, and does any quirk in quirks://receivers explain the behaviour."""


@mcp.prompt()
def review_layout(advert_hex: str) -> str:
    """Review a captured advertising payload against the spec and the budget."""
    return f"""Review this advertising payload: {advert_hex}
Decode it with decode_frame (form advert), list every AD structure and its size with
validate_advert, check the BLE Live structure's data identifier and payload against the spec
tables, and point out anything the receiver quirks in quirks://receivers would trip over."""


@mcp.prompt()
def build_receiver(language: str, platform: str) -> str:
    """Start a receiver implementation from the scaffold for a language and platform."""
    return f"""I am writing a BLE Live receiver in {language} for {platform}.
Run scaffold_receiver for {language}, explain how to feed it the 0xFE/0xFD advertising
structures on {platform} (note the CoreBluetooth limitation on iOS from quirks://receivers),
then show how to verify it with the frames in vectors://frames and, for 0xFD, how to
implement the decrypt hook with derive_key and decrypt_payload."""


# -- bench tools (emulator serial channel, live scanning) ------------------------------------

from .hardware_tools import register as _register_hardware_tools  # noqa: E402

_register_hardware_tools(mcp, _decoder)


def main(argv: list[str] | None = None) -> None:
    """Entry point: `ble-live-mcp` (stdio) or `ble-live-mcp --transport http --read-only`."""
    from .hosting import run_from_args

    run_from_args(mcp, argv)


if __name__ == "__main__":
    main()
