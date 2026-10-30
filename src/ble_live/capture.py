"""Parse captures of advertising frames and run them through a Decoder.

Supported inputs:
- hex lines: one frame per line, optionally prefixed by a timestamp in seconds or HH:MM:SS(.fff);
  each frame is an AD structure, a whole advertising payload, or device-id-first data.
- nRF Connect for Mobile text: lines containing `Unknown EIR (0xFE): <hex>` (or 0xFD), which is
  the structure data without length and type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .codec import Decoder, LiveFrame, hex_to_bytes, parse_frame, parse_live_data

NRF_RE = re.compile(
    r"0x(FE|FD)\)?\s*:\s*(?:0x)?([0-9A-Fa-f]{2}(?:[\s:,-]*(?:0x)?[0-9A-Fa-f]{2})*)", re.I
)
HEX_LINE_RE = re.compile(
    r"^\s*(?:(?P<ts>\d{1,2}:\d{2}:\d{2}(?:\.\d+)?|\d+(?:\.\d+)?)[\s,;]+)?"
    r"(?P<hex>(?:0x)?[0-9A-Fa-f]{2}(?:[\s:,-]*(?:0x)?[0-9A-Fa-f]{2})*)\s*$"
)


@dataclass
class CaptureRecord:
    line_no: int
    timestamp_s: float | None
    raw: bytes
    encrypted_hint: bool | None = None
    form: str = "auto"


def _timestamp(text: str | None) -> float | None:
    if not text:
        return None
    if ":" in text:
        h, m, s = text.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    return float(text)


def parse_capture(text: str, fmt: str = "auto") -> tuple[list[CaptureRecord], list[str]]:
    records: list[CaptureRecord] = []
    skipped: list[str] = []
    for no, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = NRF_RE.search(line) if fmt in ("auto", "nrf-connect") else None
        if m:
            records.append(
                CaptureRecord(
                    no,
                    None,
                    hex_to_bytes(m.group(2)),
                    encrypted_hint=m.group(1).upper() == "FD",
                    form="data",
                )
            )
            continue
        if fmt == "nrf-connect":
            skipped.append(f"line {no}: no 0xFE/0xFD EIR entry")
            continue
        h = HEX_LINE_RE.match(line)
        if not h:
            skipped.append(f"line {no}: not a hex frame")
            continue
        try:
            records.append(
                CaptureRecord(no, _timestamp(h.group("ts")), hex_to_bytes(h.group("hex")))
            )
        except ValueError as exc:
            skipped.append(f"line {no}: {exc}")
    return records, skipped


def _frame(record: CaptureRecord) -> LiveFrame:
    if record.form == "data":
        return parse_live_data(record.raw, encrypted=bool(record.encrypted_hint))
    try:
        return parse_frame(record.raw, "auto")
    except ValueError:
        return parse_live_data(record.raw)


def decode_capture(
    text: str,
    decoder: Decoder | None = None,
    fmt: str = "auto",
    dedupe: bool = True,
    max_frames: int = 200,
    dfd_gap_limit_s: float = 4.0,
) -> dict[str, Any]:
    decoder = decoder or Decoder()
    records, skipped = parse_capture(text, fmt)
    frames: list[dict[str, Any]] = []
    duplicates = 0
    dfv_before_dfd = 0
    errors = 0
    last_raw: bytes | None = None
    dfd_times: dict[tuple[int, int], list[float]] = {}
    for rec in records:
        if dedupe and rec.raw == last_raw:
            duplicates += 1
            continue
        last_raw = rec.raw
        try:
            frame = _frame(rec)
        except ValueError as exc:
            skipped.append(f"line {rec.line_no}: {exc}")
            continue
        decoded = decoder.decode(frame)
        if decoded.error:
            errors += 1
            if decoded.error.startswith("DFD "):
                dfv_before_dfd += 1
        if decoded.kind == "DFD" and rec.timestamp_s is not None and frame.dfv_index is not None:
            dfd_times.setdefault((frame.device_id, frame.dfv_index), []).append(rec.timestamp_s)
        if len(frames) < max_frames:
            d = decoded.to_dict()
            d["line"] = rec.line_no
            if rec.timestamp_s is not None:
                d["t"] = rec.timestamp_s
            frames.append(d)
    diagnostics: list[str] = []
    if dfv_before_dfd:
        diagnostics.append(
            f"{dfv_before_dfd} DFV frame(s) arrived before their DFD and could not be decoded"
        )
    for (device_id, dfv), times in dfd_times.items():
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        if gaps and max(gaps) > dfd_gap_limit_s:
            diagnostics.append(
                f"device 0x{device_id:08X}: DFD for DFV {dfv} had a {max(gaps):.1f} s gap "
                f"(limit {dfd_gap_limit_s:g} s)"
            )
    for device_id, st in decoder.devices.items():
        if st.dpi is not None:
            missing = [d for d in st.dpi if d not in st.dfds]
            if missing:
                diagnostics.append(
                    f"device 0x{device_id:08X}: DPI announces DFV {missing} "
                    f"but no DFD was seen for them"
                )
        if st.frames and st.kinds.get("DFV", 0) == 0:
            diagnostics.append(
                f"device 0x{device_id:08X}: no DFV frames; only definitions were captured"
            )
    return {
        "records": len(records),
        "decoded": len(records) - duplicates - (len(skipped)),
        "duplicates_collapsed": duplicates,
        "skipped": skipped,
        "errors": errors,
        "devices": {f"0x{k:08X}": v.to_dict() for k, v in decoder.devices.items()},
        "diagnostics": diagnostics,
        "frames": frames,
        "frames_truncated": max(0, len(records) - duplicates - len(frames)),
    }
