"""The generator must reproduce the reference design's committed ble_live.c/.h byte for byte
from its live.xlsx. Runs when BLE_LIVE_ROOT points at NPE's reference design checkout."""

import pytest

from ble_live.generator import generate, write_sources
from ble_live.xlsx import import_xlsx


@pytest.mark.parametrize("name", ["ble_live.h", "ble_live.c"])
def test_generated_source_matches_reference_design(ref_root, tmp_path, name):
    generated = generate(import_xlsx(ref_root / "live.xlsx"), ref_root)
    (written,) = write_sources({name: generated[name]}, tmp_path)
    expected = (ref_root / name).read_bytes()
    actual = written.read_bytes()
    if actual != expected and actual.replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n"):
        pytest.fail(f"{name}: content identical, line endings differ")
    assert actual == expected, f"{name} differs from the reference design"
