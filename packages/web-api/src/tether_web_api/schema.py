"""Deterministic JSON Schema registry for the public HTTP v1 wire contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter

from tether.client import (
    AttachmentRef,
    CallDetail,
    EffectPage,
    EntryPage,
    HistoryPage,
    HistoryValue,
    Message,
    Page,
    PendingConfirmation,
    SessionQuery,
    SessionSnapshot,
    SessionSummary,
    SessionView,
    TokenUsage,
    TurnResult,
)

from .wire import (
    AcceptedTurn,
    CallDetailRequest,
    ChangeEvent,
    ClientCapabilities,
    ConfirmRequest,
    ConfirmResponse,
    CreateSessionRequest,
    HistoryLinkRequest,
    ProblemDetails,
    ResolveRequest,
    SendRequest,
    UpdateSessionRequest,
)


class SchemaDefinitionError(ValueError):
    """A schema export cannot be generated consistently."""


_COUNTER_FIELDS = {
    "SessionSummary": {
        "entry_count",
        "message_count",
        "turn_count",
        "unresolved_effect_count",
        "last_seq",
    },
    "TokenUsage": {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    },
    "UsageSoFar": {"steps", "input_tokens", "output_tokens", "model_calls", "summary_calls"},
    "CallManifest": {"position"},
    "Entry": {"index"},
    "AttachmentRef": {"size_bytes"},
    "EffectInfo": {"intent_index"},
    "SessionSnapshot": {"through_index", "seq"},
    "EntryPage": {"next_start"},
    "EffectPage": {"next_start"},
    "HistoryPage": {"next_start"},
    "HistorySlot": {"entry_index"},
    "PersistedChange": {"seq"},
    "SessionUpdated": {"seq"},
    "SessionUpdatedEvent": {"seq"},
    "SessionRemoved": {"seq"},
    "SessionRemovedEvent": {"seq"},
    "EntriesAppended": {"seq"},
    "EntriesAppendedEvent": {"seq"},
    "ReplyText": {"offset"},
    "ReplyTextEvent": {"offset"},
    "Lagged": {"missed"},
    "LaggedEvent": {"missed"},
}
_SIGNED_COUNTERS = {"SessionSnapshot": {"through_index"}}


def _mark_counter_fields(schema: dict[str, Any]) -> None:
    definitions = cast(dict[str, Any], schema.get("$defs", {}))
    for raw_definition in definitions.values():
        definition = cast(dict[str, Any], raw_definition)
        model_name = definition.get("title")
        if not isinstance(model_name, str):
            continue
        counter_names = _COUNTER_FIELDS.get(model_name, set())
        properties = cast(dict[str, Any], definition.get("properties", {}))
        for name in counter_names:
            prop = cast(dict[str, Any] | None, properties.get(name))
            if not isinstance(prop, dict):
                continue
            candidates = cast(list[Any], prop.get("anyOf", [prop]))
            for raw_item in candidates:
                item = cast(dict[str, Any], raw_item)
                if item.get("type") == "integer":
                    signed = name in _SIGNED_COUNTERS.get(model_name, set())
                    item.update(
                        type="string",
                        pattern="^(0|[1-9][0-9]*)$|^-1$" if signed else "^(0|[1-9][0-9]*)$",
                        format="tether-counter",
                        **{"x-tether-counter": True},
                    )
                    if signed:
                        item["x-tether-counter-signed"] = True
            prop.pop("default", None)


def _collect(adapter: TypeAdapter[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    document = adapter.json_schema(ref_template="#/$defs/{model}")
    children = cast(dict[str, Any], document.pop("$defs", {}))
    return document, children


def schema_registry() -> dict[str, Any]:
    """Return named request, response, value, and event schemas."""
    schemas: dict[str, Any] = {
        "AcceptedTurn": TypeAdapter(AcceptedTurn),
        "AttachmentRef": TypeAdapter(AttachmentRef),
        "CallDetail": TypeAdapter(CallDetail),
        "CallDetailRequest": TypeAdapter(CallDetailRequest),
        "Change": TypeAdapter(ChangeEvent),
        "ClientCapabilities": TypeAdapter(ClientCapabilities),
        "ConfirmRequest": TypeAdapter(ConfirmRequest),
        "ConfirmResponse": TypeAdapter(ConfirmResponse),
        "CreateSessionRequest": TypeAdapter(CreateSessionRequest),
        "EffectPage": TypeAdapter(EffectPage),
        "EntryPage": TypeAdapter(EntryPage),
        "HistoryLinkRequest": TypeAdapter(HistoryLinkRequest),
        "HistoryPage": TypeAdapter(HistoryPage),
        "HistoryValue": TypeAdapter(HistoryValue),
        "Message": TypeAdapter(Message),
        "PageSessionSummary": TypeAdapter(Page[SessionSummary]),
        "PendingConfirmation": TypeAdapter(PendingConfirmation),
        "ProblemDetails": TypeAdapter(ProblemDetails),
        "ResolveRequest": TypeAdapter(ResolveRequest),
        "SendRequest": TypeAdapter(SendRequest),
        "SessionQuery": TypeAdapter(SessionQuery),
        "SessionSnapshot": TypeAdapter(SessionSnapshot),
        "SessionSummary": TypeAdapter(SessionSummary),
        "SessionView": TypeAdapter(SessionView),
        "TokenUsage": TypeAdapter(TokenUsage),
        "TurnResult": TypeAdapter(TurnResult),
        "UpdateSessionRequest": TypeAdapter(UpdateSessionRequest),
    }
    definitions: dict[str, Any] = {}
    roots: dict[str, Any] = {}
    for name, adapter in schemas.items():
        root, children = _collect(adapter)
        for child_name, child in children.items():
            prior = definitions.get(child_name)
            if prior is not None and prior != child:
                raise SchemaDefinitionError(f"conflicting_schema_definition:{child_name}")
            definitions[child_name] = child
        roots[name] = root
    definitions.update(roots)
    registry = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://tether.invalid/schemas/http-v1.json",
        "title": "Tether HTTP v1 schema registry",
        "$defs": definitions,
    }
    _mark_counter_fields(registry)
    return registry


def schema_json() -> str:
    return json.dumps(schema_registry(), indent=2, sort_keys=True) + "\n"


def write_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(schema_json(), encoding="utf-8")


def assert_schema_matches(path: Path) -> None:
    if path.read_text(encoding="utf-8") != schema_json():
        raise SchemaDefinitionError(f"stale_generated_schema:{path}")


def main() -> int:
    """Regenerate or check the checked-in schema without network access."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate or check Tether HTTP v1 schemas")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path, help="write generated schema to this path")
    group.add_argument(
        "--check",
        nargs="?",
        const=Path(__file__).parents[2] / "schema/http-v1.json",
        type=Path,
        help="check generated schema at the given path (default: repository schema/http-v1.json)",
    )
    args = parser.parse_args()
    if args.output:
        write_schema(args.output)
    else:
        assert_schema_matches(args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
