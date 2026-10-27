"""Drone MatrX flight-controller binding QR payload, version 1.

The QR code on a flight controller (or printed by mission control for a
discovered one) is a short JSON object prefixed with `DMXFC1:`:

    DMXFC1:{"v":1,"fc":"sitl:bench-1a2b3c:FC-1a2b3c-01","proto":"mavlink2","conn":"sitl","sysid":1,"ref":"FC-1a2b3c-01"}

    v      int     payload version; only 1 is accepted
    fc     str     flight-controller identity (what bindings are keyed on)
    proto  str     "mavlink2"
    conn   str     "sitl" | "udp" | "serial"
    sysid  int     MAVLink system id (optional, 1..255)
    ref    str     non-secret registration reference shown to the operator

It carries no secret. Keys that look like credentials (password, token,
secret, key, private, credential, auth) are rejected, as are unknown keys,
so a payload that would leak a reusable secret can never be accepted.
"""
from __future__ import annotations

import json
import re

PREFIX = "DMXFC1:"
MAX_LEN = 512
ALLOWED_KEYS = {"v", "fc", "proto", "conn", "sysid", "ref"}
FORBIDDEN = re.compile(r"pass|token|secret|key|private|credential|auth|pwd|cert", re.IGNORECASE)
FC_PATTERN = re.compile(r"^(sitl|udp|serial):[A-Za-z0-9_.:/\-]{1,110}$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class QrError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse(text: str) -> dict:
    if not isinstance(text, str) or not text:
        raise QrError("QR_EMPTY", "Empty QR payload")
    if len(text) > MAX_LEN:
        raise QrError("QR_TOO_LONG", f"QR payload longer than {MAX_LEN} characters")
    if not text.startswith(PREFIX):
        m = re.match(r"^DMXFC(\d+):", text)
        if m:
            raise QrError("QR_UNSUPPORTED_VERSION", f"Unsupported binding QR version {m.group(1)}; this server reads version 1")
        raise QrError("QR_NOT_DRONE_MATRX", "Not a Drone MatrX flight-controller QR code")
    try:
        data = json.loads(text[len(PREFIX):])
    except json.JSONDecodeError as exc:
        raise QrError("QR_MALFORMED", f"Malformed QR payload: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise QrError("QR_MALFORMED", "QR payload must be a JSON object")
    secret_keys = sorted(k for k in data if FORBIDDEN.search(str(k)))
    if secret_keys:
        raise QrError("QR_CONTAINS_SECRET", f"QR payload must not contain secrets (rejected fields: {', '.join(secret_keys)})")
    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise QrError("QR_UNKNOWN_FIELDS", f"Unknown QR fields: {', '.join(unknown)}")
    if data.get("v") != 1:
        raise QrError("QR_UNSUPPORTED_VERSION", f"Unsupported binding QR version {data.get('v')!r}; this server reads version 1")
    fc = data.get("fc")
    if not isinstance(fc, str) or not FC_PATTERN.match(fc):
        raise QrError("QR_INVALID_FC", "Field 'fc' must be '<sitl|udp|serial>:<identity>'")
    if data.get("proto") != "mavlink2":
        raise QrError("QR_INVALID_PROTO", "Field 'proto' must be 'mavlink2'")
    conn = data.get("conn")
    if conn not in ("sitl", "udp", "serial") or not fc.startswith(f"{conn}:"):
        raise QrError("QR_INVALID_CONN", "Field 'conn' must match the identity's connection type")
    sysid = data.get("sysid")
    if sysid is not None and (not isinstance(sysid, int) or isinstance(sysid, bool) or not 1 <= sysid <= 255):
        raise QrError("QR_INVALID_SYSID", "Field 'sysid' must be an integer 1..255")
    ref = data.get("ref")
    if ref is not None and (not isinstance(ref, str) or not REF_PATTERN.match(ref)):
        raise QrError("QR_INVALID_REF", "Field 'ref' must be 1-64 characters [A-Za-z0-9_.-]")
    return {"v": 1, "fc": fc, "proto": "mavlink2", "conn": conn, "sysid": sysid, "ref": ref}


def build(fc_identity: str, conn: str, sysid: int | None, ref: str | None) -> str:
    body = {"v": 1, "fc": fc_identity, "proto": "mavlink2", "conn": conn}
    if sysid is not None:
        body["sysid"] = sysid
    if ref:
        body["ref"] = ref
    text = PREFIX + json.dumps(body, separators=(",", ":"))
    parse(text)  # never hand out a payload we would reject
    return text
