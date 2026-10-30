"""Field sizes the WASP .NET library uses (BluetoothLive.cs EnumMetaData attributes, read
2026-10-29) against the spec tables. Documents where the two disagree so the decoder's choice
is explicit."""

from ble_live import spec

DOTNET_FITNESS = {
    0: 1, 1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 2, 7: 2, 8: 2, 9: 2, 10: 1, 11: 2, 12: 2, 13: 2,
    14: 1, 15: 1, 254: None,  # RRIntervals: ushort array
}  # fmt: skip
DOTNET_META = {
    0: 1, 1: 1, 2: 2, 3: 2, 4: 1, 5: 2, 6: 2, 7: 1, 8: 1, 9: 1, 10: 1, 11: 2, 12: 1, 13: 1,
    14: None,  # ZoneThresholds byte array (spec: Zone Threshold Size, Uint8)
    15: 1,  # UICharacteristics (spec: Zone Thresholds, variable)
}  # fmt: skip
KNOWN_DIFFERENCES = {("meta", 14), ("meta", 15)}


def test_dotnet_sizes_match_spec_except_known():
    differences = set()
    for category, table in (("fitness", DOTNET_FITNESS), ("meta", DOTNET_META)):
        for index, size in table.items():
            if spec.size_bytes(category, index) != size:
                differences.add((category, index))
    assert differences == KNOWN_DIFFERENCES


def test_dotnet_signed_rssi_matches():
    assert spec.field_by_index("meta", 9)["type"] == "int8"  # sbyte in .NET
