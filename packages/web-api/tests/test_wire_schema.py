from pathlib import Path

import pytest
from tether_web_api.counters import CounterValueError, convert_counters
from tether_web_api.schema import assert_schema_matches, schema_registry
from tether_web_api.serialization import event_data

from tether.client import ReplyText


def test_counter_conversion_is_field_directed_and_preserves_payload_numbers() -> None:
    schemas = schema_registry()
    payload = {
        "facts": {"customer_count": 7},
        "settings": {"threshold": 1.25},
        "entry_count": 9,
        "last_seq": 2**63 + 1,
        "usage": {
            "input_tokens": 2**63 + 3,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        },
    }
    wire = convert_counters(payload, "SessionSummary", schemas, to_wire=True)
    assert wire["entry_count"] == "9"
    assert wire["last_seq"] == "9223372036854775809"
    assert wire["usage"]["input_tokens"] == "9223372036854775811"
    assert wire["facts"]["customer_count"] == 7
    assert wire["settings"]["threshold"] == 1.25
    assert convert_counters(wire, "SessionSummary", schemas, to_wire=False) == payload


def test_empty_snapshot_signed_index_and_optional_cursor() -> None:
    schemas = schema_registry()
    index_schema = schemas["$defs"]["SessionSnapshot"]["properties"]["through_index"]
    assert index_schema["pattern"] == "^(0|[1-9][0-9]*)$|^-1$"
    value = {"view": {}, "through_index": -1, "seq": 0}
    assert convert_counters(value, "SessionSnapshot", schemas, to_wire=True) == {
        "view": {},
        "through_index": "-1",
        "seq": "0",
    }
    assert (
        convert_counters(
            {"items": [], "next_start": None, "partial": False},
            "EntryPage",
            schemas,
            to_wire=True,
        )["next_start"]
        is None
    )
    with pytest.raises(CounterValueError, match="counter_out_of_range"):
        convert_counters(
            {"view": {}, "through_index": "-2", "seq": "1"},
            "SessionSnapshot",
            schemas,
            to_wire=False,
        )


def test_change_schema_is_discriminated_and_provisional_text_has_no_commit_id() -> None:
    definitions = schema_registry()["$defs"]
    schema = definitions["Change"]
    assert schema["discriminator"]["propertyName"] == "type"
    assert all(
        "type" in definitions[ref["$ref"].rsplit("/", 1)[1]]["required"] for ref in schema["oneOf"]
    )
    name, data = event_data(
        ReplyText(
            session_id="session-1", turn_id="turn-1", attempt_id="attempt-1", offset=12, text="x"
        )
    )
    assert name == "reply_text"
    assert data["type"] == "reply_text"
    assert data["offset"] == "12"


def test_committed_schema_is_current() -> None:
    assert_schema_matches(Path(__file__).parents[1] / "schema/http-v1.json")
