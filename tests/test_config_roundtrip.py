from ble_live.config import Enum, LiveConfig, Struct
from ble_live.xlsx import export_xlsx, import_xlsx


def test_example_config_shape(example_cfg):
    names = [(type(o).__name__, o.name) for o in example_cfg.objects]
    assert names == [
        ("Struct", "Fitness Data"),
        ("Struct", "Meta Data"),
        ("Enum", "Equipment Type"),
    ]
    fitness, meta, equipment = example_cfg.objects
    assert isinstance(fitness, Struct)
    assert fitness.repeat == 4 and (fitness.dfv_min, fitness.dfv_max) == (0, 7)
    assert [f.value for f in fitness.included] == [8, 16, 17]
    assert {f.dfv for f in fitness.included} == {0}
    # Firmware Version (5) is listed before Model Number (3) so DFD 9 starts with 0x05
    assert isinstance(meta, Struct)
    assert [f.value for f in meta.included] == [0, 2, 5, 3, 7, 8, 10, 11]
    assert isinstance(equipment, Enum)
    assert [m.value for m in equipment.members if not m.include] == [11]  # Running Speed clashes
    assert example_cfg.validate() == []


def test_xlsx_roundtrip(example_cfg, tmp_path):
    export_xlsx(example_cfg, tmp_path / "live.xlsx")
    again = import_xlsx(tmp_path / "live.xlsx")
    again.spec_version = example_cfg.spec_version
    assert again == example_cfg


def test_json_roundtrip(example_cfg, tmp_path):
    example_cfg.save_json(tmp_path / "live.json")
    assert LiveConfig.load_json(tmp_path / "live.json") == example_cfg


def test_reference_workbook_matches_example(ref_root, example_cfg):
    cfg = import_xlsx(ref_root / "live.xlsx")
    cfg.spec_version = example_cfg.spec_version
    assert cfg == example_cfg


def test_validate_reports_bad_dfv(example_cfg):
    example_cfg.structs[0].included[0].dfv = 9
    assert any("outside 0-7" in p for p in example_cfg.validate())
