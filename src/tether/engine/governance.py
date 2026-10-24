"""Current policy decisions and metadata-only append helpers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from tether.types import Message, Purpose
from tether.types.canonical import digest
from tether.types.execution import (
    Actor,
    AuthzRequest,
    Clock,
    Decision,
    Entry,
    ExecutionDenied,
    ExecutionLimits,
    MessageHistory,
    NewEntry,
    Policy,
    TurnLog,
    copy_actor,
)
from tether.types.history import HistoryRecord
from tether.types.usage import UsageLedger

from .observers.facade import Observers
from .observers.metadata import ACTIONS, Scope, code


def reason_code(value: str, fallback: str = "policy_reason_invalid") -> str:
    return value if re.fullmatch(r"[a-z][a-z0-9_.]{0,95}", value) else fallback


def utc_now() -> datetime:
    return datetime.now(UTC)


class AllowAll:
    """Explicit permissive dummy; ownership and engine invariants still apply."""

    async def authorize(self, request: AuthzRequest) -> Decision:
        return Decision(allowed=True, reason="allowed")


class PolicyDenied(ExecutionDenied):
    """Only a known accepted negative policy decision, never an append failure."""


class Governance:
    def __init__(
        self,
        actor: Actor,
        log: TurnLog,
        history: MessageHistory,
        policy: Policy,
        turn_id: str,
        limits: ExecutionLimits,
        clock: Clock = utc_now,
        *,
        observers: Observers | None = None,
    ) -> None:
        self.actor, self.log, self.history, self.policy = actor, log, history, policy
        self.turn_id, self.limits, self.clock = turn_id, limits, clock
        self.observers = observers if observers is not None else Observers()
        self.spec_id: str | None = None
        self.spec_version: str | None = None
        self.configure_usage(frozenset({turn_id}))
        self.policy_reasons = frozenset(
            {
                "allowed",
                "denied",
                "policy_denied",
                "scope_required",
                "budget_exceeded",
                "policy_unavailable",
            }
        )

    def configure_usage(self, turn_ids: frozenset[str]) -> None:
        self.usage_turns = turn_ids
        self.accounting = UsageLedger()
        for entry in self.log.entries:
            if entry.turn_id in turn_ids:
                self.accounting.apply(entry)
        self.usage = self.accounting.snapshot()

    @property
    def scope(self) -> Scope:
        return Scope(
            self.log.session.owner,
            self.log.session.id,
            self.actor.principal or self.actor.id,
            self.log.turn_id or self.turn_id,
            self.spec_id,
            self.spec_version,
        )

    async def fact(
        self,
        kind: str,
        *,
        key: str | None = None,
        source_turn_id: str | None = None,
        **facts: object,
    ) -> Entry:
        entry = NewEntry.model_validate(
            {"kind": kind, "turn_id": source_turn_id or self.turn_id, "key": key, "facts": facts}
        )
        return (await self.commit([entry]))[0]

    async def commit(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        accepted = await self.log.append(entries)
        if len(accepted) != len(entries):
            raise ExecutionDenied("commit_not_accepted")
        for expected, actual in zip(entries, accepted, strict=True):
            value = NewEntry.model_validate(actual.model_dump(exclude={"index", "at"}))
            if value != expected or actual not in self.log.entries:
                raise ExecutionDenied("commit_not_accepted")
        for entry in accepted:
            if entry.turn_id in self.usage_turns:
                self.accounting.apply(entry)
        self.usage = self.accounting.snapshot()
        self.observers.accepted(
            self.actor,
            self.log.session,
            self.log.turn_id or self.turn_id,
            entries,
            accepted,
            self.accounting,
            spec_id=self.spec_id,
            spec_version=self.spec_version,
        )
        return accepted

    async def decision(
        self,
        action: str,
        resource: str,
        *,
        purpose: Purpose | None = None,
        requires: frozenset[str] = frozenset(),
        permitted: frozenset[str] | None = None,
    ) -> NewEntry:
        request = AuthzRequest(
            actor=self.actor,
            action=action,
            resource=resource,
            held=self.log.session.held,
            requires=requires,
            permitted=permitted,
            usage=self.usage,
            purpose=(purpose or "answer") if action == "call_model" else None,
        )
        with self.observers.tracer.start_as_current_span(
            "tether.gate", attributes={"tether.action": code(action, ACTIONS)}
        ):
            try:
                async with asyncio.timeout(self.limits.hook_timeout_seconds):
                    isolated = AuthzRequest.model_validate(
                        request.model_dump(mode="python") | {"actor": copy_actor(self.actor)}
                    )
                    decision = await self.policy.authorize(isolated)
                decision = Decision.model_validate(decision)
            except Exception:
                decision = Decision(allowed=False, reason="policy_unavailable")
            allowed = decision.allowed and self.actor.id == self.log.session.owner
            reason = (
                (
                    decision.reason
                    if decision.reason in self.policy_reasons
                    else "policy_reason_invalid"
                )
                if self.actor.id == self.log.session.owner
                else "denied"
            )
            entry = NewEntry(
                kind="authorization",
                turn_id=self.turn_id,
                facts={
                    "action": action,
                    "resource": resource,
                    "allowed": allowed,
                    "reason": reason,
                    "actor_id": self.actor.id,
                    **({"purpose": request.purpose} if request.purpose is not None else {}),
                },
            )

            self.observers.remember(entry)
            return entry

    async def authorize(
        self,
        action: str,
        resource: str,
        *,
        purpose: Purpose | None = None,
        requires: frozenset[str] = frozenset(),
        permitted: frozenset[str] | None = None,
    ) -> Entry:
        entry = await self.decision(
            action, resource, purpose=purpose, requires=requires, permitted=permitted
        )
        accepted = (await self.commit([entry]))[0]
        if not entry.facts["allowed"]:
            raise PolicyDenied(str(entry.facts["reason"]))
        return accepted

    async def messages(self) -> tuple[Message, ...]:
        return tuple(
            [await self.read(e.history_ref) for e in self.log.entries if e.kind == "message"]
        )

    async def read(self, ref: object) -> Message:
        from tether.types.execution import HistoryRef

        if not isinstance(ref, HistoryRef):
            raise ExecutionDenied("invalid_source")
        await self.authorize("read_session", self.log.session.id)
        if not any(e.history_ref == ref for e in self.log.entries):
            raise ExecutionDenied("unreachable_source")
        try:
            message = await self.history.read(ref, actor=self.actor, session_id=self.log.session.id)
        except (ValueError, PermissionError):
            raise ExecutionDenied("history_unavailable") from None
        payload = (
            HistoryRecord(kind="message", message=message).model_dump(mode="json")
            if ref.codec == "tether-history-v1"
            else message.model_dump(mode="json")
        )
        if digest(payload) != ref.sha256:
            raise ExecutionDenied("source_integrity")
        return message.model_copy(deep=True)
