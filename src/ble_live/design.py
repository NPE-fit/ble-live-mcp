"""Design lints for a BLE Live configuration: spec rules, advert budget and receiver quirks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import rotation, spec
from .codec import (
    HEART_BEAT_COUNT_INDEX,
    RR_INTERVALS_INDEX,
    AdvertLayout,
    advert_budget,
    category_for_dfv,
    encode_dpi,
)
from .config import LiveConfig, Struct
from .generator import byte_size

# DFD first bytes GatewayKit's beta-DFD heuristic treats as a flag byte when the popcount
# equals the number of bytes that follow. Recorded from NPEBleFitnessData.m (2026-10-21).
GATEWAYKIT_FLAG_LIKE = {0x01, 0x03, 0x07, 0x0F, 0x1F, 0x3F, 0x4F, 0x7F, 0xFF}


@dataclass
class Finding:
    severity: str  # error | warning | info
    code: str
    message: str
    where: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.where:
            d["where"] = self.where
        return d


def dfv_fields(cfg: LiveConfig) -> dict[int, list[tuple[Struct, Any]]]:
    """DFV index -> included (struct, field) pairs in generator order."""
    out: dict[int, list[tuple[Struct, Any]]] = {}
    for s in cfg.structs:
        for f in s.included:
            if f.dfv is not None:
                out.setdefault(f.dfv, []).append((s, f))
    return out


def frame_payload_sizes(cfg: LiveConfig) -> dict[int, int]:
    """Payload bytes after the data identifier, per DI the generated firmware sends.

    Arrays (repeat > 1) count their full length; the firmware truncates them to the buffer.
    """
    sizes: dict[int, int] = {}
    by_dfv = dfv_fields(cfg)
    dpi_bits = [d for d in by_dfv if 0 <= d <= 63]
    sizes[spec.DI_DPI] = 8 if dpi_bits else 0
    for dfv, pairs in by_dfv.items():
        sizes[dfv | spec.DI_DFD_BIT] = len(pairs)
        sizes[dfv] = 1 + sum(byte_size(f.type) * f.repeat for _, f in pairs)
    return sizes


def legacy_tx_pattern(cfg: LiveConfig) -> list[int]:
    """The pattern the generator emits: DPI, every DFD once, then each struct's DFVs repeated."""
    by_dfv = dfv_fields(cfg)
    pattern = [spec.DI_DPI] + [d | spec.DI_DFD_BIT for d in by_dfv]
    for s in cfg.structs:
        for _ in range(s.repeat):
            seen: list[int] = []
            for f in s.included:
                if f.dfv is not None and f.dfv not in seen:
                    pattern.append(f.dfv)
                    seen.append(f.dfv)
    return pattern


def check_design(
    cfg: LiveConfig,
    interval_ms: float | None = None,
    layout: AdvertLayout | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    for p in cfg.validate():
        findings.append(Finding("error", "config", p))
    by_dfv = dfv_fields(cfg)
    if not by_dfv:
        findings.append(Finding("error", "empty", "no included fields"))
        return findings

    for dfv, pairs in sorted(by_dfv.items()):
        category = category_for_dfv(dfv)
        indexes = [f.value for _, f in pairs]
        where = f"DFV {dfv}"
        if category == "reserved":
            findings.append(
                Finding("error", "dfv-range", f"DFV {dfv} is in a reserved range", where)
            )
        for s, f in pairs:
            if category in ("fitness", "meta"):
                try:
                    row = spec.field_by_index(category, f.value)
                except KeyError:
                    findings.append(
                        Finding(
                            "warning",
                            "unknown-field",
                            f"{s.name}/{f.name}: index {f.value} is not in the spec's "
                            f"{category} table",
                            where,
                        )
                    )
                    continue
                expected = row.get("size_bytes")
                if expected is not None and f.repeat == 1 and byte_size(f.type) != expected:
                    findings.append(
                        Finding(
                            "error",
                            "field-size",
                            f"{s.name}/{f.name}: C type {f.type} is {byte_size(f.type)} byte(s), "
                            f"the spec says {expected}",
                            where,
                        )
                    )
        # GatewayKit beta-DFD heuristic
        first = indexes[0]
        if first in GATEWAYKIT_FLAG_LIKE and bin(first).count("1") == len(indexes) - 1:
            findings.append(
                Finding(
                    "warning",
                    "gatewaykit-dfd-heuristic",
                    f"DFD for DFV {dfv} starts with 0x{first:02X} followed by {len(indexes) - 1} "
                    f"byte(s); GatewayKit drops it as a flag byte. Reorder the fields.",
                    where,
                )
            )
        if category == "fitness" and RR_INTERVALS_INDEX in indexes:
            if indexes[-1] != RR_INTERVALS_INDEX:
                findings.append(
                    Finding(
                        "error",
                        "rr-not-last",
                        "R-R Intervals must be the last field of its DFV",
                        where,
                    )
                )
            if HEART_BEAT_COUNT_INDEX not in indexes:
                findings.append(
                    Finding(
                        "warning",
                        "rr-without-heartbeat",
                        "R-R Intervals should be preceded by Heart Beat Count in the same DFV",
                        where,
                    )
                )
        if category == "meta":
            if 10 in indexes:
                findings.append(
                    Finding(
                        "info",
                        "equipment-type-numbering",
                        "Equipment Type: the spec numbers Table 10 from 1, GatewayKit from 0, "
                        "and the WYMU sends 11 meaning the ANT+ bike power type (Table 10: "
                        "Running Speed). State which numbering your receivers expect.",
                        where,
                    )
                )
            if 4 in indexes:
                findings.append(
                    Finding(
                        "warning",
                        "hw-version-width",
                        "Hardware Version is Uint8 in Table 6 but two bytes in Appendix B and in "
                        "GatewayKit; receivers disagree. Prefer leaving it out.",
                        where,
                    )
                )

    # advert budget per frame
    sizes = frame_payload_sizes(cfg)
    layout = layout or AdvertLayout()
    worst_di, worst_size = max(sizes.items(), key=lambda kv: kv[1])
    for di, payload in sorted(sizes.items()):
        lay = AdvertLayout(**{**layout.__dict__, "live_payload_bytes": payload})
        budget = advert_budget(lay)
        if not budget["fits"]:
            findings.append(
                Finding(
                    "error",
                    "advert-budget",
                    f"frame 0x{di:02X} needs {budget['total_bytes']} bytes with the other "
                    f"structures; the advertising packet holds 31",
                    f"DI 0x{di:02X}",
                )
            )
    largest = advert_budget(AdvertLayout(**{**layout.__dict__, "live_payload_bytes": worst_size}))
    findings.append(
        Finding(
            "info",
            "largest-frame",
            f"largest frame is 0x{worst_di:02X} with a {worst_size}-byte payload: "
            f"{largest['total_bytes']} of 31 bytes used, {largest['remaining_bytes']} spare",
        )
    )

    dpi_payload = sizes.get(spec.DI_DPI, 0)
    if dpi_payload:
        truncated = len(encode_dpi([d for d in by_dfv if 0 <= d <= 63]))
        if truncated < dpi_payload:
            findings.append(
                Finding(
                    "info",
                    "dpi-truncation",
                    f"the generator emits all 8 DPI bytes; the spec allows truncating after the "
                    f"last non-zero byte, {truncated} byte(s) here",
                    "DI 0xFF",
                )
            )

    # rotation the generator would emit
    if interval_ms:
        stats = rotation.analyze(legacy_tx_pattern(cfg), interval_ms)
        for p in stats.problems:
            findings.append(
                Finding(
                    "warning",
                    "legacy-rotation",
                    f"generated tx_pattern at {interval_ms:g} ms: {p}. Use plan_rotation for a "
                    f"pattern that meets the limit.",
                )
            )
    return findings
