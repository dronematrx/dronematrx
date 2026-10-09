"""Secrets must never reach the logs (Sprint 4 security requirement)."""
import logging

from app.core.logging import REDACTED_LOGGERS, RedactSecretsFilter, install_redaction, redact

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


def test_redact_query_tokens():
    line = f'("127.0.0.1", 5000) - "WebSocket /ws/telemetry?token={JWT}" [accepted]'
    out = redact(line)
    assert JWT not in out
    assert "/ws/telemetry?token=[REDACTED]" in out
    assert redact(f"/x?a=1&access_token={JWT}&b=2") == "/x?a=1&access_token=[REDACTED]&b=2"
    assert redact("GET /drones?status=active") == "GET /drones?status=active"


def test_filter_rewrites_formatted_uvicorn_record():
    # uvicorn logs the handshake with %-style args; the formatted message is redacted.
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                               '%s - "WebSocket %s" [accepted]', ("('127.0.0.1', 1)", f"/ws/telemetry?token={JWT}"), None)
    assert RedactSecretsFilter().filter(record) is True
    assert JWT not in record.getMessage()


def test_install_is_idempotent_and_covers_uvicorn(caplog):
    install_redaction()
    install_redaction()
    for name in REDACTED_LOGGERS:
        assert sum(isinstance(f, RedactSecretsFilter) for f in logging.getLogger(name).filters) == 1
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        logging.getLogger("uvicorn.error").info('%s - "WebSocket %s"', "client", f"/ws/telemetry?token={JWT}")
    assert JWT not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_uvicorn_access_formatter_still_works_after_redaction():
    from uvicorn.logging import AccessFormatter

    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:1", "GET", f"/health?token={JWT}", "1.1", 200), None)
    RedactSecretsFilter().filter(record)
    out = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert JWT not in out and "token=[REDACTED]" in out and "200" in out
