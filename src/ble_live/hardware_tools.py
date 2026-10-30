"""MCP tools that touch the bench: the nRF52 emulator's serial command channel and live
capture from a GEM3 or the PC's Bluetooth adapter. Registered on the server by `register`;
left out when the server runs read-only (hosted)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import emulator as emu
from .codec import Decoder
from .livescan import scan_live as _scan_live

HARDWARE_TOOLS = (
    "list_serial_ports",
    "emulator_connect",
    "emulator_disconnect",
    "emulator_status",
    "emulator_set",
    "emulator_auto",
    "emulator_set_pattern",
    "emulator_set_step",
    "emulator_set_serial",
    "emulator_command",
    "scan_live",
)

_clients: dict[str, emu.EmulatorClient] = {}


def _client(port: str) -> emu.EmulatorClient:
    client = _clients.get(port)
    if client is None:
        raise emu.EmulatorError(f"not connected to {port!r}; call emulator_connect first")
    return client


def register(mcp: MCPServer, get_decoder: Callable[[str], Decoder]) -> None:
    @mcp.tool()
    def list_serial_ports() -> dict[str, Any]:
        """Serial ports that may carry the emulator's command channel. A Nordic DK shows as a
        J-Link CDC UART port (likely_dk). The LG monitor control endpoint is never listed."""
        return {"ports": emu.list_ports()}

    @mcp.tool()
    def emulator_connect(port: str = "mock", baud: int = emu.BAUD) -> dict[str, Any]:
        """Open the emulator command channel on a serial port ('COM6', '/dev/ttyACM0'), or
        'mock' for an in-process stand-in with the same commands. Returns the board's state."""
        if port in _clients:
            _clients[port].close()
        client = emu.connect(port, baud)
        _clients[port] = client
        return {"port": port, "banner": client.info[-3:], "state": client.get()}

    @mcp.tool()
    def emulator_disconnect(port: str = "mock") -> dict[str, Any]:
        """Close the command channel."""
        client = _clients.pop(port, None)
        if client:
            client.close()
        return {"port": port, "closed": client is not None}

    @mcp.tool()
    def emulator_status(port: str = "mock") -> dict[str, Any]:
        """Every live value, the serial, auto mode, rotation step and pattern override."""
        return {"port": port, "state": _client(port).get()}

    @mcp.tool()
    def emulator_set(port: str, field: str, value: int, auto_off: bool = True) -> dict[str, Any]:
        """Set one live value on the board: power, events, accum, batt (0.01 V), proto, model,
        fw, mfr, supported, available, equip. The simulator is switched off first (auto_off)
        so the value stays on the air."""
        client = _client(port)
        result: dict[str, Any] = {"port": port}
        if auto_off and field.lower() in ("power", "events", "accum", "batt"):
            result["auto"] = client.set_auto(False)
        result["set"] = client.set_value(field, value)
        return result

    @mcp.tool()
    def emulator_auto(port: str, on: bool) -> dict[str, Any]:
        """Turn the board's simulated power and battery values on or off."""
        return {"port": port, **_client(port).set_auto(on)}

    @mcp.tool()
    def emulator_set_pattern(port: str, pattern: list[int] | None = None) -> dict[str, Any]:
        """Override the rotation with a list of data identifiers (e.g. from plan_rotation);
        empty or omitted restores the generated tx_pattern."""
        return {"port": port, "reply": _client(port).set_pattern(pattern)}

    @mcp.tool()
    def emulator_set_step(port: str, ms: int) -> dict[str, Any]:
        """Rotation period in ms (20..5000): how often the next data identifier goes on the air."""
        return {"port": port, **_client(port).set_step_ms(ms)}

    @mcp.tool()
    def emulator_set_serial(port: str, serial_number: int) -> dict[str, Any]:
        """Device id the board puts in the live data and the manufacturer data."""
        return {"port": port, **_client(port).set_serial(serial_number)}

    @mcp.tool()
    def emulator_command(port: str, line: str) -> dict[str, Any]:
        """Send a raw command line (see 'help') and return the reply."""
        return {"port": port, "reply": _client(port).command(line)}

    @mcp.tool()
    def scan_live(
        source: str,
        duration_s: float = 10,
        session: str = "default",
        interface_ip: str | None = None,
        max_frames: int = 200,
    ) -> dict[str, Any]:
        """Capture BLE Live frames from the air for duration_s seconds and decode them into
        the session. source 'gem3' joins a GEM3 gateway's multicast stream (passive;
        interface_ip selects the adapter on a multi-homed host); 'bluetooth' uses this PC's
        adapter via bleak (raw AD types are available on Windows only)."""
        return _scan_live(source, duration_s, get_decoder(session), interface_ip, max_frames)
