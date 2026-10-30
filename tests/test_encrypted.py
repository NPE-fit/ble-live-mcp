"""0xFD frames through the codec and captures, and the scan capture format."""

from ble_live import crypto
from ble_live.capture import decode_capture, parse_capture, scan_live_structures
from ble_live.codec import Decoder, LiveFrame, bytes_to_hex, encrypt_frame, hex_to_bytes

PRIVATE = bytes(range(16))
DEVICE = 0xEBC4B6B1


def wymu_frames():
    dfd = LiveFrame(DEVICE, 0x40, bytes([8, 16, 17]))
    dfv = LiveFrame(DEVICE, 0x00, hex_to_bytes("07 20 00 05 A0 00"))
    return dfd, dfv


def test_encrypted_round_trip_with_private_key():
    key = crypto.derive_key(PRIVATE, DEVICE)
    dfd, dfv = wymu_frames()
    sealed = encrypt_frame(dfv, key, iv_counter=42)
    assert sealed.encrypted and sealed.iv_counter == 42 and sealed.payload != dfv.payload
    assert sealed.size == dfv.size + 4

    no_key = Decoder()
    no_key.decode(dfd)
    out = no_key.decode(sealed)
    assert out.error and "no key" in out.error

    keyed = Decoder(crypto.KeyRing(private_key=PRIVATE))
    keyed.decode(dfd)
    out = keyed.decode(sealed)
    assert out.error is None and out.decrypted
    assert out.values() == {"power": 32, "power_event_count": 5, "accumulated_power": 160}
    d = out.to_dict()
    assert d["encrypted"] and d["decrypted"] and d["iv_counter"] == 42
    assert d["payload_hex"] == "07 20 00 05 A0 00"


def test_encrypted_round_trip_with_device_key_and_wrong_counter():
    key = crypto.derive_key(PRIVATE, DEVICE)
    dfd, dfv = wymu_frames()
    ring = crypto.KeyRing()
    ring.set_key(DEVICE, key)
    dec = Decoder(ring)
    dec.decode(dfd)
    good = dec.decode(encrypt_frame(dfv, key, 7))
    assert good.values()["power"] == 32
    # a stale counter yields garbage, not an exception
    wrong = LiveFrame(DEVICE, 0x00, encrypt_frame(dfv, key, 7).payload, True, 8)
    bad = dec.decode(wrong)
    assert bad.error is None or bad.warnings or bad.values() != good.values()


def test_scan_finds_structures_in_noise():
    dfd, dfv = wymu_frames()
    flags = hex_to_bytes("02 01 06")
    mfg = hex_to_bytes("0A FF 30 03 8E 01 B1 B6 C4 EB 00")
    junk = hex_to_bytes("4E 1B 00 01 B1 B6 C4 EB FE C4 12 34")  # a GEM3-like record header
    blob = junk + flags + mfg + dfd.to_bytes() + hex_to_bytes("00") + junk + dfv.to_bytes()
    found = scan_live_structures(blob)
    assert found == [dfd.to_bytes(), dfv.to_bytes()]
    text = "1.5 " + bytes_to_hex(blob) + "\n0000: " + bytes_to_hex(blob[:8]) + "  ....\n"
    records, skipped = parse_capture(text, "scan")
    assert [r.form for r in records] == ["ad", "ad"] and records[0].timestamp_s == 1.5
    assert skipped == ["line 2: no BLE Live structure found"]
    report = decode_capture(text, fmt="scan")
    assert report["errors"] == 0
    assert report["devices"]["0xEBC4B6B1"]["last_values"]["power"] == 32


def test_capture_nrf_connect_encrypted_line_decrypts_with_session_key():
    key = crypto.derive_key(PRIVATE, DEVICE)
    dfd, dfv = wymu_frames()
    sealed = encrypt_frame(dfv, key, 3)
    text = (
        f"Unknown EIR (0xFE): {bytes_to_hex(dfd.to_data())}\n"
        f"Unknown EIR (0xFD): {bytes_to_hex(sealed.to_data())}\n"
    )
    decoder = Decoder(crypto.KeyRing(private_key=PRIVATE))
    report = decode_capture(text, decoder)
    assert report["errors"] == 0
    assert report["frames"][1]["decrypted"] and report["frames"][1]["values"]["power"] == 32
