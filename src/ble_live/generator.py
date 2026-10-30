"""Render `ble_live.c` and `ble_live.h` from a LiveConfig and the base templates.

This is the original `live.py` restructured. Section markers in `ble_live_base.c/.h` look like
`//@!_<section>`; the first marker opens a section that this module fills in, the next marker
closes it. Everything outside markers is copied through.

For the repository's `live.xlsx` the output is identical to the original script's
(`tests/test_generator_regression.py`). Two behaviours of the original are kept on purpose
for now: the transmit pattern repeats each DFD once per cycle (the WYMU firmware uses its own
rotation; `rotation.plan` builds a compliant one).
"""

from __future__ import annotations

from pathlib import Path

from .config import LiveConfig, Struct, c_ident, c_object_name, enum_const

MARKER = "//@!"
BYTE_SIZES = {
    "uint8_t": 1,
    "int8_t": 1,
    "uint16_t": 2,
    "int16_t": 2,
    "uint32_t": 4,
    "int32_t": 4,
}
DFD_BIT = 0x40
DPI_MAX_DFV = 63  # the DPI's eight bytes cover DFV indexes 0..63


def byte_size(ctype: str) -> int:
    """Bytes a field of this C type occupies on the air. Enums are a byte."""
    return BYTE_SIZES.get(ctype.strip(), 1)


def _comment(description: str | None) -> str:
    """Trailing C comment for a field or enum member; nothing when there is no description."""
    return "" if description is None else f" // {description}"


class _Model:
    """Derived tables the renderers share: DFD/DFV names, enum contents."""

    def __init__(self, cfg: LiveConfig) -> None:
        problems = cfg.validate()
        if problems:
            raise ValueError("; ".join(problems))
        self.structs = cfg.structs
        self.enums: list[tuple[str, list[tuple[str, int, str | None]]]] = []
        self.dfds: dict[str, int] = {}
        self.dfvs: dict[str, int] = {}
        for obj in cfg.objects:
            cname = c_object_name(obj.name)
            if isinstance(obj, Struct):
                members = []
                for f in obj.included:
                    assert f.dfv is not None
                    self.dfvs.setdefault(f"{cname.upper()}_DFV_{f.dfv}", f.dfv)
                    self.dfds.setdefault(f"{cname.upper()}_DFD_{f.dfv}", f.dfv | DFD_BIT)
                    members.append((enum_const(obj.name, f.name), f.value, f.description))
                self.enums.append((cname, members))
            else:
                self.enums.append(
                    (
                        cname,
                        [
                            (enum_const(obj.name, m.name), m.value, m.description)
                            for m in obj.included
                        ],
                    )
                )

    def fields_for_dfv(self, dfv: int):
        for s in self.structs:
            for f in s.included:
                if f.dfv == dfv:
                    yield s, f

    def dfv_name(self, dfv: int) -> str:
        for name, value in self.dfvs.items():
            if value == dfv:
                return name
        raise KeyError(dfv)


# -- header sections ---------------------------------------------------------------------


def _dfd_dfv_defines(m: _Model) -> str:
    out = [f"#define {name} 0x{value:02x}\n" for name, value in m.dfds.items()]
    out += [f"#define {name} 0x{value:02x}\n" for name, value in m.dfvs.items()]
    out.append("\n")
    return "".join(out)


def _enum_struct_defs(m: _Model) -> str:
    out: list[str] = []
    for cname, members in m.enums:
        if not members:
            continue
        out.append("typedef enum {\n")
        for const, value, description in members:
            out.append(f"    {const} = {value},{_comment(description)}\n")
        out.append(f"}} {cname}_e;\n\n")
    for s in m.structs:
        if not s.included:
            continue
        out.append("typedef struct {\n")
        for f in s.included:
            name = c_ident(f.name)
            if f.repeat > 1:
                out.append("    struct\n    {\n")
                out.append(f"        {f.type} {name}[{f.repeat}];{_comment(f.description)}\n")
                out.append(f"        uint8_t {name}_count; // {name} array counter\n")
                out.append(f"    }} {name};\n")
            else:
                out.append(f"    {f.type} {name};{_comment(f.description)}\n")
        out.append(f"}} {c_object_name(s.name)}_t;\n\n")
    return "".join(out)


def _accessor_prototypes(m: _Model) -> str:
    out: list[str] = []
    for s in m.structs:
        for f in s.included:
            name = c_ident(f.name)
            if f.repeat == 1:
                out.append(f"void ble_live_set_{name}({f.type} _{name});\n")
                out.append(f"{f.type} ble_live_get_{name}(void);\n\n")
            else:
                out.append(f"void ble_live_add_{name}({f.type} _{name});\n")
                out.append(f"void ble_live_clear_{name}(void);\n\n")
    return "".join(out)


# -- source sections ---------------------------------------------------------------------


def _static_definitions(m: _Model) -> str:
    return "".join(f"{c_object_name(s.name)}_t m_{c_object_name(s.name)};\n" for s in m.structs)


def _tx_pattern(m: _Model) -> str:
    out = [
        "static uint8_t tx_pattern[] = { \n",
        "    BLE_LIVE_DATA_IDENTIFIER_PRESENCE_INDICATOR,\n",
    ]
    out += [f"    {name},\n" for name in m.dfds]
    for s in m.structs:
        for _ in range(s.repeat):
            seen: list[int] = []
            for f in s.included:
                if f.dfv in seen:
                    continue
                assert f.dfv is not None
                out.append(f"    {m.dfv_name(f.dfv)},\n")
                seen.append(f.dfv)
    out.append("};")
    return "".join(out)


def _encode_switch(m: _Model) -> str:
    out = ["    switch (data_identifier)\n    {\n"]

    # DPI: one bit per DFV index the device sends.
    out.append("        case BLE_LIVE_DATA_IDENTIFIER_PRESENCE_INDICATOR:\n        {\n")
    out.append("           memset(m_live_data, BLE_LIVE_DATA_FIELD_ID_UNUSED, 8);\n")
    dpi = [0] * 8
    for s in m.structs:
        for f in s.included:
            assert f.dfv is not None
            if 0 <= f.dfv <= DPI_MAX_DFV:
                dpi[f.dfv // 8] |= 1 << (f.dfv % 8)
    out += [f"            m_live_data[index++] = 0x{b:02x};\n" for b in dpi]
    out.append("            break;\n        }\n")

    # DFD: the field indexes, one byte per flag bit.
    for dfd_name, dfd_value in m.dfds.items():
        out.append(f"        case {dfd_name}:\n        {{\n")
        out.append("           memset(m_live_data, BLE_LIVE_DATA_FIELD_ID_UNUSED, 8);\n")
        for s, f in m.fields_for_dfv(dfd_value & (DFD_BIT - 1)):
            out.append(f"            m_live_data[index++] = {enum_const(s.name, f.name)};\n")
        out.append("            break;\n        }\n")

    # DFV: flag byte then the values, little-endian.
    for dfv_name, dfv_value in m.dfvs.items():
        out.append(f"        case {dfv_name}:\n        {{\n")
        out.append("            memset(m_live_data, BLE_LIVE_DATA_FIELD_ID_UNUSED, 8);\n")
        fields = list(m.fields_for_dfv(dfv_value))
        flag = (1 << len(fields)) - 1
        out.append(f"            m_live_data[index++] = 0x{flag:02x}; // Flag\n")
        for s, f in fields:
            var = f"m_{c_object_name(s.name)}.{c_ident(f.name)}"
            size = byte_size(f.type)
            if f.repeat > 1:
                name = c_ident(f.name)
                out.append(f"            int max_index = BLE_LIVE_MAX_BUFFER_SIZE - {size};\n")
                out.append("            int count = 0;\n")
                out.append(f"            while(count < {var}.{name}_count && index <= max_index)\n")
                out.append("            {\n")
                for b in range(size):
                    shift = ";\n" if b == 0 else f" >> {b * 8};\n"
                    out.append(f"                m_live_data[index++] = {var}.{name}[count]{shift}")
                out.append("                count++;\n")
                out.append("            }\n")
            else:
                for b in range(size):
                    shift = ";\n" if b == 0 else f" >> {b * 8};\n"
                    out.append(f"            m_live_data[index++] = {var}{shift}")
        out.append("            break;\n        }\n")
    out.append("    }\n")
    return "".join(out)


def _accessor_functions(m: _Model) -> str:
    out: list[str] = []
    for s in m.structs:
        for f in s.included:
            name = c_ident(f.name)
            var = f"m_{c_object_name(s.name)}.{name}"
            if f.repeat == 1:
                out.append(f"void ble_live_set_{name}({f.type} _{name})\n{{\n")
                out.append(f"    {var} = _{name};\n}}\n\n")
                out.append(f"{f.type} ble_live_get_{name}(void)\n{{\n")
                out.append(f"    return {var};\n}}\n\n")
            else:
                arr = f"{var}.{name}"
                out.append(f"void ble_live_add_{name}({f.type} _{name})\n{{\n")
                out.append(f"    const int max_count = sizeof({arr})/ sizeof({f.type});\n")
                out.append(f"    const int size_in_bytes = sizeof({arr});\n")
                out.append(f"    const int move_size = size_in_bytes - sizeof({f.type});\n")
                out.append(f"    memmove(&{arr}[1], &{arr}[0], move_size);\n")
                out.append(f"    {arr}[0] = _{name};\n")
                out.append(f"    if({arr}_count < max_count)\n    {{\n")
                out.append(f"        {arr}_count++;\n    }}\n}}\n\n")
                out.append(f"void ble_live_clear_{name}(void)\n{{\n")
                out.append(f"    const int size_in_bytes = sizeof({arr});\n")
                out.append(f"    memset(&{arr}[0], 0, size_in_bytes);\n")
                out.append(f"    {arr}_count = 0;\n}}\n\n")
    return "".join(out)


# -- template filling ----------------------------------------------------------------------


def fill_template(template: str, sections: dict[str, str]) -> str:
    """Copy `template` through, replacing each `//@!_<name>` ... `//@!...` block."""
    out: list[str] = []
    state: str | None = None
    for line in template.splitlines():
        tokens = line.split("_")
        if tokens[0] == MARKER:
            if state is None:
                state = tokens[1] if len(tokens) > 1 else ""
                out.append(sections.get(state, ""))
            else:
                state = None
        elif state is None:
            out.append(line + "\n")
    return "".join(out)


def generate(cfg: LiveConfig, template_dir: str | Path) -> dict[str, str]:
    """Return {'ble_live.h': text, 'ble_live.c': text} for the configuration."""
    template_dir = Path(template_dir)
    m = _Model(cfg)
    header = fill_template(
        (template_dir / "ble_live_base.h").read_text(encoding="utf-8"),
        {
            "dfd-dfv": _dfd_dfv_defines(m),
            "enums-structs": _enum_struct_defs(m),
            "accessors": _accessor_prototypes(m),
        },
    )
    source = fill_template(
        (template_dir / "ble_live_base.c").read_text(encoding="utf-8"),
        {
            "statics": _static_definitions(m),
            "txPattern": _tx_pattern(m),
            "decode": _encode_switch(m),
            "accessors": _accessor_functions(m),
        },
    )
    return {"ble_live.h": header, "ble_live.c": source}


def write_sources(sources: dict[str, str], out_dir: str | Path) -> list[Path]:
    """Write generated sources with the platform's line endings, as the original script did."""
    out_dir = Path(out_dir)
    written = []
    for name, text in sources.items():
        path = out_dir / name
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    return written
