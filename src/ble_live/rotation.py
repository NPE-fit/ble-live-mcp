"""Transmit rotation: analyse a pattern of data identifiers or plan one that meets the spec.

The spec asks for the DPI first during fast advertising, every DFD at least every 4 seconds
during slow advertising, and a rotation made mostly of DFV packets. `analyze` measures a
pattern against those rules; `plan` builds one for a set of DFV indexes and an interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import spec
from .codec import category_for_dfv

DFD_GAP_LIMIT_S = 4.0
DI_DPI = spec.DI_DPI


@dataclass
class RotationStats:
    interval_ms: float
    cycle_frames: int
    cycle_s: float
    dfd_max_gap_s: dict[int, float] = field(default_factory=dict)
    """Worst time between two transmissions of each DFD, keyed by DFV index."""
    dfv_rate_hz: dict[int, float] = field(default_factory=dict)
    dpi_period_s: float | None = None
    dfv_share: float = 0.0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval_ms": self.interval_ms,
            "cycle_frames": self.cycle_frames,
            "cycle_s": round(self.cycle_s, 3),
            "dfd_max_gap_s": {str(k): round(v, 3) for k, v in self.dfd_max_gap_s.items()},
            "dfv_rate_hz": {str(k): round(v, 3) for k, v in self.dfv_rate_hz.items()},
            "dpi_period_s": None if self.dpi_period_s is None else round(self.dpi_period_s, 3),
            "dfv_share": round(self.dfv_share, 3),
            "problems": self.problems,
        }


def _max_circular_gap(positions: list[int], length: int) -> int:
    if not positions:
        return length
    if len(positions) == 1:
        return length
    gaps = [b - a for a, b in zip(positions, positions[1:], strict=False)]
    gaps.append(length - positions[-1] + positions[0])
    return max(gaps)


def analyze(
    pattern: list[int], interval_ms: float, dfd_gap_limit_s: float = DFD_GAP_LIMIT_S
) -> RotationStats:
    if not pattern:
        raise ValueError("empty pattern")
    if interval_ms <= 0:
        raise ValueError("interval must be positive")
    n = len(pattern)
    cycle_s = n * interval_ms / 1000
    stats = RotationStats(interval_ms=interval_ms, cycle_frames=n, cycle_s=cycle_s)
    dfd_pos: dict[int, list[int]] = {}
    dfv_count: dict[int, int] = {}
    dpi_count = 0
    for i, di in enumerate(pattern):
        kind = spec.di_kind(di)
        if kind == "DPI":
            dpi_count += 1
        elif kind == "DFD":
            dfd_pos.setdefault(di & 0x3F, []).append(i)
        elif kind == "DFV":
            dfv_count[di] = dfv_count.get(di, 0) + 1
        else:
            stats.problems.append(f"position {i}: data identifier 0x{di:02X} is {kind}")
    for dfv, positions in dfd_pos.items():
        gap = _max_circular_gap(positions, n) * interval_ms / 1000
        stats.dfd_max_gap_s[dfv] = gap
        if gap > dfd_gap_limit_s:
            stats.problems.append(
                f"DFD for DFV {dfv} repeats every {gap:.2f} s, over the {dfd_gap_limit_s:g} s limit"
            )
    for dfv, count in dfv_count.items():
        stats.dfv_rate_hz[dfv] = count / cycle_s
        if dfv not in dfd_pos:
            stats.problems.append(f"DFV {dfv} is sent but its DFD is not in the pattern")
    for dfv in dfd_pos:
        if dfv not in dfv_count:
            stats.problems.append(f"DFD for DFV {dfv} is sent but no DFV {dfv} is")
    if dpi_count:
        stats.dpi_period_s = cycle_s / dpi_count
    else:
        stats.problems.append("no DPI (0xFF) in the pattern")
    stats.dfv_share = sum(dfv_count.values()) / n
    return stats


def plan(
    dfv_indexes: list[int],
    interval_ms: float,
    dfd_gap_limit_s: float = DFD_GAP_LIMIT_S,
    dpi_period_s: float = 20.0,
    meta_period_s: float = 20.0,
    phase: str = "slow",
) -> list[int]:
    """Build a rotation for the given DFV indexes.

    slow: blocks of k frames (k = largest count with k * interval <= gap limit), each block
    carries every DFD once, interleaved with DFV slots; the DPI and the meta DFVs take one
    slot per cycle, fitness DFVs fill the rest. fast: DPI then each DFD/DFV pair, as the spec
    FAQ recommends for the first 30 seconds.
    """
    dfvs = sorted(set(dfv_indexes))
    if not dfvs:
        raise ValueError("no DFV indexes")
    if interval_ms <= 0:
        raise ValueError("interval must be positive")
    dfds = [d | spec.DI_DFD_BIT for d in dfvs]
    if phase == "fast":
        out = [DI_DPI]
        for d in dfvs:
            out += [d | spec.DI_DFD_BIT, d]
        return out
    if phase != "slow":
        raise ValueError("phase is 'slow' or 'fast'")

    k = int(dfd_gap_limit_s * 1000 // interval_ms)
    if k < len(dfds) + 1:
        raise ValueError(
            f"{len(dfds)} DFDs cannot each repeat within {dfd_gap_limit_s:g} s at {interval_ms} ms "
            f"with room for values; use a shorter interval or fewer DFV indexes"
        )
    fitness = [d for d in dfvs if category_for_dfv(d) == "fitness"]
    specials = [DI_DPI] + [d for d in dfvs if category_for_dfv(d) != "fitness"]
    period = max(dpi_period_s, meta_period_s)
    blocks = max(1, round(period * 1000 / (k * interval_ms)))
    blocks = max(blocks, 1)
    slots_per_block = k - len(dfds)
    pattern: list[int] = []
    special_i = 0
    fitness_i = 0
    for _ in range(blocks):
        block: list[int | None] = [None] * k
        # DFDs at even positions so definitions and values alternate
        dfd_slots = list(range(0, k, 2))[: len(dfds)]
        if len(dfd_slots) < len(dfds):
            dfd_slots = list(range(len(dfds)))
        for pos, dfd in zip(dfd_slots, dfds, strict=True):
            block[pos] = dfd
        free = [i for i, v in enumerate(block) if v is None]
        for pos in free:
            if special_i < len(specials) and (
                blocks == 1 or pos == free[-1] and special_i < len(specials)
            ):
                # one special per block, in the last free slot; more if there are more
                # specials than blocks
                block[pos] = specials[special_i]
                special_i += 1
            elif fitness:
                block[pos] = fitness[fitness_i % len(fitness)]
                fitness_i += 1
            elif special_i < len(specials):
                block[pos] = specials[special_i]
                special_i += 1
            else:
                block[pos] = dfvs[0]
        pattern += [v for v in block if v is not None]
    # specials that did not get a slot (more specials than blocks): append extra blocks
    while special_i < len(specials):
        for dfd in dfds:
            pattern.append(dfd)
        pattern.append(specials[special_i])
        special_i += 1
        for _ in range(slots_per_block - 1):
            if fitness:
                pattern.append(fitness[fitness_i % len(fitness)])
                fitness_i += 1
    # start the cycle on the DPI
    if DI_DPI in pattern:
        i = pattern.index(DI_DPI)
        pattern = pattern[i:] + pattern[:i]
    return pattern


def describe(pattern: list[int]) -> list[str]:
    """Human labels: 'DPI', 'DFD 0', 'DFV 8'."""
    out = []
    for di in pattern:
        kind = spec.di_kind(di)
        if kind in ("DFD", "DFV"):
            out.append(f"{kind} {di & 0x3F}")
        else:
            out.append(kind)
    return out
