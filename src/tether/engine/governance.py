"""Current policy decisions and metadata-only append helpers."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from datetime import UTC, datetime

from tether.types import Message
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
    UsageSoFar,
    copy_actor,
)
from tether.types.history import HistoryRecord


def reason_code(value: str, fallback: str = "policy_reason_invalid") -> str:
    return value if re.fullmatch(r"[a-z][a-z0-9_.]{0,95}", value) else fallback


def utc_now() -> datetime:
    return datetime.now(UTC)


class AllowAll:
    """Explicit permissive dummy; ownership and engine invariants still apply."""

    async def authorize(self, request: AuthzRequest) -> Decision:
        return Decision(allowed=True, reason="allowed")


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
    ) -> None:
        self.actor, self.log, self.history, self.policy = actor, log, history, policy
        self.turn_id, self.limits, self.clock = turn_id, limits, clock
        self.usage = UsageSoFar()
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

    async def fact(self, kind: str, *, key: str | None = None, **facts: object) -> None:
        entry = NewEntry.model_validate(
            {"kind": kind, "turn_id": self.turn_id, "key": key, "facts": facts}
        )
        await self.commit([entry])

    async def commit(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        accepted = await self.log.append(entries)
        if len(accepted) != len(entries):
            raise ExecutionDenied("commit_not_accepted")
        for expected, actual in zip(entries, accepted, strict=True):
            value = NewEntry.model_validate(actual.model_dump(exclude={"index", "at"}))
            if value != expected or actual not in self.log.entries:
                raise ExecutionDenied("commit_not_accepted")
        return accepted

    async def authorize(
        self,
        action: str,
        resource: str,
        *,
        requires: frozenset[str] = frozenset(),
        permitted: frozenset[str] | None = None,
    ) -> None:
        request = AuthzRequest(
            actor=self.actor,
            action=action,
            resource=resource,
            held=self.log.session.held,
            requires=requires,
            permitted=permitted,
            usage=self.usage,
            purpose="answer" if action == "call_model" else None,
        )
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
            (decision.reason if decision.reason in self.policy_reasons else "policy_reason_invalid")
            if self.actor.id == self.log.session.owner
            else "denied"
        )
        await self.fact(
            "authorization",
            action=action,
            resource=resource,
            allowed=allowed,
            reason=reason,
            actor_id=self.actor.id,
        )
        if not allowed:
            raise ExecutionDenied(reason)

    async def read(self, ref: object) -> Message:
        from tether.types.execution import HistoryRef

        if not isinstance(ref, HistoryRef):
            raise ExecutionDenied("invalid_source")
        await self.authorize("read_session", self.log.session.id)
        if not any(e.history_ref == ref for e in self.log.entries):
            raise ExecutionDenied("unreachable_source")
        message = await self.history.read(ref, actor=self.actor, session_id=self.log.session.id)
        payload = (
            HistoryRecord(kind="message", message=message).model_dump(mode="json")
            if ref.codec == "tether-history-v1"
            else message.model_dump(mode="json")
        )
        if digest(payload) != ref.sha256:
            raise ExecutionDenied("source_integrity")
        return message.model_copy(deep=True)
