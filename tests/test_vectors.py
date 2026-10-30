"""Structural checks on the golden vectors: DI kinds and DFV lengths against the spec tables."""

import json
from importlib import resources

import pytest

from ble_live import spec


def load_vectors():
    text = resources.files("ble_live.data").joinpath("vectors.json").read_text(encoding="utf-8")
    return json.loads(text)


VECTORS = load_vectors()
FRAMES = {f["id"]: f for f in VECTORS["frames"]}


def payload(frame):
    return bytes.fromhex(frame["payload_hex"].replace(" ", ""))


@pytest.mark.parametrize("frame", VECTORS["frames"], ids=lambda f: f["id"])
def test_kind_matches_data_identifier(frame):
    assert spec.di_kind(payload(frame)[0]) == frame["kind"]


@pytest.mark.parametrize(
    "frame", [f for f in VECTORS["frames"] if f["kind"] == "DFD"], ids=lambda f: f["id"]
)
def test_dfd_lists_defined_fields(frame):
    data = payload(frame)
    assert data[0] & 0x40 and (data[0] & 0x3F) == frame["expect"]["dfv"]
    assert list(data[1:]) == frame["expect"]["field_indexes"]
    for index in data[1:]:
        spec.field_by_index(frame["expect"]["category"], index)


@pytest.mark.parametrize(
    "frame", [f for f in VECTORS["frames"] if f["kind"] == "DFV"], ids=lambda f: f["id"]
)
def test_dfv_length_follows_dfd_and_flag(frame):
    dfd = FRAMES[frame["dfd"]]
    category = dfd["expect"]["category"]
    fields = list(payload(dfd)[1:])
    data = payload(frame)
    assert data[0] == dfd["expect"]["dfv"]
    flag = data[1]
    selected = [fields[i] for i in range(len(fields)) if flag >> i & 1]
    expected = sum(spec.size_bytes(category, i) for i in selected)
    actual = len(data) - 2
    if "known_inconsistency" in frame:
        assert actual != expected, "inconsistency no longer reproduces; drop the flag"
    else:
        assert actual == expected


@pytest.mark.parametrize(
    "frame", [f for f in VECTORS["frames"] if f["kind"] == "DPI"], ids=lambda f: f["id"]
)
def test_dpi_bits(frame):
    data = payload(frame)[1:]
    bits = [i * 8 + b for i, byte in enumerate(data) for b in range(8) if byte >> b & 1]
    assert bits == frame["expect"]["dfv_indexes"]


def test_rotations_reference_known_frames():
    for rotation in VECTORS["rotations"]:
        for fid in rotation["frames"]:
            assert fid in FRAMES, f"{rotation['id']} references unknown frame {fid}"
