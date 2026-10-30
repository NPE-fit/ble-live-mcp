"""BLE Live frame codec: advertising-structure parsing, stateful decoding, encoding, budget.

Terminology follows the spec. An *AD structure* is `length, type, data`. A BLE Live structure
has type 0xFE (plain) or 0xFD (encrypted) and its data is `device id (4, LE) [iv counter (4,
LE)] data identifier (1) payload`. The payload of a DFV starts with the flag byte; the payload
of a DFD is the list of field indexes; the payload of the DPI is the presence bitmap.

Decoding a DFV needs the DFD for the same index, so `Decoder` keeps per-device state across
frames.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from . import spec
from .crypto import DEFAULT_NONCE, KeyRing, crypt_payload

AD_TYPE_LIVE = spec.AD_TYPE_PLAIN
AD_TYPE_LIVE_ENCRYPTED = spec.AD_TYPE_ENCRYPTED
ADVERT_MAX_BYTES = 31
RR_INTERVALS_INDEX = 254
HEART_BEAT_COUNT_INDEX = 14
ZONE_THRESHOLDS_INDEX = 15
ZONE_THRESHOLD_SIZE_INDEX = 14

_HEX_JUNK = re.compile(r"(0x|[\s:,\-])", re.I)


def hex_to_bytes(text: str) -> bytes:
    """Accept '0A 0B', '0a:0b', '0x0A,0x0B' or '0A0B'."""
    cleaned = _HEX_JUNK.sub("", text)
    if len(cleaned) % 2:
        raise ValueError(f"odd number of hex digits in {text!r}")
    return bytes.fromhex(cleaned)


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def field_key(name: str) -> str:
    """Spec field name -> snake_case key: 'Model #' -> 'model_number', 'ANT+ Manufacturer ID' ->
    'ant_manufacturer_id'."""
    s = name.lower().replace("#", " number ").replace("+", "")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def category_for_dfv(dfv_index: int) -> str:
    if 0 <= dfv_index <= 7:
        return "fitness"
    if 8 <= dfv_index <= 15:
        return "meta"
    if 60 <= dfv_index <= 63:
        return "manufacturer"
    return "reserved"


# -- advertising structures ----------------------------------------------------------------


@dataclass(frozen=True)
class AdStructure:
    ad_type: int
    data: bytes

    @property
    def size(self) -> int:
        return 2 + len(self.data)

    def to_bytes(self) -> bytes:
        return bytes([len(self.data) + 1, self.ad_type]) + self.data


def parse_ad_structures(payload: bytes) -> list[AdStructure]:
    """Split an advertising (or scan response) payload into AD structures."""
    out: list[AdStructure] = []
    i = 0
    while i < len(payload):
        length = payload[i]
        if length == 0:  # padding
            i += 1
            continue
        if i + 1 + length > len(payload):
            raise ValueError(f"AD structure at offset {i} overruns the payload")
        out.append(AdStructure(payload[i + 1], bytes(payload[i + 2 : i + 1 + length])))
        i += 1 + length
    return out


def find_live_structures(payload: bytes) -> list[AdStructure]:
    return [
        s
        for s in parse_ad_structures(payload)
        if s.ad_type in (AD_TYPE_LIVE, AD_TYPE_LIVE_ENCRYPTED)
    ]


# -- frames -----------------------------------------------------------------------------------


@dataclass
class LiveFrame:
    device_id: int
    data_identifier: int
    payload: bytes
    encrypted: bool = False
    iv_counter: int | None = None

    @property
    def kind(self) -> str:
        return spec.di_kind(self.data_identifier)

    @property
    def dfv_index(self) -> int | None:
        return self.data_identifier & 0x3F if self.kind in ("DFD", "DFV") else None

    @property
    def ad_type(self) -> int:
        return AD_TYPE_LIVE_ENCRYPTED if self.encrypted else AD_TYPE_LIVE

    def to_data(self) -> bytes:
        """Structure data: device id, optional counter, data identifier, payload."""
        body = self.device_id.to_bytes(4, "little")
        if self.encrypted:
            body += (self.iv_counter or 0).to_bytes(4, "little")
        return body + bytes([self.data_identifier]) + self.payload

    def to_ad_structure(self) -> AdStructure:
        return AdStructure(self.ad_type, self.to_data())

    def to_bytes(self) -> bytes:
        return self.to_ad_structure().to_bytes()

    @property
    def size(self) -> int:
        """Bytes this structure takes in the 31-byte advertising packet."""
        return self.to_ad_structure().size


def parse_live_data(data: bytes, encrypted: bool = False) -> LiveFrame:
    """Parse structure data (device id first), as nRF Connect shows it after
    'Unknown EIR (0xFE):'."""
    header = 9 if encrypted else 5
    if len(data) < header:
        raise ValueError(f"BLE Live data needs at least {header} bytes, got {len(data)}")
    device_id = int.from_bytes(data[0:4], "little")
    if encrypted:
        counter = int.from_bytes(data[4:8], "little")
        return LiveFrame(device_id, data[8], bytes(data[9:]), True, counter)
    return LiveFrame(device_id, data[4], bytes(data[5:]))


def parse_live_structure(ad: AdStructure) -> LiveFrame:
    if ad.ad_type not in (AD_TYPE_LIVE, AD_TYPE_LIVE_ENCRYPTED):
        raise ValueError(f"AD type 0x{ad.ad_type:02X} is not BLE Live (0xFE/0xFD)")
    return parse_live_data(ad.data, encrypted=ad.ad_type == AD_TYPE_LIVE_ENCRYPTED)


def parse_frame(data: bytes, form: str = "auto") -> LiveFrame:
    """Parse one frame from bytes.

    form: 'ad' (length, type, data), 'advert' (whole advertising payload holding a live
    structure), 'data' (device id first, plain), 'data-encrypted', or 'auto'.
    """
    if form == "data":
        return parse_live_data(data)
    if form == "data-encrypted":
        return parse_live_data(data, encrypted=True)
    if form == "ad":
        structures = parse_ad_structures(data)
        if len(structures) != 1:
            raise ValueError("expected exactly one AD structure")
        return parse_live_structure(structures[0])
    if form == "advert":
        live = find_live_structures(data)
        if not live:
            raise ValueError("no BLE Live structure (0xFE/0xFD) in the advertising payload")
        return parse_live_structure(live[0])
    if form != "auto":
        raise ValueError(f"unknown form {form!r}")
    # auto: a single structure or a whole payload, else device-id-first data
    try:
        live = find_live_structures(data)
    except ValueError:
        live = []
    if live:
        return parse_live_structure(live[0])
    return parse_live_data(data)


# -- decoding ---------------------------------------------------------------------------------


@dataclass
class DecodedField:
    index: int
    name: str
    key: str
    value: int | list[int] | None
    units: str | None
    raw: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "key": self.key,
            "value": self.value,
            "units": self.units,
            "raw_hex": bytes_to_hex(self.raw),
        }


@dataclass
class DecodedFrame:
    frame: LiveFrame
    kind: str
    category: str | None = None
    flag: int | None = None
    fields: list[DecodedField] = field(default_factory=list)
    dfv_indexes_present: list[int] | None = None
    field_indexes: list[int] | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    decrypted: bool = False

    def values(self) -> dict[str, int | list[int] | None]:
        """Field values by key; a key that repeats in the frame becomes a list."""
        out: dict[str, Any] = {}
        for f in self.fields:
            if f.key in out:
                prev = out[f.key]
                out[f.key] = (prev if isinstance(prev, list) else [prev]) + [f.value]
            else:
                out[f.key] = f.value
        return out

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "device_id": self.frame.device_id,
            "device_id_hex": f"0x{self.frame.device_id:08X}",
            "kind": self.kind,
            "data_identifier": self.frame.data_identifier,
            "dfv_index": self.frame.dfv_index,
            "category": self.category,
            "encrypted": self.frame.encrypted,
            "payload_hex": bytes_to_hex(self.frame.payload),
            "structure_size": self.frame.size,
        }
        if self.frame.encrypted:
            d["iv_counter"] = self.frame.iv_counter
            d["decrypted"] = self.decrypted
        if self.kind == "DPI":
            d["dfv_indexes_present"] = self.dfv_indexes_present
        if self.kind == "DFD":
            d["field_indexes"] = self.field_indexes
            d["field_names"] = [f.name for f in self.fields]
        if self.kind == "DFV":
            d["flag"] = self.flag
            d["fields"] = [f.to_dict() for f in self.fields]
            d["values"] = self.values()
        if self.warnings:
            d["warnings"] = self.warnings
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class DeviceState:
    dfds: dict[int, list[int]] = field(default_factory=dict)
    dpi: list[int] | None = None
    frames: int = 0
    kinds: Counter = field(default_factory=Counter)
    last_values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": self.frames,
            "kinds": dict(self.kinds),
            "dpi": self.dpi,
            "dfds": {str(k): v for k, v in self.dfds.items()},
            "last_values": self.last_values,
        }


def _field_name(category: str, index: int) -> tuple[str, str | None, dict | None]:
    if category in ("fitness", "meta"):
        try:
            row = spec.field_by_index(category, index)
            return row["name"], row.get("units"), row
        except KeyError:
            return f"undefined {category} index {index}", None, None
    return f"{category} field {index}", None, None


def decode_dfv_payload(
    category: str, dfd_fields: list[int], payload: bytes
) -> tuple[int, list[DecodedField], list[str]]:
    """Decode a DFV payload (flag byte then values) against its DFD field list."""
    warnings: list[str] = []
    if not payload:
        raise ValueError("DFV payload is empty (no flag byte)")
    flag = payload[0]
    data = payload[1:]
    fields: list[DecodedField] = []
    pos = 0
    sizes_this_frame: dict[int, int] = {}
    for bit in range(8):
        if not flag >> bit & 1:
            continue
        if bit >= len(dfd_fields):
            warnings.append(
                f"flag bit {bit} is set but the DFD defines only {len(dfd_fields)} fields"
            )
            continue
        index = dfd_fields[bit]
        name, units, row = _field_name(category, index)
        key = field_key(name)
        if row is None:
            fields.append(DecodedField(index, name, key, None, None, data[pos:]))
            warnings.append(f"{name}: size unknown, remaining {len(data) - pos} byte(s) kept raw")
            pos = len(data)
            break
        if category == "fitness" and index == RR_INTERVALS_INDEX:
            rest = data[pos:]
            if len(rest) % 2:
                warnings.append("R-R interval bytes are not a multiple of 2")
            values = [int.from_bytes(rest[i : i + 2], "little") for i in range(0, len(rest) - 1, 2)]
            fields.append(DecodedField(index, name, key, values, units, rest))
            pos = len(data)
            break
        size = row.get("size_bytes")
        if category == "meta" and index == ZONE_THRESHOLDS_INDEX:
            bits = sizes_this_frame.get(ZONE_THRESHOLD_SIZE_INDEX)
            rest = data[pos:]
            if bits and bits % 8 == 0 and len(rest) % (bits // 8) == 0:
                step = bits // 8
                values = [
                    int.from_bytes(rest[i : i + step], "little") for i in range(0, len(rest), step)
                ]
                fields.append(DecodedField(index, name, key, values, units, rest))
            else:
                fields.append(DecodedField(index, name, key, None, units, rest))
                warnings.append("Zone Thresholds kept raw: no Zone Threshold Size in this frame")
            pos = len(data)
            break
        if size is None:
            fields.append(DecodedField(index, name, key, None, units, data[pos:]))
            warnings.append(f"{name}: variable size, remaining bytes kept raw")
            pos = len(data)
            break
        if pos + size > len(data):
            warnings.append(f"{name}: needs {size} byte(s), only {len(data) - pos} left")
            fields.append(DecodedField(index, name, key, None, units, data[pos:]))
            pos = len(data)
            break
        raw = data[pos : pos + size]
        signed = (row.get("type") or "").startswith("int")
        value = int.from_bytes(raw, "little", signed=signed)
        sizes_this_frame[index] = value
        fields.append(DecodedField(index, name, key, value, units, raw))
        pos += size
    if pos < len(data):
        warnings.append(f"{len(data) - pos} trailing byte(s) not covered by the flag")
    return flag, fields, warnings


class Decoder:
    """Stateful decoder: remembers each device's DPI and DFDs so DFVs can be interpreted."""

    def __init__(self, keys: KeyRing | None = None) -> None:
        self.devices: dict[int, DeviceState] = {}
        self.keys = keys or KeyRing()

    def state(self, device_id: int) -> DeviceState:
        return self.devices.setdefault(device_id, DeviceState())

    def reset(self) -> None:
        self.devices.clear()

    def learn_dfd(self, device_id: int, dfv_index: int, field_indexes: list[int]) -> None:
        self.state(device_id).dfds[dfv_index] = list(field_indexes)

    def decode_bytes(self, data: bytes, form: str = "auto") -> DecodedFrame:
        return self.decode(parse_frame(data, form))

    def decode(self, frame: LiveFrame) -> DecodedFrame:
        st = self.state(frame.device_id)
        st.frames += 1
        kind = frame.kind
        st.kinds[kind] += 1
        out = DecodedFrame(frame=frame, kind=kind)
        if frame.encrypted:
            plain = self.keys.decrypt(frame.device_id, frame.iv_counter or 0, frame.payload)
            if plain is None:
                out.error = "encrypted payload (0xFD); no key for this device (set_session_key)"
                return out
            frame = LiveFrame(frame.device_id, frame.data_identifier, plain, True, frame.iv_counter)
            out.frame = frame
            out.decrypted = True
        if kind == "DPI":
            bits = [
                i * 8 + b for i, byte in enumerate(frame.payload) for b in range(8) if byte >> b & 1
            ]
            out.dfv_indexes_present = bits
            st.dpi = bits
            if len(frame.payload) > 8:
                out.warnings.append("DPI payload longer than 8 bytes")
            return out
        if kind == "DFD":
            dfv = frame.dfv_index
            assert dfv is not None
            fields = list(frame.payload)
            out.category = category_for_dfv(dfv)
            out.field_indexes = fields
            if len(fields) > 8:
                out.warnings.append("DFD lists more than 8 fields; the flag byte covers 8")
            if not fields:
                out.warnings.append("DFD has no fields")
            for index in fields:
                name, units, _ = _field_name(out.category, index)
                out.fields.append(
                    DecodedField(index, name, field_key(name), None, units, bytes([index]))
                )
            st.dfds[dfv] = fields
            return out
        if kind == "DFV":
            dfv = frame.data_identifier
            out.category = category_for_dfv(dfv)
            dfd_fields = st.dfds.get(dfv)
            if dfd_fields is None:
                out.error = f"DFD 0x{dfv | spec.DI_DFD_BIT:02X} for DFV {dfv} not seen yet"
                return out
            try:
                out.flag, out.fields, out.warnings = decode_dfv_payload(
                    out.category, dfd_fields, frame.payload
                )
            except ValueError as exc:
                out.error = str(exc)
                return out
            st.last_values.update({k: v for k, v in out.values().items() if v is not None})
            return out
        if kind == "DFA":
            out.error = (
                "DFA packets are defined by the spec but not implemented (placeholder tables)"
            )
        else:
            out.error = f"data identifier 0x{frame.data_identifier:02X} is reserved"
        return out


# -- encoding ---------------------------------------------------------------------------------


def encode_dpi(dfv_indexes: list[int], truncate: bool = True) -> bytes:
    bitmap = bytearray(8)
    for dfv in dfv_indexes:
        if not 0 <= dfv <= 63:
            raise ValueError(f"DFV index {dfv} does not fit the DPI (0-63)")
        bitmap[dfv // 8] |= 1 << (dfv % 8)
    if truncate:
        while len(bitmap) > 1 and bitmap[-1] == 0:
            bitmap.pop()
    return bytes(bitmap)


def encode_dfd(field_indexes: list[int]) -> bytes:
    if not 1 <= len(field_indexes) <= 8:
        raise ValueError("a DFD defines 1 to 8 fields")
    return bytes(field_indexes)


def encode_dfv(
    category: str, dfd_fields: list[int], values: Mapping[int, int | list[int]]
) -> bytes:
    """Build a DFV payload (flag + values) for the given fields.

    `values` maps field index -> value; a field index that appears more than once in the DFD
    (equipment type twice) takes a list, consumed in DFD order. R-R intervals (254) take the
    whole list.
    """
    pending: dict[int, list[int]] = {}
    for index, v in values.items():
        pending[int(index)] = list(v) if isinstance(v, list) else [v]
    flag = 0
    out = bytearray()
    for bit, index in enumerate(dfd_fields):
        if bit >= 8:
            raise ValueError("a DFD defines at most 8 fields")
        queue = pending.get(index)
        if not queue:
            continue
        flag |= 1 << bit
        if category == "fitness" and index == RR_INTERVALS_INDEX:
            for rr in queue:
                out += int(rr).to_bytes(2, "little")
            pending[index] = []
            continue
        value = queue.pop(0)
        row = spec.field_by_index(category, index) if category in ("fitness", "meta") else None
        size = row.get("size_bytes") if row else None
        if size is None:
            raise ValueError(f"{category} field {index} has no fixed size; pass raw bytes instead")
        signed = (row.get("type") or "").startswith("int")
        out += int(value).to_bytes(size, "little", signed=signed)
    leftover = {k: v for k, v in pending.items() if v}
    if leftover:
        raise ValueError(f"values for fields not in the DFD or given too often: {sorted(leftover)}")
    return bytes([flag]) + bytes(out)


def encrypt_frame(
    frame: LiveFrame, key: bytes, iv_counter: int, nonce: bytes = DEFAULT_NONCE
) -> LiveFrame:
    """The 0xFD form of a plain frame: payload XOR AES-128(key, nonce || counter)."""
    sealed = crypt_payload(key, iv_counter, frame.payload, nonce)
    return LiveFrame(frame.device_id, frame.data_identifier, sealed, True, iv_counter)


# -- advertising budget -----------------------------------------------------------------------


def live_structure_size(payload_bytes: int, encrypted: bool = False) -> int:
    return 2 + 4 + (4 if encrypted else 0) + 1 + payload_bytes


@dataclass
class AdvertLayout:
    """What else shares the 31-byte advertising packet with the BLE Live structure."""

    flags: bool = True
    manufacturer_data_bytes: int | None = None
    """Manufacturer-specific data after the 2-byte company id; None when absent."""
    name_bytes: int = 0
    uuids16: int = 0
    uuids128: int = 0
    appearance: bool = False
    tx_power: bool = False
    live_payload_bytes: int | None = None
    encrypted: bool = False


def advert_budget(layout: AdvertLayout) -> dict[str, Any]:
    items: list[tuple[str, int]] = []
    if layout.flags:
        items.append(("flags", 3))
    if layout.manufacturer_data_bytes is not None:
        items.append(("manufacturer specific data", 2 + 2 + layout.manufacturer_data_bytes))
    if layout.name_bytes:
        items.append(("name", 2 + layout.name_bytes))
    if layout.uuids16:
        items.append(("16-bit service UUIDs", 2 + 2 * layout.uuids16))
    if layout.uuids128:
        items.append(("128-bit service UUIDs", 2 + 16 * layout.uuids128))
    if layout.appearance:
        items.append(("appearance", 4))
    if layout.tx_power:
        items.append(("tx power", 3))
    if layout.live_payload_bytes is not None:
        items.append(
            (
                "BLE Live (0xFD)" if layout.encrypted else "BLE Live (0xFE)",
                live_structure_size(layout.live_payload_bytes, layout.encrypted),
            )
        )
    total = sum(size for _, size in items)
    return {
        "items": [{"structure": n, "bytes": s} for n, s in items],
        "total_bytes": total,
        "limit_bytes": ADVERT_MAX_BYTES,
        "remaining_bytes": ADVERT_MAX_BYTES - total,
        "fits": total <= ADVERT_MAX_BYTES,
    }
