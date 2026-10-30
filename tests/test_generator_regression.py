"""The refactored generator must reproduce the committed ble_live.c/.h byte for byte.

The committed files were produced by the original live.py from the committed live.xlsx
(verified before the refactor: running live.py left the working tree clean).
"""

from pathlib import Path

import pytest

from ble_live.generator import generate, write_sources
from ble_live.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def generated():
    cfg = import_xlsx(ROOT / "live.xlsx")
    return generate(cfg, ROOT)


@pytest.mark.parametrize("name", ["ble_live.h", "ble_live.c"])
def test_generated_source_matches_committed(generated, tmp_path, name):
    (written,) = write_sources({name: generated[name]}, tmp_path)
    expected = (ROOT / name).read_bytes()
    actual = written.read_bytes()
    if actual != expected and actual.replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n"):
        pytest.fail(f"{name}: content identical, line endings differ")
    assert actual == expected, f"{name} differs from the committed file"
