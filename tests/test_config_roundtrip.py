from pathlib import Path

from ble_live.config import Enum, LiveConfig, Struct
from ble_live.xlsx import export_xlsx, import_xlsx

ROOT = Path(__file__).resolve().parents[2]


def test_import_live_xlsx_shape():
    cfg = import_xlsx(ROOT / "live.xlsx")
    names = [(type(o).__name__, o.name) for o in cfg.objects]
    assert names == [
        ("Struct", "Fitness Data"),
        ("Struct", "Meta Data"),
        ("Enum", "Equipment Type"),
    ]
    fitness, meta, equipment = cfg.objects
    assert (
        isinstance(fitness, Struct)
        and fitness.repeat == 4
        and (fitness.dfv_min, fitness.dfv_max) == (0, 7)
    )
    assert [f.value for f in fitness.included] == [8, 16, 17]
    assert {f.dfv for f in fitness.included} == {0}
    # Firmware Version (5) is listed before Model Number (3) so DFD 9 starts with 0x05
    assert isinstance(meta, Struct)
    assert [f.value for f in meta.included] == [0, 2, 5, 3, 7, 8, 10, 11]
    assert isinstance(equipment, Enum)
    assert [m.value for m in equipment.members if not m.include] == [11]  # Running Speed clashes
    assert equipment.notes and "GatewayKit" in equipment.notes
    assert cfg.validate() == []


def test_xlsx_roundtrip(tmp_path):
    cfg = import_xlsx(ROOT / "live.xlsx")
    export_xlsx(cfg, tmp_path / "copy.xlsx")
    again = import_xlsx(tmp_path / "copy.xlsx")
    assert again == cfg


def test_json_roundtrip(tmp_path):
    cfg = import_xlsx(ROOT / "live.xlsx")
    cfg.spec_version = "1.7.0"
    cfg.save_json(tmp_path / "live.json")
    assert LiveConfig.load_json(tmp_path / "live.json") == cfg


def test_validate_reports_bad_dfv():
    cfg = import_xlsx(ROOT / "live.xlsx")
    cfg.structs[0].included[0].dfv = 9
    assert any("outside 0-7" in p for p in cfg.validate())
