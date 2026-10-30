"""Host side of the emulator command channel (see ble_live_demo/.../emulator_cli.h).

The nRF52 emulator speaks one line per command over the DK's virtual COM port at 115200 8N1
and answers with one `OK ...` or `ERR ...` line; `#` lines are information. `EmulatorClient`
wraps that; `SerialTransport` talks to a board and `MockTransport` reproduces the firmware's
command handling in Python so the tools and tests work without hardware.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import spec

BAUD = 115200
LG_MONITOR_CONTROL = (0x043E, 0x9A39)  # LG monitor control endpoint: never open it
JLINK_VID = 0x1366
NORDIC_VID = 0x1915

FIELD_MAX = {
    "power": 0xFFFF,
    "events": 0xFF,
    "accum": 0xFFFF,
    "batt": 0xFFFF,
    "proto": 0xFF,
    "model": 0xFFFF,
    "fw": 0xFFFF,
    "mfr": 0xFFFF,
    "supported": 0xFF,
    "available": 0xFF,
    "equip": 0xFF,
}
STEP_MIN_MS, STEP_MAX_MS, PATTERN_MAX = 20, 5000, 64


class EmulatorError(Exception):
    """The board answered ERR, or did not answer."""


class Transport(Protocol):
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout_s: float) -> str | None: ...
    def close(self) -> None: ...


# -- transports -------------------------------------------------------------------------------


class SerialTransport:
    """pyserial transport; imported lazily so the package works without a serial port."""

    def __init__(self, port: str, baud: int = BAUD) -> None:
        import serial  # type: ignore[import-untyped]

        self.port = port
        self._ser = serial.Serial(port, baud, timeout=0.05)

    def write_line(self, line: str) -> None:
        self._ser.write((line.strip() + "\n").encode("ascii"))

    def read_line(self, timeout_s: float) -> str | None:
        deadline = time.monotonic() + timeout_s
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self._ser.read(1)
            if not chunk:
                continue
            if chunk in (b"\n", b"\r"):
                if buf:
                    return buf.decode("ascii", "replace")
                continue
            buf += chunk
        return None

    def close(self) -> None:
        self._ser.close()


class MockTransport:
    """The firmware's command handling, in Python, for tests and dry runs."""

    def __init__(self, serial_number: int = 0xEBC4B6B1) -> None:
        self.state: dict[str, int] = {
            "power": 20,
            "events": 0,
            "accum": 0,
            "batt": 300,
            "proto": 1,
            "model": 0x8E00,
            "fw": 1 << 12,
            "mfr": 66,
            "supported": 2,
            "available": 2,
            "equip": 11,
        }
        self.serial_number = serial_number
        self.auto = True
        self.step_ms = 550
        self.pattern: list[int] = []
        self.resets = 0
        self._out: list[str] = ["# WYMU BLE Live emulator CLI ready; send help"]
        self.commands: list[str] = []

    def write_line(self, line: str) -> None:
        self.commands.append(line)
        self._out += self._handle(line.strip())

    def read_line(self, timeout_s: float) -> str | None:
        return self._out.pop(0) if self._out else None

    def close(self) -> None:
        pass

    def _handle(self, line: str) -> list[str]:
        argv = line.split()
        if not argv:
            return []
        cmd = argv[0].lower()
        arg1 = argv[1].lower() if len(argv) > 1 else None
        arg2 = argv[2] if len(argv) > 2 else None
        if cmd == "help":
            return [
                "OK commands: help | get | set <field> <value> | auto on|off | step <ms> | "
                "pattern default|<hex,...> | serial <value> | reset",
                "# fields: power events accum batt proto model fw mfr supported available equip",
            ]
        if cmd == "get":
            s = self.state
            pattern = "default" if not self.pattern else ",".join(f"{d:02X}" for d in self.pattern)
            return [
                f"OK power={s['power']} events={s['events']} accum={s['accum']} batt={s['batt']} "
                f"proto={s['proto']} model={s['model']} fw={s['fw']} mfr={s['mfr']} "
                f"supported={s['supported']} available={s['available']} equip={s['equip']} "
                f"serial=0x{self.serial_number:08X} auto={1 if self.auto else 0} "
                f"step={self.step_ms} pattern={pattern}"
            ]
        if cmd == "set":
            if arg1 is None or arg2 is None:
                return ["ERR usage: set <field> <value>"]
            try:
                value = int(arg2, 0)
            except ValueError:
                return ["ERR value is not a number"]
            if arg1 not in FIELD_MAX:
                return ["ERR unknown field; see help"]
            if value > FIELD_MAX[arg1] or value < 0:
                return [f"ERR {arg1} is at most {FIELD_MAX[arg1]}"]
            self.state[arg1] = value
            return [f"OK {arg1}={value}"]
        if cmd == "auto":
            if arg1 in ("on", "off"):
                self.auto = arg1 == "on"
                return [f"OK auto={1 if self.auto else 0}"]
            return ["ERR usage: auto on|off"]
        if cmd == "step":
            try:
                ms = int(arg1 or "", 0)
            except ValueError:
                ms = -1
            if not STEP_MIN_MS <= ms <= STEP_MAX_MS:
                return [f"ERR step is {STEP_MIN_MS}..{STEP_MAX_MS} ms"]
            self.step_ms = ms
            return [f"OK step={ms}"]
        if cmd == "pattern":
            if arg1 is None:
                return ["ERR usage: pattern default|<hex,...>"]
            if arg1 == "default":
                self.pattern = []
                return ["OK pattern=default"]
            entries = []
            for tok in arg1.split(","):
                try:
                    di = int(tok, 16)
                except ValueError:
                    di = -1
                if not tok or di < 0 or di > 0xFF or (di != 0xFF and di >= 0x80):
                    return [
                        "ERR pattern entries are DPI (FF), DFD (40..7F) or DFV (00..3F) hex bytes"
                    ]
                entries.append(di)
                if len(entries) > PATTERN_MAX:
                    return [f"ERR at most {PATTERN_MAX} entries"]
            self.pattern = entries
            return [f"OK pattern={len(entries)} entries"]
        if cmd == "serial":
            try:
                self.serial_number = int(arg1 or "", 0) & 0xFFFFFFFF
            except ValueError:
                return ["ERR usage: serial <value>"]
            return [f"OK serial=0x{self.serial_number:08X}"]
        if cmd == "reset":
            self.resets += 1
            self.pattern = []
            self.auto = True
            return ["OK resetting", "# WYMU BLE Live emulator CLI ready; send help"]
        return ["ERR unknown command; send help"]


# -- client -----------------------------------------------------------------------------------


def parse_kv(line: str) -> dict[str, Any]:
    """'OK power=32 pattern=FF,40' -> {'power': 32, 'pattern': [255, 64]}."""
    out: dict[str, Any] = {}
    for token in line.split()[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key == "pattern":
            out[key] = None if value == "default" else [int(x, 16) for x in value.split(",") if x]
        elif key == "serial":
            out[key] = int(value, 0)
        else:
            try:
                out[key] = int(value, 0)
            except ValueError:
                out[key] = value
    return out


@dataclass
class EmulatorClient:
    transport: Transport
    port: str = "mock"
    info: list[str] = field(default_factory=list)

    def command(self, line: str, timeout_s: float = 1.5) -> str:
        """Send one command; return its OK line or raise EmulatorError on ERR / silence."""
        self.transport.write_line(line)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            reply = self.transport.read_line(max(0.0, deadline - time.monotonic()))
            if reply is None:
                if isinstance(self.transport, MockTransport):
                    break
                continue
            if reply.startswith("#"):
                self.info.append(reply)
                continue
            if reply.startswith("OK"):
                return reply
            if reply.startswith("ERR"):
                raise EmulatorError(reply[3:].strip() or "error")
            self.info.append(reply)  # stray log text
        raise EmulatorError(f"no reply to {line!r} within {timeout_s:g} s")

    def drain(self, timeout_s: float = 0.3) -> list[str]:
        """Collect pending lines (banner, stray output) without sending anything."""
        lines = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            reply = self.transport.read_line(max(0.0, deadline - time.monotonic()))
            if reply is None:
                if isinstance(self.transport, MockTransport):
                    break
                continue
            lines.append(reply)
        self.info += lines
        return lines

    def get(self) -> dict[str, Any]:
        return parse_kv(self.command("get"))

    def set_value(self, name: str, value: int) -> dict[str, Any]:
        name = name.lower()
        if name not in FIELD_MAX:
            raise EmulatorError(f"unknown field {name!r}; one of {sorted(FIELD_MAX)}")
        return parse_kv(self.command(f"set {name} {int(value)}"))

    def set_auto(self, on: bool) -> dict[str, Any]:
        return parse_kv(self.command(f"auto {'on' if on else 'off'}"))

    def set_step_ms(self, ms: int) -> dict[str, Any]:
        return parse_kv(self.command(f"step {int(ms)}"))

    def set_pattern(self, pattern: list[int] | None) -> str:
        if not pattern:
            return self.command("pattern default")
        for di in pattern:
            if spec.di_kind(di) not in ("DPI", "DFD", "DFV"):
                raise EmulatorError(f"0x{di:02X} is not a DPI, DFD or DFV data identifier")
        return self.command("pattern " + ",".join(f"{d:02X}" for d in pattern))

    def set_serial(self, serial_number: int) -> dict[str, Any]:
        return parse_kv(self.command(f"serial 0x{serial_number & 0xFFFFFFFF:08X}"))

    def reset(self) -> str:
        return self.command("reset")

    def close(self) -> None:
        self.transport.close()


def connect(port: str, baud: int = BAUD) -> EmulatorClient:
    """Open a board ('COM6', '/dev/ttyACM0') or the in-process mock ('mock')."""
    if port.lower() == "mock":
        client = EmulatorClient(MockTransport(), "mock")
    else:
        client = EmulatorClient(SerialTransport(port, baud), port)
    client.drain()
    return client


def list_ports() -> list[dict[str, Any]]:
    """Serial ports that could carry the emulator CLI. The LG monitor's control endpoint
    (VID 043E, PID 9A39) is left out entirely; J-Link and Nordic VIDs are flagged as likely."""
    try:
        from serial.tools import list_ports as lp  # type: ignore[import-untyped]
    except ImportError:
        return []
    out = []
    for p in lp.comports():
        vid, pid = p.vid, p.pid
        if (vid, pid) == LG_MONITOR_CONTROL:
            continue
        out.append(
            {
                "device": p.device,
                "description": p.description,
                "vid": f"{vid:04X}" if vid is not None else None,
                "pid": f"{pid:04X}" if pid is not None else None,
                "serial_number": p.serial_number,
                "likely_dk": vid in (JLINK_VID, NORDIC_VID),
            }
        )
    return sorted(out, key=lambda d: (not d["likely_dk"], d["device"]))
