"""Access to the BLE Live protocol tables and text, extracted from the spec into `data/`."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

CATEGORIES = ("fitness", "meta", "equipment")

AD_TYPE_PLAIN = 0xFE
AD_TYPE_ENCRYPTED = 0xFD
DI_DPI = 0xFF
DI_DFA = 0xFE
DI_DFD_BIT = 0x40


def _load(name: str) -> Any:
    text = resources.files("ble_live.data").joinpath(name).read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    return _load("spec.json")


@lru_cache(maxsize=1)
def load_sections() -> dict[str, Any]:
    return _load("spec_sections.json")


@lru_cache(maxsize=1)
def load_quirks() -> dict[str, Any]:
    return _load("quirks.json")


@lru_cache(maxsize=1)
def load_vectors() -> dict[str, Any]:
    return _load("vectors.json")


def load_example(name: str) -> dict[str, Any]:
    """A packaged example configuration (data/examples/<name>.json)."""
    return _load(f"examples/{name}.json")


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


# -- text -------------------------------------------------------------------------------------


def sections() -> list[dict[str, Any]]:
    return load_sections()["sections"]


def toc() -> list[dict[str, Any]]:
    return [{"id": s["id"], "level": s["level"], "title": s["title"]} for s in sections()]


def section(section_id: str) -> dict[str, Any]:
    for s in sections():
        if s["id"] == section_id:
            return s
    raise KeyError(f"no section {section_id!r}; see the table of contents")


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Rank sections by query terms: title hits count triple, body hits cap at 3 per term so
    long sections do not win on repetition, and a section holding every term gets a bonus."""
    terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 1]
    if not terms:
        return []
    scored = []
    for s in sections():
        title = s["title"].lower()
        body = s["body"].lower()
        score = 0
        present = 0
        for t in terms:
            title_hits = title.count(t)
            body_hits = body.count(t)
            if title_hits or body_hits:
                present += 1
            score += 3 * title_hits + min(body_hits, 3)
        if present == len(terms):
            score += 2 * len(terms)
        if score:
            first = min((body.find(t) for t in terms if t in body), default=0)
            start = max(0, first - 120)
            excerpt = s["body"][start : start + 360].strip()
            scored.append({"id": s["id"], "title": s["title"], "score": score, "excerpt": excerpt})
    scored.sort(key=lambda r: -r["score"])
    return scored[:limit]
