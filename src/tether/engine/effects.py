"""Journal-derived unresolved effects; a terminal unknown is still a barrier."""

from collections.abc import Sequence

from tether.types.execution import Entry


def unresolved_effects(entries: Sequence[Entry]) -> bool:
    intents = set[str]()
    for entry in entries:
        inv = str(entry.facts.get("invocation_id", ""))
        if entry.kind == "resolved" and entry.facts.get("outcome") in {
            "completed",
            "not_completed",
        }:
            intents.discard(inv)
        if entry.kind == "tool_call" and entry.facts.get("effect") == "effectful":
            if entry.facts.get("status") == "started":
                intents.add(inv)
            elif entry.facts.get("status") in {"ok", "withheld"}:
                intents.discard(inv)
    return bool(intents)
