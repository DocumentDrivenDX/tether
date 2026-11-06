"""Public-result validators for managed PostgreSQL process controls."""

from __future__ import annotations

from tether.client import Entry, RecoveredTurn


def fencing_projection_valid(entries: tuple[Entry, ...], original_turn: str) -> bool:
    ended = [entry for entry in entries if entry.kind == "turn_ended"]
    return (
        [(entry.turn_id, entry.facts.get("stop_reason")) for entry in ended]
        == [(original_turn, "interrupted")]
        and sum(entry.key == "stale-a" for entry in entries) == 0
        and sum(entry.key == "current-b" for entry in entries) == 1
    )


def unknown_intent_valid(entries: tuple[Entry, ...], turn_id: str) -> bool:
    invocation_id = "qualification-unknown"
    matching_calls = [
        entry
        for entry in entries
        if entry.kind == "tool_call" and entry.facts.get("invocation_id") == invocation_id
    ]
    intents = [
        entry
        for entry in matching_calls
        if entry.turn_id == turn_id
        and entry.facts.get("effect") == "effectful"
        and entry.facts.get("status") == "started"
    ]
    resolved = any(
        entry.facts.get("invocation_id") == invocation_id
        and entry.kind in {"resolved", "tool_result"}
        for entry in entries
    )
    results = any(
        entry.key in {f"result:{invocation_id}", f"message:result:{invocation_id}"}
        or (
            entry.facts.get("invocation_id") == invocation_id
            and entry.kind not in {"tool_call", "turn_started"}
        )
        for entry in entries
    )
    return len(intents) == len(matching_calls) == 1 and not resolved and not results


def recovered_exactly(recovered: tuple[RecoveredTurn, ...], session_id: str, turn_id: str) -> bool:
    return [(item.session_id, item.turn_id) for item in recovered] == [(session_id, turn_id)]


def lease_refusal(event: dict[str, object], command: str) -> bool:
    return (
        event.get("event") == "refused"
        and event.get("command") == command
        and event.get("reason") == "lease_lost"
    )
