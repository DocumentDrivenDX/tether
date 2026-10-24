"""Pure bounded session metadata transitions, shared by RAM and SQL projections."""

from dataclasses import dataclass, field
from datetime import datetime

from tether.types import JsonValue
from tether.types.execution import Entry, SessionState
from tether.types.storage import IntegrityError, SessionInit, SessionSummary, SpecRef


@dataclass
class Projection:
    state: SessionState
    init: SessionInit
    summary: SessionSummary
    specs: dict[str, SpecRef] = field(default_factory=lambda: dict[str, SpecRef]())

    def metadata(self, entry: Entry) -> None:
        facts, summary = entry.facts, self.summary
        if entry.kind == "message":
            self.summary = summary.model_copy(update={"message_count": summary.message_count + 1})
        elif entry.kind in {"turn_started", "turn_resumed"}:
            self._start(entry)
        elif entry.kind == "turn_ended":
            interrupted = facts.get("stop_reason") == "interrupted"
            status = (
                "interrupted"
                if interrupted
                else "waiting"
                if facts.get("stop_reason") == "confirmation_required"
                else "idle"
            )
            self.summary = summary.model_copy(
                update={
                    "status": status,
                    "resumable": interrupted,
                    "active_turn_id": None,
                }
            )
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


def finish_summary(
    summary: SessionSummary,
    entry: Entry,
    *,
    pending: int,
    effects: int,
) -> SessionSummary:
    return summary.model_copy(
        update={
            "entry_count": entry.index + 1,
            "updated_at": entry.at,
            "pending": bool(pending),
            "unresolved_effect_count": effects,
        }
    )


def new_projection(session_id: str, owner: str, init: SessionInit, at: datetime) -> Projection:
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
    return Projection(state, init, summary)
