"""Live capture sources: a GEM3's multicast stream, or the PC's Bluetooth adapter.

- gem3: a GEM3 PoE gateway forwards raw BLE advertisements in UDP datagrams to the multicast
  group 239.78.80.1:51113. Joining the group is passive; nothing is sent to the GEM3. Every
  0xFE/0xFD structure found in a datagram (via `capture.scan_live_structures`) is decoded.
- bluetooth: bleak on Windows exposes the raw advertisement sections (WinRT data_sections), so
  0xFE/0xFD structures can be read directly. BlueZ (Linux) and CoreBluetooth (macOS) do not
  expose unknown AD types, so this source reports unsupported there.
"""

from __future__ import annotations

import asyncio
import socket
import struct
import sys
import time
from typing import Any

from .capture import scan_live_structures
from .codec import AdStructure, DecodedFrame, Decoder, parse_live_structure

GEM3_GROUP = "239.78.80.1"
GEM3_PORT = 51113
LIVE_TYPES = (0xFE, 0xFD)


def _report(
    decoder: Decoder, frames: list[DecodedFrame], extra: dict[str, Any], max_frames: int
) -> dict[str, Any]:
    errors = sum(1 for f in frames if f.error)
    dfv_before_dfd = sum(1 for f in frames if f.error and f.error.startswith("DFD "))
    diagnostics = []
    if dfv_before_dfd:
        diagnostics.append(f"{dfv_before_dfd} DFV frame(s) arrived before their DFD")
    for device_id, st in decoder.devices.items():
        if st.frames and st.kinds.get("DFV", 0) == 0:
            diagnostics.append(f"device 0x{device_id:08X}: only definitions were heard")
    return {
        **extra,
        "frames_total": len(frames),
        "errors": errors,
        "devices": {f"0x{k:08X}": v.to_dict() for k, v in decoder.devices.items()},
        "diagnostics": diagnostics,
        "frames": [f.to_dict() for f in frames[:max_frames]],
        "frames_truncated": max(0, len(frames) - max_frames),
    }


def ingest_blob(decoder: Decoder, blob: bytes) -> list[DecodedFrame]:
    """Decode every BLE Live structure inside a datagram or dump."""
    return [decoder.decode_bytes(s, "ad") for s in scan_live_structures(blob)]


# -- GEM3 -------------------------------------------------------------------------------------


def gem3_listen(
    duration_s: float,
    decoder: Decoder | None = None,
    interface_ip: str | None = None,
    group: str = GEM3_GROUP,
    port: int = GEM3_PORT,
    max_frames: int = 200,
) -> dict[str, Any]:
    """Join the GEM3 multicast group for `duration_s` seconds and decode what arrives."""
    decoder = decoder or Decoder()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(("", port))
    iface = socket.inet_aton(interface_ip) if interface_ip else struct.pack("!I", socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + iface)
    sock.settimeout(0.5)
    frames: list[DecodedFrame] = []
    datagrams = 0
    senders: set[str] = set()
    deadline = time.monotonic() + duration_s
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                continue
            datagrams += 1
            senders.add(addr[0])
            frames += ingest_blob(decoder, data)
    finally:
        sock.close()
    extra = {
        "source": "gem3",
        "group": f"{group}:{port}",
        "interface_ip": interface_ip,
        "duration_s": duration_s,
        "datagrams": datagrams,
        "senders": sorted(senders),
    }
    if datagrams == 0:
        extra["note"] = (
            "no datagrams: is a GEM3 on this subnet and forwarding BLE? On a multi-homed host pass "
            "interface_ip for the adapter that reaches it"
        )
    return _report(decoder, frames, extra, max_frames)


# -- Bluetooth (Windows) ---------------------------------------------------------------------


def _buffer_bytes(buf: Any) -> bytes:
    for conv in (bytes, lambda b: bytes(memoryview(b)), lambda b: bytes(bytearray(b))):
        try:
            return conv(buf)
        except Exception:  # noqa: BLE001 - try the next conversion
            continue
    raise TypeError(f"cannot read advertisement buffer of type {type(buf).__name__}")


def raw_sections(details: Any) -> list[tuple[int, bytes]]:
    """(AD type, data) pairs from bleak's Windows `RawAdvData` (advert and scan response)."""
    out: list[tuple[int, bytes]] = []
    for name in ("adv", "scan"):
        args = getattr(details, name, None)
        if args is None:
            continue
        sections = args.advertisement.data_sections
        try:
            count = len(sections)
            get = sections.__getitem__
        except TypeError:
            count = sections.size
            get = sections.get_at
        for i in range(count):
            sec = get(i)
            out.append((int(sec.data_type), _buffer_bytes(sec.data)))
    return out


def live_structures_from_sections(sections: list[tuple[int, bytes]]) -> list[AdStructure]:
    return [AdStructure(t, d) for t, d in sections if t in LIVE_TYPES]


async def bluetooth_scan_async(
    duration_s: float, decoder: Decoder | None = None, max_frames: int = 200
) -> dict[str, Any]:
    decoder = decoder or Decoder()
    extra: dict[str, Any] = {"source": "bluetooth", "duration_s": duration_s}
    if sys.platform != "win32":
        extra["note"] = "raw AD types 0xFE/0xFD are only exposed by the Windows (WinRT) backend"
        return _report(decoder, [], extra, max_frames)
    try:
        from bleak import BleakScanner
    except ImportError:
        extra["note"] = "bleak is not installed (pip install bleak)"
        return _report(decoder, [], extra, max_frames)
    frames: list[DecodedFrame] = []
    seen: dict[str, str | None] = {}
    errors: list[str] = []

    def on_advert(device: Any, adv: Any) -> None:
        seen[device.address] = adv.local_name
        try:
            for structure in live_structures_from_sections(raw_sections(device.details)):
                frames.append(decoder.decode(parse_live_structure(structure)))
        except Exception as exc:  # noqa: BLE001 - keep scanning
            if len(errors) < 5:
                errors.append(f"{device.address}: {exc!r}")

    scanner = BleakScanner(on_advert, scanning_mode="active")
    await scanner.start()
    await asyncio.sleep(duration_s)
    await scanner.stop()
    extra.update(
        {
            "devices_seen": len(seen),
            "live_devices": len({f.frame.device_id for f in frames}),
            "scan_errors": errors,
        }
    )
    if not frames:
        extra["note"] = "no BLE Live structures heard; is a sensor on and advertising nearby?"
    return _report(decoder, frames, extra, max_frames)


def bluetooth_scan(
    duration_s: float, decoder: Decoder | None = None, max_frames: int = 200
) -> dict[str, Any]:
    return asyncio.run(bluetooth_scan_async(duration_s, decoder, max_frames))


def scan_live(
    source: str,
    duration_s: float,
    decoder: Decoder | None = None,
    interface_ip: str | None = None,
    max_frames: int = 200,
) -> dict[str, Any]:
    if duration_s <= 0 or duration_s > 300:
        raise ValueError("duration_s is 0..300 seconds")
    if source == "gem3":
        return gem3_listen(duration_s, decoder, interface_ip, max_frames=max_frames)
    if source == "bluetooth":
        return bluetooth_scan(duration_s, decoder, max_frames)
    raise ValueError("source is 'gem3' or 'bluetooth'")
