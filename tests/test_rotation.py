import pytest

from ble_live import rotation

# Spec 1.7.0, "Normal Advertising Rotation" table, 19 frames at 211.25 ms.
SPEC_NORMAL = [
    0xFF, 0x40, 0x00, 0x48, 0x00, 0x08, 0x00, 0x08, 0x40, 0x00, 0x00, 0x00, 0x00,
    0x40, 0x00, 0x00, 0x00, 0x00, 0x00,
]  # fmt: skip


def test_analyze_spec_normal_rotation():
    stats = rotation.analyze(SPEC_NORMAL, 211.25)
    assert stats.cycle_frames == 19
    assert stats.dfd_max_gap_s[0] == pytest.approx(7 * 0.21125)
    # the spec's own example repeats DFD 8 once per 19 frames: 4.01 s, just over its 4 s rule
    assert stats.dfd_max_gap_s[8] == pytest.approx(19 * 0.21125)
    assert any("DFV 8" in p for p in stats.problems)
    assert stats.dpi_period_s == pytest.approx(19 * 0.21125)
    assert stats.dfv_share == pytest.approx(14 / 19)
    assert stats.dfv_rate_hz[0] > stats.dfv_rate_hz[8]


def test_analyze_flags_missing_pieces():
    stats = rotation.analyze([0x40, 0x00, 0x09], 500)
    assert any("no DPI" in p for p in stats.problems)
    assert any("DFV 9 is sent but its DFD" in p for p in stats.problems)
    with pytest.raises(ValueError):
        rotation.analyze([], 500)


def test_plan_fast_phase():
    assert rotation.plan([0, 8], 20, phase="fast") == [0xFF, 0x40, 0x00, 0x48, 0x08]


def test_plan_wymu_slow_phase():
    pattern = rotation.plan([0, 8, 9], 556.25)
    stats = rotation.analyze(pattern, 556.25)
    assert stats.problems == []
    assert pattern[0] == 0xFF
    assert all(gap <= 4.0 for gap in stats.dfd_max_gap_s.values())
    assert set(pattern) == {0xFF, 0x40, 0x48, 0x49, 0x00, 0x08, 0x09}
    assert stats.dfv_rate_hz[0] > 4 * stats.dfv_rate_hz[8]
    assert 15 <= stats.dpi_period_s <= 25
    assert rotation.describe(pattern[:3]) == [
        "DPI",
        "DFD 0",
        "DFV 0",
    ] or "DPI" in rotation.describe(pattern)


def test_plan_spec_interval():
    pattern = rotation.plan([0, 8], 211.25)
    stats = rotation.analyze(pattern, 211.25)
    assert stats.problems == []
    assert stats.dfv_share > 0.7


def test_plan_rejects_impossible_interval():
    with pytest.raises(ValueError):
        rotation.plan(list(range(8)), 1000)
