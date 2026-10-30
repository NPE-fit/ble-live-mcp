"""The generated receiver scaffolds decode the golden vectors (Python executed, C compiled)."""

import shutil
import subprocess
import sys

import pytest

from ble_live import spec
from ble_live.codec import field_key, hex_to_bytes
from ble_live.scaffold import LANGUAGES, scaffold

VECTORS = spec.load_vectors()
FRAMES = {f["id"]: f for f in VECTORS["frames"]}
DEVICE = bytes([0xB1, 0xB6, 0xC4, 0xEB])


def data(frame_id: str) -> bytes:
    return DEVICE + hex_to_bytes(FRAMES[frame_id]["payload_hex"])


def test_python_scaffold_decodes_vectors():
    namespace: dict = {}
    exec(compile(scaffold("python")["source"], "ble_live_receiver.py", "exec"), namespace)
    decoder = namespace["LiveDecoder"]()
    for frame in VECTORS["frames"]:
        if frame["kind"] != "DFV":
            continue
        dfd = FRAMES[frame["dfd"]]
        assert decoder.decode_structure(0xFE, data(dfd["id"]))["kind"] == "DFD"
        out = decoder.decode_structure(0xFE, data(frame["id"]))
        assert out["kind"] == "DFV" and "error" not in out
        got = {field_key(name): value for name, value in out["values"].items()}
        assert got == frame["expect"], frame["id"]
    dpi = decoder.decode_structure(0xFE, data("wymu-1.7.0-dpi"))
    assert dpi["dfv_indexes"] == [0, 8, 9]
    enc = decoder.decode_structure(0xFD, DEVICE + bytes(4) + bytes([0x00, 1, 2]))
    assert enc["kind"] == "encrypted"


C_MAIN = r"""
#include <stdio.h>
bool ble_live_decrypt(uint32_t d, uint32_t c, uint8_t *p, size_t l)
{ (void)d; (void)c; (void)p; (void)l; return false; }
static void cb(uint32_t id, uint8_t dfv, const ble_live_field_t *f, int32_t v,
               const uint8_t *raw, size_t n, void *ctx)
{ (void)id; (void)raw; (void)n; (void)ctx; printf("%u %s=%ld\n", dfv, f->name, (long)v); }
int main(void)
{
    ble_live_decoder_t d; memset(&d, 0, sizeof d);
    uint8_t dfd[] = {0xB1,0xB6,0xC4,0xEB,0x40,0x08,0x10,0x11};
    uint8_t dfv[] = {0xB1,0xB6,0xC4,0xEB,0x00,0x07,0x20,0x00,0x05,0xA0,0x00};
    uint8_t dfd9[] = {0xB1,0xB6,0xC4,0xEB,0x49,0x05,0x03,0x0B};
    uint8_t dfv9[] = {0xB1,0xB6,0xC4,0xEB,0x09,0x07,0x03,0x10,0x00,0x8E,0x42,0x00};
    uint8_t enc[] = {0xB1,0xB6,0xC4,0xEB,0,0,0,0,0x00,0x07,0x01};
    printf("dfv-before-dfd=%d\n", ble_live_decode(&d, 0xFE, dfv, sizeof dfv, cb, NULL));
    ble_live_decode(&d, 0xFE, dfd, sizeof dfd, cb, NULL);
    ble_live_decode(&d, 0xFE, dfv, sizeof dfv, cb, NULL);
    ble_live_decode(&d, 0xFE, dfd9, sizeof dfd9, cb, NULL);
    ble_live_decode(&d, 0xFE, dfv9, sizeof dfv9, cb, NULL);
    printf("encrypted=%d\n", ble_live_decode(&d, 0xFD, enc, sizeof enc, cb, NULL));
    return 0;
}
"""


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not installed")
def test_c_scaffold_compiles_and_decodes(tmp_path):
    source = tmp_path / "receiver.c"
    source.write_text(scaffold("c")["source"] + C_MAIN, encoding="utf-8")
    exe = tmp_path / ("receiver.exe" if sys.platform == "win32" else "receiver")
    subprocess.run(
        ["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(exe), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    assert out.splitlines() == [
        "dfv-before-dfd=-1",
        "0 Power=32",
        "0 Power Event Count=5",
        "0 Accumulated Power=160",
        "9 Firmware Version=4099",
        "9 Model #=36352",
        "9 ANT+ Manufacturer ID=66",
        "encrypted=-1",
    ]


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_scaffold_carries_tables_and_quirks(language):
    out = scaffold(language)
    assert out["language"] == language and out["filename"]
    src = out["source"]
    assert "Power Event Count" in src and "Zone Threshold Size" in src
    assert "gatewaykit-beta-dfd-heuristic" in src
    assert spec.version() in src
    assert "0xFD" in src and "SHA-256" in src
