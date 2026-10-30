from pathlib import Path

from ble_live.codec import AdvertLayout
from ble_live.config import Field, LiveConfig, Struct
from ble_live.design import check_design, frame_payload_sizes, legacy_tx_pattern
from ble_live.xlsx import import_xlsx

ROOT = Path(__file__).resolve().parents[2]


def codes(findings, severity=None):
    return [f.code for f in findings if severity is None or f.severity == severity]


def test_wymu_config_lints_clean_but_notes_rotation():
    cfg = import_xlsx(ROOT / "live.xlsx")
    sizes = frame_payload_sizes(cfg)
    assert sizes == {0xFF: 8, 0x40: 3, 0x00: 6, 0x48: 5, 0x08: 7, 0x49: 3, 0x09: 7}
    assert legacy_tx_pattern(cfg) == [0xFF, 0x40, 0x48, 0x49, 0x00, 0x00, 0x00, 0x00, 0x08, 0x09]
    findings = check_design(cfg, interval_ms=546.25, layout=AdvertLayout(manufacturer_data_bytes=6))
    assert codes(findings, "error") == []
    assert "legacy-rotation" in codes(findings, "warning")
    assert "equipment-type-numbering" in codes(findings, "info")
    # live.xlsx lists Model Number (0x03) first in DFV 9 followed by two fields: GatewayKit
    # would drop the 0x03 as a flag byte. The WYMU firmware reorders to FW, Model, Mfr.
    heuristic = [f for f in findings if f.code == "gatewaykit-dfd-heuristic"]
    assert len(heuristic) == 1 and heuristic[0].where == "DFV 9"
    # the generator writes all 8 DPI bytes, so the DPI is the largest frame of the emulator
    largest = next(f for f in findings if f.code == "largest-frame")
    assert "0xFF" in largest.message and "28 of 31" in largest.message
    assert "dpi-truncation" in codes(findings, "info")


def meta_struct(fields):
    return Struct(name="Meta Data", repeat=1, dfv_min=8, dfv_max=15, fields=fields)


def test_gatewaykit_heuristic_and_sizes():
    cfg = LiveConfig(
        objects=[
            Struct(
                name="Fitness Data",
                repeat=4,
                dfv_min=0,
                dfv_max=7,
                fields=[Field("Power", "uint8_t", 8, include=True, dfv=0)],
            ),
            meta_struct(
                [
                    Field("Model Number", "uint16_t", 3, include=True, dfv=9),
                    Field("Firmware Version", "uint16_t", 5, include=True, dfv=9),
                    Field("ANT Manufacturer ID", "uint16_t", 11, include=True, dfv=9),
                    Field("Hardware Version", "uint8_t", 4, include=True, dfv=8),
                ]
            ),
        ]
    )
    findings = check_design(cfg)
    assert "gatewaykit-dfd-heuristic" in codes(findings, "warning")
    assert "hw-version-width" in codes(findings, "warning")
    size_errors = [f for f in findings if f.code == "field-size"]
    assert len(size_errors) == 1 and "Power" in size_errors[0].message


def test_rr_rules_and_budget():
    cfg = LiveConfig(
        objects=[
            Struct(
                name="Fitness Data",
                repeat=2,
                dfv_min=0,
                dfv_max=7,
                fields=[
                    Field("RR Intervals", "uint16_t", 254, include=True, dfv=0, repeat=4),
                    Field("Heart Rate", "uint8_t", 0, include=True, dfv=0),
                ],
            )
        ]
    )
    findings = check_design(cfg, layout=AdvertLayout(manufacturer_data_bytes=20))
    assert "rr-not-last" in codes(findings, "error")
    assert "rr-without-heartbeat" in codes(findings, "warning")
    assert "advert-budget" in codes(findings, "error")
