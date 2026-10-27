"""Drone topic naming.

The PRD writes topics as `drone/{UIN}/telemetry`. In NATS the token
separator is `.` (wildcards `*`/`>` only work per token), so the same
hierarchy is the subject `drone.{UIN}.telemetry`. `topic()` returns the
PRD form for display; `subject()` the NATS form actually published.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = ("telemetry", "state", "alert", "command", "detection")  # detection: Sprint 6 inference events
UIN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidSubjectError(ValueError):
    pass


def _prefix(prefix: str) -> str:
    return f"{prefix}." if prefix else ""


def validate_uin(uin: str) -> str:
    if not isinstance(uin, str) or not UIN_PATTERN.match(uin):
        raise InvalidSubjectError(f"Invalid UIN for a NATS subject: {uin!r} (allowed: letters, digits, '-', '_')")
    return uin


def subject(uin: str, kind: str, prefix: str = "") -> str:
    if kind not in KINDS:
        raise InvalidSubjectError(f"Unknown message kind {kind!r}; expected one of {KINDS}")
    return f"{_prefix(prefix)}drone.{validate_uin(uin)}.{kind}"


def topic(uin: str, kind: str) -> str:
    return f"drone/{uin}/{kind}"


def stream_subjects(prefix: str = "") -> list[str]:
    return [f"{_prefix(prefix)}drone.*.{kind}" for kind in KINDS]


def kind_filter(kind: str, prefix: str = "") -> str:
    return f"{_prefix(prefix)}drone.*.{kind}"


@dataclass(frozen=True)
class ParsedSubject:
    uin: str
    kind: str


def parse(subject_name: str, prefix: str = "") -> ParsedSubject:
    head = _prefix(prefix)
    if not subject_name.startswith(head + "drone."):
        raise InvalidSubjectError(f"Not a drone subject: {subject_name}")
    parts = subject_name[len(head) :].split(".")
    if len(parts) != 3 or parts[2] not in KINDS:
        raise InvalidSubjectError(f"Malformed drone subject: {subject_name}")
    return ParsedSubject(uin=validate_uin(parts[1]), kind=parts[2])
