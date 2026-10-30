import socket
import threading
import time

import pytest

from ble_live import livescan
from ble_live.codec import Decoder, LiveFrame, hex_to_bytes

DEVICE = 0xEBC4B6B1
DFD = LiveFrame(DEVICE, 0x40, bytes([8, 16, 17])).to_bytes()
DFV = LiveFrame(DEVICE, 0x00, hex_to_bytes("07 20 00 05 A0 00")).to_bytes()


def gem3_like_datagram(*structures: bytes) -> bytes:
    """Shape observed from a GEM3 PoE: 'BL' header, then records of
    [len] 4e 1b 00 [addr type] [6-byte address] [2 bytes] [AD structures...] [trailer]."""
    body = b""
    for s in structures:
        advert = hex_to_bytes("02 01 06") + s
        record = hex_to_bytes("4E 1B 00 01") + bytes.fromhex("B1B6C4EBFEC4") + b"\x00\x00" + advert
        body += bytes([len(record)]) + record + b"\xc8"
    return b"BL\x15\x00" + body


def test_ingest_blob_decodes_structures_inside_records():
    decoder = Decoder()
    frames = livescan.ingest_blob(decoder, gem3_like_datagram(DFD, DFV))
    assert [f.kind for f in frames] == ["DFD", "DFV"]
    assert frames[1].values()["power"] == 32


def test_gem3_listen_receives_loopback_multicast():
    port = 51200 + int(time.time()) % 200
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    try:
        sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
    except OSError:
        pytest.skip("no loopback multicast on this host")

    def pump():
        for _ in range(6):
            time.sleep(0.2)
            sender.sendto(gem3_like_datagram(DFD), (livescan.GEM3_GROUP, port))
            sender.sendto(gem3_like_datagram(DFV), (livescan.GEM3_GROUP, port))

    threading.Thread(target=pump, daemon=True).start()
    report = livescan.gem3_listen(2.0, interface_ip="127.0.0.1", port=port)
    sender.close()
    if report["datagrams"] == 0:
        pytest.skip("loopback multicast not delivered on this host")
    assert report["errors"] == 0
    assert report["devices"]["0xEBC4B6B1"]["last_values"]["accumulated_power"] == 160


class Buf(bytes):
    pass


class Section:
    def __init__(self, data_type, data):
        self.data_type, self.data = data_type, Buf(data)


class Advertisement:
    def __init__(self, sections):
        self.data_sections = sections


class Args:
    def __init__(self, sections):
        self.advertisement = Advertisement(sections)


class Details:
    def __init__(self, adv, scan=None):
        self.adv, self.scan = adv, scan


def test_raw_sections_and_live_structures():
    details = Details(
        Args([Section(0x01, b"\x06"), Section(0xFE, DFD[2:])]),
        Args([Section(0x09, b"WYMU")]),
    )
    sections = livescan.raw_sections(details)
    assert [t for t, _ in sections] == [0x01, 0xFE, 0x09]
    live = livescan.live_structures_from_sections(sections)
    assert len(live) == 1 and live[0].ad_type == 0xFE and live[0].data == DFD[2:]


def test_scan_live_validates_arguments():
    with pytest.raises(ValueError):
        livescan.scan_live("gem3", 0)
    with pytest.raises(ValueError):
        livescan.scan_live("radio", 1)
