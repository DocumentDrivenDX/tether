"""Closed, typed accepted-fact registry; content and opaque extensions never pass through."""

from __future__ import annotations

from dataclasses import dataclass

from tether.types import JsonValue
from tether.types.canonical import digest
from tether.types.execution import Entry
from tether.types.telemetry import AuditContext, AuditRecord, TraceIds
from tether.types.usage import UsageLedger

ACTIONS = frozenset(
    {
        "read_session",
        "read_call_payload",
        "call_model",
        "invoke",
        "call_tool",
        "approve",
        "resume",
        "resolve_effect",
        "retrieve",
        "create_session",
        "update_session",
        "close_session",
        "x.tether.archive_session",
        "x.tether.restore_session",
    }
)
REASONS = frozenset(
    {
        "allowed",
        "denied",
        "policy_denied",
        "scope_required",
        "budget_exceeded",
        "policy_unavailable",
        "policy_reason_invalid",
        "invalid_arguments",
        "tool_error",
        "outcome_unknown",
        "withheld",
        "cancelled",
        "interrupted",
        "invalid_response",
        "empty_response",
        "step_limit",
        "model_denied",
        "model_unavailable",
    }
)
ENUMS = {
    "status": frozenset(
        {"prepared", "started", "ok", "error", "denied", "interrupted", "withheld"}
    ),
    "state": frozenset({"requested", "approved", "declined", "expired", "invalid", "superseded"}),
    "purpose": frozenset({"answer", "summary", "correction"}),
    "effect": frozenset({"read_only", "effectful"}),
    "replay": frozenset({"safe", "unsafe"}),
    "stop_reason": frozenset(
        {
            "final",
            "confirmation_required",
            "cancelled",
            "interrupted",
            "step_limit",
            "model_denied",
            "model_unavailable",
        }
    ),
    "finish_reason": frozenset({"stop", "tool_calls", "length", "filtered", "unknown"}),
}
KINDS = {
    "authorization": "authorization",
    "model_call": "model_call",
    "model_attempt": "x.model.prepared",
    "model_receipt": "x.model.observed",
    "tool_call": "tool_call",
    "confirmation": "confirmation",
    "turn_started": "x.tether.turn_started",
    "turn_resumed": "turn_resumed",
    "turn_ended": "turn_ended",
    "session_closed": "session_closed",
    "x.effects.resolved": "x.effects.resolved",
    "x.effects.replayed": "x.effects.replayed",
}
FIELDS = {
    "authorization": ("action", "allowed", "reason", "purpose"),
    "model_call": ("status", "purpose", "finish_reason", "reason"),
    "model_attempt": ("status",),
    "model_receipt": (),
    "tool_call": ("status", "effect", "replay", "reason"),
    "confirmation": ("state",),
    "turn_ended": ("stop_reason", "reason"),
}


def text(value: object) -> str | None:
    return value if type(value) is str and 0 < len(value) <= 256 else None


def code(value: object, values: frozenset[str]) -> str:
    return value if type(value) is str and value in values else "other"


@dataclass(frozen=True)
class Scope:
    owner: str
    session_id: str
    principal: str
    phase_turn_id: str
    spec_id: str | None = None
    spec_version: str | None = None


def _kind(entry: Entry) -> str | None:
    if entry.kind == "session" and entry.facts.get("event") == "closed":
        return "session_closed"
    if entry.kind != "message":
        return KINDS.get(entry.kind)
    role = entry.facts.get("role")
    return (
        {"user": "user_message", "assistant": "assistant_message"}.get(role)
        if isinstance(role, str)
        else None
    )


def _fields(entry: Entry) -> dict[str, JsonValue]:
    detail: dict[str, JsonValue] = {}
    for field in FIELDS.get(entry.kind, ()):
        value = entry.facts.get(field)
        if field == "purpose" and value is None:
            continue
        if field == "allowed":
            if type(value) is bool:
                detail[field] = value
        elif field == "action":
            detail[field] = code(value, ACTIONS)
        elif field == "reason":
            if value is not None:
                detail[field] = code(value, REASONS)
        else:
            detail[field] = code(value, ENUMS[field])
    return detail


def _links(entry: Entry) -> dict[str, JsonValue]:
    facts = entry.facts
    detail: dict[str, JsonValue] = {}
    if entry.kind == "authorization":
        resource = facts.get("resource")
        if type(resource) is str:
            detail["resource_digest"] = digest(resource)
    if entry.kind == "confirmation":
        binding = facts.get("binding")
        if isinstance(binding, dict) and text(binding.get("invocation_id")) is not None:
            detail["invocation_id"] = text(binding.get("invocation_id"))
    if entry.kind in {"tool_call", "x.effects.resolved", "x.effects.replayed"}:
        for field in ("invocation_id", "args_digest", "tool_digest", "spec_digest", "tool"):
            if text(facts.get(field)) is not None:
                detail[field] = text(facts.get(field))
    return detail


def _usage(entry: Entry, ledger: UsageLedger) -> dict[str, JsonValue]:
    identity = entry.facts.get("identity")
    metadata = identity if isinstance(identity, dict) else entry.facts
    call = text(metadata.get("model_call_id"))
    attempt = text(metadata.get("attempt_id"))
    detail: dict[str, JsonValue] = {
        "purpose": code(metadata.get("purpose", "answer"), ENUMS["purpose"]),
    }
    if call is None:
        return detail
    detail["model_call_id"] = call
    usage, complete = ledger.call_usage(call)
    if attempt is not None:
        detail["attempt_id"] = attempt
        key = "attempt:" + attempt
        usage = ledger.values.get(key)
        complete = key in ledger.values and usage is not None
    detail["usage_complete"] = complete
    detail["usage"] = usage.model_dump(mode="json") if usage is not None else None
    return detail


def record(
    entry: Entry, scope: Scope, ledger: UsageLedger, ids: TraceIds | None
) -> AuditRecord | None:
    kind = _kind(entry)
    if kind is None:
        return None
    detail: dict[str, JsonValue] = {
        "entry_position": entry.index,
        "source_turn_id": entry.turn_id,
        "phase_turn_id": scope.phase_turn_id,
        **_fields(entry),
        **_links(entry),
    }
    if entry.kind in {"model_call", "model_attempt", "model_receipt"}:
        detail.update(_usage(entry, ledger))
    return AuditRecord(
        context=AuditContext(
            actor=scope.owner,
            principal=scope.principal,
            session_id=scope.session_id,
            event_id=digest(["tether.audit.v1", scope.owner, scope.session_id, entry.index, kind]),
            trace_id=ids.trace_id if ids is not None else None,
            span_id=ids.span_id if ids is not None else None,
            spec_id=scope.spec_id,
            spec_version=scope.spec_version,
        ),
        kind=kind,
        detail=detail,
    )
