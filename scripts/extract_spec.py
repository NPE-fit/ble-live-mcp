"""Extract the BLE Live protocol tables from the AsciiDoc spec into JSON.

Usage: python extract_spec.py <ble-live-protocol.adoc> <spec.json>

Reads the document attributes (revnumber, revdate) and the titled tables the library needs:
fitness field indexes, meta field indexes, equipment types, the Data Identifier range map and
the revision history. Table cells are parsed with the subset of AsciiDoc syntax the spec uses:
`|` separated cells, `a|` cells, `N+^|` column spans and ` +` line continuations.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TABLE_TITLES = {
    "Document Revisions": "revisions",
    "Fitness Data Index Definitions": "fitness_fields",
    "Meta Data Index Definitions": "meta_fields",
    "Equipment Type": "equipment_types",
    "Data Identifier Range Mapping": "di_ranges",
}

CELL_SPEC = re.compile(r"(?:^|\s)((?:\d+\+)?[\^<>]?a?)$")


def parse_attributes(text: str) -> dict[str, str]:
    attrs = {}
    for m in re.finditer(r"^:(\w+):\s*(.*)$", text, re.M):
        attrs[m.group(1)] = m.group(2).strip()
    return attrs


def find_tables(text: str) -> dict[str, tuple[int, list[str]]]:
    """Map table title -> (column count, body lines) for every titled table."""
    lines = text.splitlines()
    tables: dict[str, tuple[int, list[str]]] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(".") and not line.startswith(".."):
            title = re.sub(r"\[\[.*?\]\]", "", line[1:]).strip()
            j = i + 1
            cols = None
            while j < len(lines) and lines[j].startswith("["):
                m = re.search(r'cols="([^"]*)"', lines[j])
                if m:
                    cols = len(m.group(1).split(","))
                j += 1
            if j < len(lines) and lines[j].strip() == "|===":
                k = j + 1
                body = []
                while k < len(lines) and lines[k].strip() != "|===":
                    body.append(lines[k])
                    k += 1
                if cols is None:
                    cols = body[0].count("|") if body else 0
                tables[title] = (cols, body)
                i = k
        i += 1
    return tables


def split_cells(body: list[str], cols: int) -> list[list[str]]:
    """Turn a table body into rows of `cols` cleaned cell strings."""
    text = "\n".join(body)
    chunks = text.split("|")
    cells: list[str] = []
    span_next = 1
    # chunks[0] is whatever precedes the first '|' (normally empty)
    for chunk in chunks[1:]:
        spec = ""
        m = CELL_SPEC.search(chunk)
        if m and m.group(1):
            spec = m.group(1)
            chunk = chunk[: m.start(1)]
        content = clean(chunk)
        cells.append(content)
        cells.extend([""] * (span_next - 1))
        span_next = int(spec.split("+")[0]) if "+" in spec else 1
    rows = [cells[i : i + cols] for i in range(0, len(cells), cols)]
    return [r for r in rows if len(r) == cols]


def clean(cell: str) -> str:
    cell = cell.replace(" +\n", "\n")
    cell = re.sub(r"\n{2,}", "\n", cell)
    cell = cell.strip()
    cell = cell.replace("*", "")
    return cell


def norm_type(raw: str) -> dict:
    r = raw.strip().lower()
    m = re.match(r"^(u|s)?int(8|16|32)(.*)$", r)
    if m:
        signed = m.group(1) == "s" or m.group(1) is None
        bits = int(m.group(2))
        ctype = ("int" if signed else "uint") + str(bits)
        out = {"type": ctype, "size_bytes": bits // 8}
        if "per interval" in m.group(3):
            out["array"] = True
            out["size_bytes"] = None
            out["element_size_bytes"] = bits // 8
        return out
    if r.startswith("sizeof"):
        return {"type": None, "size_bytes": None, "variable": True}
    return {"type": None, "size_bytes": None}


def split_name_notes(cell: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in cell.split("\n") if p.strip()]
    if not parts:
        return "", None
    return parts[0], (" ".join(parts[1:]) or None)


def field_rows(rows: list[list[str]]) -> list[dict]:
    out = []
    for index, type_raw, size, units in rows[1:]:
        if not index.strip().isdigit():
            continue
        name, notes = split_name_notes(type_raw)
        entry = {"index": int(index), "name": name}
        entry.update(norm_type(size))
        entry["size_raw"] = size
        if units:
            entry["units"] = units.replace("\n", " ")
        if notes:
            entry["notes"] = notes
        out.append(entry)
    return out


def equipment_rows(rows: list[list[str]]) -> list[dict]:
    return [
        {"index": int(index), "name": name} for index, name in rows[1:] if index.strip().isdigit()
    ]


def di_range_rows(rows: list[list[str]]) -> list[dict]:
    out: list[dict] = []
    for rng, sub, meaning in rows[1:]:
        if rng and meaning:
            out.append({"range": rng, "meaning": meaning, "sub": []})
        elif sub and meaning and out:
            out[-1]["sub"].append({"range": sub, "meaning": meaning})
    return out


def revision_rows(rows: list[list[str]]) -> list[dict]:
    return [
        {"date": d, "version": v, "author": a, "description": desc.replace("\n", " ")}
        for d, v, a, desc in rows[1:]
        if d
    ]


def extract(adoc_path: Path) -> dict:
    text = adoc_path.read_text(encoding="utf-8")
    attrs = parse_attributes(text)
    tables = find_tables(text)
    missing = [t for t in TABLE_TITLES if t not in tables]
    if missing:
        raise SystemExit(f"tables not found in {adoc_path}: {missing}")
    parsed = {TABLE_TITLES[t]: split_cells(tables[t][1], tables[t][0]) for t in TABLE_TITLES}
    return {
        "spec_version": attrs.get("revnumber"),
        "revdate": attrs.get("revdate"),
        "source": adoc_path.name,
        "ad_types": {"plain": "0xFE", "encrypted": "0xFD"},
        "data_identifiers": {"dpi": "0xFF", "dfa": "0xFE", "dfd_bit": "0x40"},
        "fitness_fields": field_rows(parsed["fitness_fields"]),
        "meta_fields": field_rows(parsed["meta_fields"]),
        "equipment_types": equipment_rows(parsed["equipment_types"]),
        "di_ranges": di_range_rows(parsed["di_ranges"]),
        "revisions": revision_rows(parsed["revisions"]),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    data = extract(Path(argv[1]))
    Path(argv[2]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"spec {data['spec_version']}: {len(data['fitness_fields'])} fitness, "
        f"{len(data['meta_fields'])} meta, {len(data['equipment_types'])} equipment types, "
        f"{len(data['di_ranges'])} DI ranges, {len(data['revisions'])} revisions -> {argv[2]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
