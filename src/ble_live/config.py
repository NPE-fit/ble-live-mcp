"""Configuration model for a BLE Live sensor: which fields go in which DFV packets.

This is the data `live.xlsx` has always carried, as plain dataclasses with a JSON form.
A configuration is an ordered list of objects. A *struct* groups fields that share a DFV
index range (fitness 0-7, meta 8-15, manufacturer 60-63); each included field names the DFV
it travels in. An *enum* defines a value set used by a field (e.g. equipment type).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def c_ident(name: str) -> str:
    """'Heart Rate' -> 'heart_rate'."""
    return name.lower().replace(" ", "_")


def c_object_name(name: str) -> str:
    """'Fitness Data' -> 'ble_live_fitness_data'."""
    return "ble_live_" + c_ident(name)


def enum_const(object_name: str, member_name: str) -> str:
    """('Fitness Data', 'Power') -> 'BLE_LIVE_FITNESS_DATA_POWER'."""
    return c_object_name(object_name).upper() + "_" + c_ident(member_name).upper()


@dataclass
class Field:
    name: str
    type: str
    value: int
    description: str | None = None
    include: bool = False
    dfv: int | None = None
    repeat: int = 1
    """Array length. Above 1 the field becomes a stack with add/clear accessors (R-R intervals)."""


@dataclass
class Member:
    name: str
    value: int
    description: str | None = None
    include: bool = True


@dataclass
class Struct:
    name: str
    repeat: int
    dfv_min: int
    dfv_max: int
    fields: list[Field] = field(default_factory=list)
    notes: str | None = None

    @property
    def included(self) -> list[Field]:
        return [f for f in self.fields if f.include]


@dataclass
class Enum:
    name: str
    members: list[Member] = field(default_factory=list)
    notes: str | None = None

    @property
    def included(self) -> list[Member]:
        return [m for m in self.members if m.include]


@dataclass
class LiveConfig:
    objects: list[Struct | Enum] = field(default_factory=list)
    spec_version: str | None = None

    @property
    def structs(self) -> list[Struct]:
        return [o for o in self.objects if isinstance(o, Struct)]

    @property
    def enums(self) -> list[Enum]:
        return [o for o in self.objects if isinstance(o, Enum)]

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the configuration can be generated."""
        problems: list[str] = []
        for s in self.structs:
            for f in s.included:
                if f.dfv is None:
                    problems.append(f"{s.name}/{f.name}: included field has no DFV")
                elif not s.dfv_min <= f.dfv <= s.dfv_max:
                    problems.append(
                        f"{s.name}/{f.name}: DFV {f.dfv} outside {s.dfv_min}-{s.dfv_max}"
                    )
            for dfv in {f.dfv for f in s.included if f.dfv is not None}:
                n = sum(1 for f in s.included if f.dfv == dfv)
                if n > 8:
                    problems.append(
                        f"{s.name}: DFV {dfv} carries {n} fields, the flag byte allows 8"
                    )
        return problems

    # -- JSON -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        objects = []
        for o in self.objects:
            d = asdict(o)
            d["kind"] = "struct" if isinstance(o, Struct) else "enum"
            objects.append(d)
        return {"spec_version": self.spec_version, "objects": objects}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveConfig:
        objects: list[Struct | Enum] = []
        for d in data.get("objects", []):
            d = dict(d)
            kind = d.pop("kind")
            if kind == "struct":
                d["fields"] = [Field(**f) for f in d.get("fields", [])]
                objects.append(Struct(**d))
            elif kind == "enum":
                d["members"] = [Member(**m) for m in d.get("members", [])]
                objects.append(Enum(**d))
            else:
                raise ValueError(f"unknown object kind {kind!r}")
        return cls(objects=objects, spec_version=data.get("spec_version"))

    def save_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> LiveConfig:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
