"""Journal-only indexes. Rebuilding these must reproduce every committed projection."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from tether.types import JsonValue, TokenUsage
from tether.types.canonical import digest
from tether.types.execution import (
    ApprovalBinding,
    Entry,
    EntryConflict,
    NewEntry,
    PendingConfirmation,
    SessionState,
)
from tether.types.storage import IntegrityError, SessionInit, SessionSummary, SessionView, SpecRef


@dataclass
class SessionIndex:
    state: SessionState
    init: SessionInit
    summary: SessionSummary
    entries: list[Entry] = field(default_factory=lambda: list[Entry]())
    keys: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    pending: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    effects: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    sources: dict[str, Entry] = field(default_factory=lambda: dict[str, Entry]())
    lookups: dict[tuple[str, str], tuple[Entry, ...]] = field(
        default_factory=lambda: dict[tuple[str, str], tuple[Entry, ...]]()
    )
    specs: dict[str, SpecRef] = field(default_factory=lambda: dict[str, SpecRef]())
    counted: dict[str, TokenUsage | None] = field(
        default_factory=lambda: dict[str, TokenUsage | None]()
    )

    def clone(self) -> SessionIndex:
        return replace(
            self,
            entries=self.entries.copy(),
            keys=self.keys.copy(),
            pending=self.pending.copy(),
            effects=self.effects.copy(),
            sources=self.sources.copy(),
            counted=self.counted.copy(),
            lookups=self.lookups.copy(),
            specs=self.specs.copy(),
        )

    def append(self, values: tuple[NewEntry, ...], at: datetime) -> tuple[Entry, ...]:
        accepted: list[Entry] = []
        for value in values:
            old = self.keys.get(value.key) if value.key is not None else None
            if old is not None:
                if NewEntry.model_validate(old.model_dump(exclude={"index", "at"})) != value:
                    raise EntryConflict("entry_conflict")
                accepted.append(old)
                continue
            entry = Entry(**value.model_dump(), index=len(self.entries), at=at)
            self.apply(entry)
            accepted.append(entry)
        return tuple(accepted)

    def apply(self, entry: Entry) -> None:
        if entry.index != len(self.entries) or (entry.key is not None and entry.key in self.keys):
            raise IntegrityError("entry_order_invalid")
        self.entries.append(entry)
        if entry.key is not None:
            self.keys[entry.key] = entry
        for manifest in entry.call_manifest:
            self.sources[digest([self.state.id, entry.index, manifest.position])] = entry
        self._index(entry)
        self._facts(entry)
        self.summary = self.summary.model_copy(
            update={
                "entry_count": len(self.entries),
                "updated_at": entry.at,
                "pending": bool(self.pending),
                "unresolved_effect_count": len(self.effects),
            }
        )

    def _index(self, entry: Entry) -> None:
        groups: set[tuple[str, str]] = set()
        if entry.turn_id is not None:
            groups.add(("turn", entry.turn_id))
        identity = entry.facts.get("identity")
        call = (
            identity.get("model_call_id")
            if isinstance(identity, dict)
            else entry.facts.get("model_call_id")
        )
        if isinstance(call, str) and entry.kind in {"model_attempt", "model_receipt", "model_call"}:
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
            ("invocation", digest([self.state.id, entry.index, m.position]))
            for m in entry.call_manifest
        )
        for group in groups:
            self.lookups[group] = (*self.lookups.get(group, ()), entry)

    def _facts(self, entry: Entry) -> None:
        facts, summary = entry.facts, self.summary
        if entry.kind == "message":
            self.summary = summary.model_copy(update={"message_count": summary.message_count + 1})
        elif entry.kind in {"turn_started", "turn_resumed"}:
            self._start(entry)
        elif entry.kind == "turn_ended":
            stop = facts.get("stop_reason")
            interrupted = stop == "interrupted"
            status = (
                "interrupted"
                if interrupted
                else "waiting"
                if stop == "confirmation_required"
                else "idle"
            )
            self.summary = summary.model_copy(
                update={"status": status, "resumable": interrupted, "active_turn_id": None}
            )
        elif entry.kind == "confirmation":
            self._confirmation(entry)
        elif entry.kind == "tool_call":
            self._effect(entry)
        elif entry.kind == "resolved":
            self._resolution(entry)
        elif entry.kind in {"model_call", "model_attempt"}:
            self._usage(entry)
        elif entry.kind == "settings":
            self._settings(facts)
        elif entry.kind == "session":
            self._session(facts)

    def _start(self, entry: Entry) -> None:
        facts, summary = entry.facts, self.summary
        if entry.kind == "turn_resumed":
            self.summary = summary.model_copy(
                update={
                    "status": "running",
                    "active_turn_id": entry.turn_id,
                    "resumable": False,
                }
            )
            return
        spec_id = facts.get("spec_id")
        if isinstance(spec_id, str):
            self.specs[spec_id] = SpecRef.model_validate(
                {
                    "id": spec_id,
                    "version": facts.get("spec_version"),
                    "digest": facts.get("spec_digest"),
                }
            )
        self.summary = summary.model_copy(
            update={
                "turn_count": summary.turn_count + 1,
                "status": "running",
                "resumable": False,
                "active_turn_id": entry.turn_id,
                "last_turn_at": entry.at,
                "spec_id": facts.get("spec_id") or summary.spec_id,
            }
        )

    def _confirmation(self, entry: Entry) -> None:
        binding = entry.facts.get("binding")
        if isinstance(binding, dict) and isinstance(binding.get("invocation_id"), str):
            inv = str(binding["invocation_id"])
            if entry.facts.get("state") == "requested":
                self.pending[inv] = entry
            else:
                self.pending.pop(inv, None)

    def _resolution(self, entry: Entry) -> None:
        if entry.facts.get("outcome") in {"completed", "not_completed"}:
            self.effects.pop(str(entry.facts.get("invocation_id")), None)

    def _effect(self, entry: Entry) -> None:
        facts = entry.facts
        inv = str(facts.get("invocation_id"))
        if facts.get("status") == "started" and facts.get("effect") == "effectful":
            self.effects[inv] = entry
        elif facts.get("status") in {"ok", "withheld"}:
            self.effects.pop(inv, None)

    def _usage(self, entry: Entry) -> None:
        identity = entry.facts.get("identity")
        attempts = entry.facts.get("attempt_ids")
        if entry.kind == "model_attempt" and isinstance(identity, dict):
            key = "attempt:" + str(identity.get("attempt_id"))
        elif isinstance(attempts, list) and attempts:
            key = "attempt:" + str(attempts[-1])
        else:
            key = "logical:" + str(entry.facts.get("model_call_id", entry.index))
        raw = entry.facts.get("usage")
        usage = TokenUsage.model_validate(raw) if raw is not None else None
        previous = self.counted.get(key)
        if previous is not None and usage is not None and previous != usage:
            raise IntegrityError("usage_changed")
        if previous is None:
            self.counted[key] = usage
        known = [value for value in self.counted.values() if value is not None]
        totals = TokenUsage(
            input_tokens=sum(u.input_tokens for u in known),
            output_tokens=sum(u.output_tokens for u in known),
        )
        self.summary = self.summary.model_copy(
            update={
                "usage": totals,
                "usage_complete": all(value is not None for value in self.counted.values()),
            }
        )

    def _session(self, facts: dict[str, JsonValue]) -> None:
        event, value = facts.get("event"), facts.get("value")
        if event == "closed":
            self.summary = self.summary.model_copy(update={"status": "closed", "resumable": False})
        elif event in {"titled", "tagged"}:
            key = "title" if event == "titled" else "tags"
            self.init = SessionInit.model_validate(self.init.model_dump() | {key: value})
            self.summary = self.summary.model_copy(update={key: getattr(self.init, key)})

    def _settings(self, facts: dict[str, JsonValue]) -> None:
        changes = facts.get("changes")
        if not isinstance(changes, dict):
            raise IntegrityError("settings_invalid")
        allowed = {key: value for key, value in changes.items() if key in {"approval", "held"}}
        self.state = SessionState.model_validate(self.state.model_dump() | allowed)
        self.init = self.init.model_copy(update={"settings": self.init.settings | changes})

    def view(self) -> SessionView:
        pending: PendingConfirmation | None = None
        if self.pending:
            inv, entry = next(iter(self.pending.items()))
            source = self.sources.get(inv)
            if source is None or source.history_ref is None:
                raise IntegrityError("pending_source_missing")
            manifest = next(
                (
                    m
                    for m in source.call_manifest
                    if digest([self.state.id, source.index, m.position]) == inv
                ),
                None,
            )
            if manifest is None:
                raise IntegrityError("pending_source_missing")
            expiry = entry.facts.get("expires_at")
            pending = PendingConfirmation(
                **ApprovalBinding.model_validate(entry.facts.get("binding")).model_dump(),
                tool=manifest.tool,
                arguments=None,
                history_ref=source.history_ref,
                history_status="not_loaded",
                requested_at=entry.at,
                expires_at=datetime.fromisoformat(expiry) if isinstance(expiry, str) else None,
            )
        return SessionView(
            summary=self.summary, settings=self.init.settings, specs=self.specs, pending=pending
        )


def create_index(session_id: str, owner: str, init: SessionInit, at: datetime) -> SessionIndex:
    state = SessionState.model_validate(
        {"id": session_id, "owner": owner}
        | {key: value for key, value in init.settings.items() if key in {"held", "approval"}}
    )
    summary = SessionSummary(
        id=session_id,
        owner=owner,
        ref=init.ref,
        title=init.title,
        tags=init.tags,
        spec_id=init.spec,
        created_at=at,
        updated_at=at,
    )
    return SessionIndex(state=state, init=init, summary=summary)
