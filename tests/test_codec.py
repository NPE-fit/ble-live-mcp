import pytest

from ble_live import spec
from ble_live.codec import (
    AdvertLayout,
    Decoder,
    LiveFrame,
    advert_budget,
    category_for_dfv,
    encode_dfd,
    encode_dfv,
    encode_dpi,
    field_key,
    find_live_structures,
    hex_to_bytes,
    parse_ad_structures,
    parse_frame,
)

VECTORS = spec.load_vectors()
FRAMES = {f["id"]: f for f in VECTORS["frames"]}


def payload(frame_id: str) -> bytes:
    return hex_to_bytes(FRAMES[frame_id]["payload_hex"])


def test_hex_and_keys():
    assert hex_to_bytes("0x0A, 0x0b 0C:0d-0E") == bytes([10, 11, 12, 13, 14])
    assert field_key("Model #") == "model_number"
    assert field_key("ANT+ Manufacturer ID") == "ant_manufacturer_id"
    assert field_key("RSSI Measured at 1m") == "rssi_measured_at_1m"
    assert [category_for_dfv(d) for d in (0, 7, 8, 15, 16, 60, 63, 64)] == [
        "fitness",
        "fitness",
        "meta",
        "meta",
        "reserved",
        "manufacturer",
        "manufacturer",
        "reserved",
    ]


def test_parse_advert_and_structure_forms():
    device = 0xEBC4B6B1
    live = LiveFrame(device, 0x40, bytes([8, 16, 17]))
    assert live.to_bytes() == hex_to_bytes("09 FE B1 B6 C4 EB 40 08 10 11")
    assert live.size == 10
    advert = hex_to_bytes("02 01 06") + hex_to_bytes("08 FF 30 03 8E 01 B1 B6 C4") + live.to_bytes()
    structures = parse_ad_structures(advert)
    assert [s.ad_type for s in structures] == [0x01, 0xFF, 0xFE]
    assert find_live_structures(advert)[0].data == live.to_data()
    for form, data in (
        ("auto", advert),
        ("advert", advert),
        ("ad", live.to_bytes()),
        ("auto", live.to_bytes()),
        ("data", live.to_data()),
        ("auto", live.to_data()),
    ):
        parsed = parse_frame(data, form)
        assert (parsed.device_id, parsed.data_identifier, parsed.payload) == (
            device,
            0x40,
            bytes([8, 16, 17]),
        )
    enc = LiveFrame(device, 0x00, bytes([7, 1, 2, 3, 4, 5]), encrypted=True, iv_counter=0x01020304)
    parsed = parse_frame(enc.to_bytes())
    assert parsed.encrypted and parsed.iv_counter == 0x01020304 and parsed.size == 2 + 4 + 4 + 1 + 6
    with pytest.raises(ValueError):
        parse_ad_structures(bytes([5, 0xFE, 1]))


@pytest.mark.parametrize(
    "frame", [f for f in VECTORS["frames"] if f["kind"] == "DFV"], ids=lambda f: f["id"]
)
def test_decode_vectors(frame):
    dfd = FRAMES[frame["dfd"]]
    decoder = Decoder()
    device = 0x12345678
    learned = decoder.decode(LiveFrame(device, int(payload(dfd["id"])[0]), payload(dfd["id"])[1:]))
    assert learned.kind == "DFD" and learned.field_indexes == dfd["expect"]["field_indexes"]
    decoded = decoder.decode(LiveFrame(device, payload(frame["id"])[0], payload(frame["id"])[1:]))
    assert decoded.error is None
    assert decoded.values() == frame["expect"]
    if "known_inconsistency" in frame:
        assert any("trailing" in w for w in decoded.warnings)
    else:
        assert decoded.warnings == []


@pytest.mark.parametrize(
    "frame", [f for f in VECTORS["frames"] if f["kind"] == "DFV"], ids=lambda f: f["id"]
)
def test_encode_vectors(frame):
    dfd = FRAMES[frame["dfd"]]
    category = dfd["expect"]["category"]
    fields = dfd["expect"]["field_indexes"]
    keyed = {}
    for index in fields:
        key = field_key(spec.field_by_index(category, index)["name"])
        if key in frame["expect"]:
            keyed[index] = frame["expect"][key]
    encoded = encode_dfv(category, fields, keyed)
    expected = payload(frame["id"])[1:]
    if "known_inconsistency" in frame:
        assert encoded == expected[:-1]  # the spec example widened Hardware Version to 2 bytes
    else:
        assert encoded == expected


def test_encode_dpi_and_dfd():
    assert encode_dpi([0, 8, 9]) == bytes([0x01, 0x03])
    assert encode_dpi([0, 8]) == bytes([0x01, 0x01])
    assert encode_dpi([0, 8], truncate=False) == bytes([1, 1, 0, 0, 0, 0, 0, 0])
    assert encode_dpi([60, 63]) == bytes([0, 0, 0, 0, 0, 0, 0, 0x90])
    assert encode_dfd([8, 16, 17]) == bytes([8, 16, 17])
    with pytest.raises(ValueError):
        encode_dfd(list(range(9)))
    with pytest.raises(ValueError):
        encode_dpi([64])


def test_rr_intervals_and_errors():
    fields = [0, 14, 254]
    payload_rr = encode_dfv("fitness", fields, {0: 70, 14: 9, 254: [850, 860]})
    assert payload_rr == bytes([0x07, 70, 9, 0x52, 0x03, 0x5C, 0x03])
    decoder = Decoder()
    decoder.learn_dfd(1, 0, fields)
    decoded = decoder.decode(LiveFrame(1, 0, payload_rr))
    assert decoded.values() == {
        "heart_rate": 70,
        "heart_beat_count": 9,
        "r_r_intervals": [850, 860],
    }
    with pytest.raises(ValueError):
        encode_dfv("fitness", fields, {1: 90})


def test_dfv_before_dfd_and_state():
    decoder = Decoder()
    out = decoder.decode(LiveFrame(7, 0x00, bytes([0x07, 1, 0, 2, 3, 0])))
    assert out.error and "DFD 0x40" in out.error
    dpi = decoder.decode(LiveFrame(7, 0xFF, bytes([0x01, 0x03])))
    assert dpi.dfv_indexes_present == [0, 8, 9]
    st = decoder.state(7)
    assert st.frames == 2 and st.dpi == [0, 8, 9] and st.kinds["DFV"] == 1


def test_advert_budget_wymu_layout():
    # flags (3) + NPE manufacturer data 2+2+9 (13) + live DFV 0 payload 7 -> 14 = 30 of 31
    b = advert_budget(AdvertLayout(manufacturer_data_bytes=9, live_payload_bytes=7))
    assert (b["total_bytes"], b["remaining_bytes"], b["fits"]) == (30, 1, True)
    too_big = advert_budget(AdvertLayout(manufacturer_data_bytes=9, live_payload_bytes=13))
    assert not too_big["fits"] and too_big["total_bytes"] == 36
    enc = advert_budget(AdvertLayout(flags=False, live_payload_bytes=7, encrypted=True))
    assert enc["total_bytes"] == 18
