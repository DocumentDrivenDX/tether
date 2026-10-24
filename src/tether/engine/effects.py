"""Journal-derived unresolved effects; a terminal unknown is still a barrier."""

from collections.abc import Sequence

from tether.types.execution import Entry


def unresolved_invocations(entries: Sequence[Entry]) -> dict[str, Entry]:
    intents: dict[str, Entry] = {}
    for entry in entries:
        inv = str(entry.facts.get("invocation_id", ""))
        if entry.kind == "resolved" and entry.facts.get("outcome") in {
            "completed",
            "not_completed",
        }:
            intents.pop(inv, None)
        if entry.kind == "tool_call" and entry.facts.get("effect") == "effectful":
            if entry.facts.get("status") == "started":
                intents[inv] = entry
            elif entry.facts.get("status") in {"ok", "withheld"}:
                intents.pop(inv, None)
    return intents


def unresolved_effects(entries: Sequence[Entry]) -> bool:
    return bool(unresolved_invocations(entries))
