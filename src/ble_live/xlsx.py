"""Import and export the `live.xlsx` workbook format.

Sheet layout (first sheet, header row first):

| Name | Type | Value | Description | DFV_Min | DFV_Max | Include | DFV | Repeat | Notes |

- A row whose Type is `struct` or `enum` opens an object. For a struct, `DFV` holds the number
  of times its DFV packets repeat in the transmit pattern and DFV_Min/Max its index range.
- Following rows are the object's fields or members until the next object row.
- `Value` is the spec index. Left blank it continues from the previous row, starting at 0 after
  the object row (the original live.py rule).
- `Include` is `Yes` or `No`. `DFV` on a field row is the DFV packet the field travels in.
- `Repeat` (optional column) is an array length; blank means 1.
- Free text in any column without a known header is kept as the object's `notes`.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from .config import Enum, Field, LiveConfig, Member, Struct

KNOWN_HEADERS = (
    "name",
    "type",
    "value",
    "description",
    "dfv_min",
    "dfv_max",
    "include",
    "dfv",
    "repeat",
    "notes",
)


def _int(value) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _str(value) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s.strip() else None


def import_xlsx(path: str | Path) -> LiveConfig:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    col: dict[str, int] = {}
    unnamed: list[int] = []
    for i, h in enumerate(header):
        key = str(h).strip().lower() if h is not None else ""
        if key in KNOWN_HEADERS:
            col[key] = i
        else:
            unnamed.append(i)

    def cell(row, key):
        i = col.get(key)
        return row[i] if i is not None and i < len(row) else None

    def notes(row):
        candidates = [
            _str(row[i]) for i in (col.get("notes"), *unnamed) if i is not None and i < len(row)
        ]
        candidates = [c for c in candidates if c]
        return candidates[-1] if candidates else None

    cfg = LiveConfig()
    current: Struct | Enum | None = None
    counter = 0
    for row in rows:
        name = _str(cell(row, "name"))
        rtype = _str(cell(row, "type"))
        if name is None:
            continue
        if rtype in ("struct", "enum"):
            counter = 0
            if rtype == "struct":
                current = Struct(
                    name=name,
                    repeat=_int(cell(row, "dfv")) or 1,
                    dfv_min=_int(cell(row, "dfv_min")) or 0,
                    dfv_max=_int(cell(row, "dfv_max")) or 0,
                    notes=notes(row),
                )
            else:
                current = Enum(name=name, notes=notes(row))
            cfg.objects.append(current)
            continue
        if current is None:
            raise ValueError(f"field row {name!r} before any struct/enum row")

        explicit = _int(cell(row, "value"))
        if explicit is not None:
            counter = explicit
        value = counter
        counter += 1
        include = str(cell(row, "include") or "").strip().lower() == "yes"
        description = _str(cell(row, "description"))

        if isinstance(current, Struct):
            current.fields.append(
                Field(
                    name=name,
                    type=rtype or "uint8_t",
                    value=value,
                    description=description,
                    include=include,
                    dfv=_int(cell(row, "dfv")),
                    repeat=_int(cell(row, "repeat")) or 1,
                )
            )
        else:
            current.members.append(
                Member(name=name, value=value, description=description, include=include)
            )
    wb.close()
    return cfg


def export_xlsx(cfg: LiveConfig, path: str | Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "objects"
    ws.append(
        [
            "Name",
            "Type",
            "Value",
            "Description",
            "DFV_Min",
            "DFV_Max",
            "Include",
            "DFV",
            "Repeat",
            "Notes",
        ]
    )
    for obj in cfg.objects:
        if isinstance(obj, Struct):
            ws.append(
                [
                    obj.name,
                    "struct",
                    None,
                    None,
                    obj.dfv_min,
                    obj.dfv_max,
                    None,
                    obj.repeat,
                    None,
                    obj.notes,
                ]
            )
            for f in obj.fields:
                ws.append(
                    [
                        f.name,
                        f.type,
                        f.value,
                        f.description,
                        None,
                        None,
                        "Yes" if f.include else "No",
                        f.dfv,
                        f.repeat if f.repeat > 1 else None,
                        None,
                    ]
                )
        else:
            ws.append([obj.name, "enum", None, None, None, None, None, None, None, obj.notes])
            for m in obj.members:
                ws.append(
                    [m.name, None, m.value, m.description, None, None, "Yes" if m.include else "No"]
                )
    ws.freeze_panes = "A2"
    wb.save(path)
