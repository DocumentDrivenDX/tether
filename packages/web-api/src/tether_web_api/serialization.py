"""Tether-value to HTTP-wire serialization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from starlette.responses import JSONResponse

from tether.client import (
    EntriesAppended,
    Lagged,
    ReplyReset,
    ReplyText,
    ResyncRequired,
    SessionRemoved,
    SessionUpdated,
)

from .counters import convert_counters
from .schema import schema_registry
from .wire import (
    EntriesAppendedEvent,
    LaggedEvent,
    ReplyResetEvent,
    ReplyTextEvent,
    ResyncRequiredEvent,
    SessionRemovedEvent,
    SessionUpdatedEvent,
)

_SCHEMAS = schema_registry()
_EVENT_TYPES: dict[type[object], tuple[type[BaseModel], str]] = {
    SessionUpdated: (SessionUpdatedEvent, "session_updated"),
    SessionRemoved: (SessionRemovedEvent, "session_removed"),
    EntriesAppended: (EntriesAppendedEvent, "entries_appended"),
    ReplyText: (ReplyTextEvent, "reply_text"),
    ReplyReset: (ReplyResetEvent, "reply_reset"),
    Lagged: (LaggedEvent, "lagged"),
    ResyncRequired: (ResyncRequiredEvent, "resync_required"),
}


def to_wire_data(value: Any, schema_name: str) -> Any:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return convert_counters(raw, schema_name, _SCHEMAS, to_wire=True)


def model_response(value: Any, schema_name: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(to_wire_data(value, schema_name), status_code=status_code)


def event_data(value: object) -> tuple[str, Mapping[str, Any]]:
    event_details = _EVENT_TYPES.get(type(value))
    if event_details is None or not isinstance(value, BaseModel):
        raise ValueError("unknown_change_variant")
    event_model, event_name = event_details
    raw = value.model_dump(mode="python")
    raw["type"] = event_name
    wire = event_model.model_validate(raw)
    return event_name, to_wire_data(wire, "Change")
