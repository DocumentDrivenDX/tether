"""Pure source identities, lookup groups and pending metadata from committed facts."""

from datetime import datetime

from tether.types.canonical import digest
from tether.types.execution import ApprovalBinding, Entry, PendingConfirmation
from tether.types.storage import IntegrityError


def entry_groups(session_id: str, entry: Entry) -> set[tuple[str, str]]:
    groups: set[tuple[str, str]] = set()
    if entry.turn_id is not None:
        groups.add(("turn", entry.turn_id))
    identity = entry.facts.get("identity")
    call = (
        identity.get("model_call_id")
        if isinstance(identity, dict)
        else entry.facts.get("model_call_id")
    )
    if isinstance(call, str) and entry.kind in {
        "model_attempt",
        "model_receipt",
        "model_call",
        "context_preparation",
        "context_use",
        "compaction",
    }:
        groups.add(("model_call", call))
    binding = entry.facts.get("binding")
    inv = (
        binding.get("invocation_id")
        if isinstance(binding, dict)
        else entry.facts.get("invocation_id")
    )
    if isinstance(inv, str) and entry.kind in {"confirmation", "tool_call", "resolved"}:
        groups.add(("invocation", inv))
    groups.update(
        ("invocation", digest([session_id, entry.index, m.position])) for m in entry.call_manifest
    )
    return groups


def pending_confirmation(
    session_id: str,
    inv: str,
    entry: Entry,
    source: Entry | None,
) -> PendingConfirmation:
    if source is None or source.history_ref is None:
        raise IntegrityError("pending_source_missing")
    manifest = next(
        (m for m in source.call_manifest if digest([session_id, source.index, m.position]) == inv),
        None,
    )
    if manifest is None:
        raise IntegrityError("pending_source_missing")
    expiry = entry.facts.get("expires_at")
    return PendingConfirmation(
        **ApprovalBinding.model_validate(entry.facts.get("binding")).model_dump(),
        tool=manifest.tool,
        arguments=None,
        history_ref=source.history_ref,
        history_status="not_loaded",
        requested_at=entry.at,
        expires_at=datetime.fromisoformat(expiry) if isinstance(expiry, str) else None,
    )


def ledger_change(entry: Entry) -> tuple[str, str, bool] | None:
    facts = entry.facts
    if entry.kind == "confirmation":
        binding = facts.get("binding")
        if isinstance(binding, dict) and isinstance(binding.get("invocation_id"), str):
            return "pending", str(binding["invocation_id"]), facts.get("state") == "requested"
    elif entry.kind == "tool_call":
        inv = str(facts.get("invocation_id"))
        if facts.get("status") == "started" and facts.get("effect") == "effectful":
            return "effects", inv, True
        if facts.get("status") in {"ok", "withheld"}:
            return "effects", inv, False
    elif entry.kind == "resolved" and facts.get("outcome") in {"completed", "not_completed"}:
        return "effects", str(facts.get("invocation_id")), False
    return None
