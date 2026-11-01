"""Shared fixtures: the packaged example configuration, and NPE's reference design checkout
when BLE_LIVE_ROOT points at one (templates and live.xlsx), otherwise those tests skip."""

import os
from pathlib import Path

import pytest

from ble_live import spec
from ble_live.config import LiveConfig


@pytest.fixture
def example_cfg() -> LiveConfig:
    return LiveConfig.from_dict(spec.load_example("power_meter"))


@pytest.fixture
def ref_root() -> Path:
    root = os.environ.get("BLE_LIVE_ROOT")
    if not root:
        pytest.skip("BLE_LIVE_ROOT not set (NPE reference design checkout)")
    path = Path(root)
    for name in ("ble_live_base.c", "ble_live_base.h", "live.xlsx"):
        if not (path / name).exists():
            pytest.skip(f"{name} not found under BLE_LIVE_ROOT")
    return path
