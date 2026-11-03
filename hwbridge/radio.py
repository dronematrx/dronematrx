"""SiK telemetry radios (3DR / Holybro / RFD900): read and set the network ID.

Two radios talk only when their NETID (and air settings) match, so every
ground/air pair needs its own NETID or the pairs mix their traffic. This is
what Mission Planner's "SiK Radio" screen does, done here with the radio's
AT commands:

    +++            enter command mode (1 s of silence before and after)
    ATI / ATI5     version / all local settings
    RTI / RTI5     the same for the remote (air) radio, over the air
    RTS3=n, RT&W   set + save the remote's NETID     (remote first: if the
    ATS3=n, AT&W   set + save the local NETID          air radio is unreachable
    RTZ, ATZ       reboot both                          nothing is changed)

The port must not be in use: the bridge pauses that link while it runs.
"""
from __future__ import annotations

import re
import time

GUARD_S = 1.2  # SiK needs ~1 s of silence around "+++"


class RadioError(Exception):
    pass


def _read(ser, timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    buf = b""
    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf += chunk
            deadline = max(deadline, time.time() + 0.15)  # keep reading while data flows
        elif buf:
            break
    return buf.decode("ascii", errors="replace")


def _at(ser, cmd: str, timeout_s: float = 1.5) -> str:
    ser.reset_input_buffer()
    ser.write(cmd.encode("ascii") + b"\r\n")
    out = _read(ser, timeout_s)
    lines = [ln.strip() for ln in out.replace("\r", "\n").split("\n") if ln.strip() and ln.strip() != cmd]
    return "\n".join(lines)


def _command_mode(ser) -> None:
    time.sleep(GUARD_S)
    ser.reset_input_buffer()
    ser.write(b"+++")
    out = _read(ser, GUARD_S + 1.0)
    if "OK" not in out:
        # Already in command mode? An AT probe answers OK.
        if "OK" not in _at(ser, "AT"):
            raise RadioError("The radio did not answer '+++'. Is this a SiK telemetry radio, and is the baud rate 57600?")


def _registers(text: str) -> dict[str, str]:
    """Parse ATI5 output: 'S3:NETID=25' -> {'NETID': '25'}."""
    out = {}
    for m in re.finditer(r"S\d+:\s*([A-Z_]+)\s*=\s*(\S+)", text):
        out[m.group(1)] = m.group(2)
    return out


def _open(port: str, baud: int):
    import serial

    try:
        return serial.Serial(port, baud, timeout=0.2)
    except Exception as exc:
        raise RadioError(f"Cannot open {port}: {exc}") from exc


def read_info(port: str, baud: int = 57600) -> dict:
    ser = _open(port, baud)
    try:
        _command_mode(ser)
        version = _at(ser, "ATI")
        local = _registers(_at(ser, "ATI5", 2.0))
        remote_text = _at(ser, "RTI5", 3.0)
        remote = _registers(remote_text)
        rssi = _at(ser, "ATI7", 1.5)
        return {"port": port, "version": version.splitlines()[0] if version else None, "local": local,
                "remote": remote or None, "remote_reachable": bool(remote), "rssi": rssi or None}
    finally:
        try:
            _at(ser, "ATO")  # back to data mode
        except Exception:
            pass
        ser.close()


def set_netid(port: str, netid: int, baud: int = 57600) -> dict:
    if not 0 <= netid <= 499:
        raise RadioError("NETID must be between 0 and 499")
    ser = _open(port, baud)
    try:
        _command_mode(ser)
        remote = _registers(_at(ser, "RTI5", 3.0))
        if not remote:
            raise RadioError("The air radio did not answer. Power the drone (its radio must be on and linked) and try again; "
                             "nothing was changed.")
        if "OK" not in _at(ser, f"RTS3={netid}", 3.0) or "OK" not in _at(ser, "RT&W", 3.0):
            raise RadioError("The air radio did not accept the new NETID; nothing was changed on the ground radio.")
        if "OK" not in _at(ser, f"ATS3={netid}") or "OK" not in _at(ser, "AT&W"):
            raise RadioError(f"The air radio now uses NETID {netid} but the ground radio did not save it. "
                             f"Retry: the ground radio must also be set to {netid}.")
        _at(ser, "RTZ", 1.0)
        _at(ser, "ATZ", 1.0)
        return {"port": port, "netid": netid, "remote_was": remote.get("NETID")}
    finally:
        ser.close()
