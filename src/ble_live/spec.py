"""Access to the BLE Live protocol tables, extracted from the spec into `data/spec.json`."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

CATEGORIES = ("fitness", "meta", "equipment")

AD_TYPE_PLAIN = 0xFE
AD_TYPE_ENCRYPTED = 0xFD
DI_DPI = 0xFF
DI_DFA = 0xFE
DI_DFD_BIT = 0x40


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    text = resources.files("ble_live.data").joinpath("spec.json").read_text(encoding="utf-8")
    return json.loads(text)


def version() -> str:
    return load_spec()["spec_version"]


def fields(category: str) -> list[dict[str, Any]]:
    """Table rows for 'fitness', 'meta' or 'equipment'."""
    if category not in CATEGORIES:
        raise KeyError(f"unknown category {category!r}; one of {CATEGORIES}")
    key = {"fitness": "fitness_fields", "meta": "meta_fields", "equipment": "equipment_types"}
    return load_spec()[key[category]]


def field_by_index(category: str, index: int) -> dict[str, Any]:
    for row in fields(category):
        if row["index"] == index:
            return row
    raise KeyError(f"{category} index {index} is not defined")


def size_bytes(category: str, index: int) -> int | None:
    """On-air size of a field, or None when it is variable (R-R intervals, zone thresholds)."""
    return field_by_index(category, index).get("size_bytes")


def di_kind(data_identifier: int) -> str:
    """Classify a Data Identifier byte: 'DPI', 'DFA', 'DFD', 'DFV' or 'reserved'."""
    if data_identifier == DI_DPI:
        return "DPI"
    if data_identifier == DI_DFA:
        return "DFA"
    if data_identifier < 0x40:
        return "DFV"
    if data_identifier < 0x80:
        return "DFD"
    return "reserved"
