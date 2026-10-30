from ble_live import spec


def test_version_and_counts():
    assert spec.version() == "1.7.0"
    assert [r["index"] for r in spec.fields("fitness")] == [*range(18), 254, 255]
    assert [r["index"] for r in spec.fields("meta")] == [*range(16), 255]
    assert [r["index"] for r in spec.fields("equipment")] == [*range(1, 15), 21, 120]


def test_known_fields():
    power = spec.field_by_index("fitness", 8)
    assert power["name"] == "Power" and power["type"] == "uint16" and power["size_bytes"] == 2
    assert power["units"] == "1 W"
    rr = spec.field_by_index("fitness", 254)
    assert rr["name"] == "R-R Intervals" and rr.get("array") and rr["size_bytes"] is None
    assert spec.field_by_index("meta", 9)["type"] == "int8"
    assert spec.field_by_index("meta", 10)["name"] == "Equipment Type"
    assert spec.field_by_index("equipment", 120)["name"] == "Heart Rate Monitor"
    assert spec.field_by_index("equipment", 11)["name"] == "Running Speed"


def test_di_ranges_and_kinds():
    ranges = spec.load_spec()["di_ranges"]
    top = [r["range"] for r in ranges]
    assert top[:2] == ["0:63", "64:127"]
    assert [s["range"] for s in ranges[0]["sub"]] == ["0:7", "8:15", "16:59", "60-63"]
    assert spec.di_kind(0xFF) == "DPI"
    assert spec.di_kind(0x40) == "DFD"
    assert spec.di_kind(0x09) == "DFV"
    assert spec.di_kind(0x80) == "reserved"


def test_revisions():
    revs = spec.load_spec()["revisions"]
    assert revs[0]["version"] == "1.7.0"
    assert revs[-1]["version"] == "1.0" and "Initial" in revs[-1]["description"]
