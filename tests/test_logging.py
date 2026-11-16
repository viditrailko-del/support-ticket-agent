"""Structured logging formatters (same module as the sister projects)."""

import json
import logging
import sys
import uuid
from decimal import Decimal

from app.logging_setup import JsonFormatter, TextFormatter


def _record(event="ticket_finished", **fields) -> logging.LogRecord:
    record = logging.getLogger("test").makeRecord("test", logging.INFO, __file__, 1, event, (), None)
    record.fields = fields
    return record


def test_json_formatter_one_object_per_line():
    ticket_id = uuid.uuid4()
    line = JsonFormatter().format(_record(ticket_id=ticket_id, amount=Decimal("19.99"), flags=["a"]))
    entry = json.loads(line)
    assert "\n" not in line
    assert entry["event"] == "ticket_finished" and entry["level"] == "info"
    assert entry["ticket_id"] == str(ticket_id)
    assert entry["amount"] == "19.99"  # Decimal stays exact (a string), never a float
    assert entry["flags"] == ["a"]


def test_text_formatter_key_values():
    assert TextFormatter().format(_record(decision="needs_human", steps=3)).endswith(
        "ticket_finished decision=needs_human steps=3")


def test_exception_is_included():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.getLogger("test").makeRecord("test", logging.ERROR, __file__, 1, "x", (),
                                                      exc_info=sys.exc_info())
    assert "RuntimeError: boom" in json.loads(JsonFormatter().format(record))["exception"]
