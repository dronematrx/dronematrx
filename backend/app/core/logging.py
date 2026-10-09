"""Structured (key=value) event logging.

    log_event(logger, "cbba.converged", mission_id=..., iterations=4)
    -> event=cbba.converged mission_id=... iterations=4

Never pass credentials or tokens as fields.
"""
import logging
import re
from typing import Any


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    text = str(value)
    return f'"{text}"' if " " in text else text


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    if not logger.isEnabledFor(level):
        return
    parts = [f"event={event}"] + [f"{key}={_fmt(val)}" for key, val in fields.items() if val is not None]
    logger.log(level, " ".join(parts))


# The telemetry WebSocket authenticates with ``?token=<JWT>`` (browsers cannot
# set headers on a WebSocket upgrade), and uvicorn logs every handshake/request
# line verbatim. Redact credential-bearing query parameters before any handler
# sees the record so JWTs never reach the console or log files.
_SECRET_QUERY = re.compile(r"(?i)([?&](?:token|access_token|refresh_token|password)=)[^&\s\"']+")


def redact(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1[REDACTED]", text)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Redact args in place, keeping their shape: uvicorn's AccessFormatter
        # unpacks record.args positionally.
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


REDACTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "dronematrx.db")


def install_redaction() -> None:
    """Attach the redaction filter to the loggers that echo request URLs (idempotent)."""
    for name in REDACTED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecretsFilter) for f in logger.filters):
            logger.addFilter(RedactSecretsFilter())
