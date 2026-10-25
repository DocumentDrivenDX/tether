"""Current-policy, fenced intent for every physical model attempt."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from tether.types import (
    AttemptIdentity,
    JsonValue,
    ModelAttemptDenied,
    RequestTags,
    ResponseReceipt,
    TokenUsage,
)
from tether.types.canonical import digest
from tether.types.execution import ExecutionDenied, HistoryRef
from tether.types.history import HistoryProvider, HistoryRecord
from tether.types.usage import usage_change

from .governance import Governance


class JournalAttemptRecorder:
    def __init__(
        self,
        gov: Governance,
        tags: RequestTags,
        tier: str,
        history: HistoryProvider | None = None,
        recheck: Callable[[], Awaitable[object]] | None = None,
        *,
        admit: Callable[[], None] | None = None,
        live_text: Callable[[AttemptIdentity, int, str], Awaitable[None]] | None = None,
    ) -> None:
        self.gov, self.tags, self.tier, self.history = gov, tags, tier, history
        self.recheck, self.admit = recheck, admit
        self.live_text = live_text
        self.latest_exchange: HistoryRef | None = None
        self.reserved: dict[str, AttemptIdentity] = {}
        self.started: dict[str, tuple[AttemptIdentity, JsonValue, str]] = {}
        self.live_offsets: dict[str, int] = {}

    async def reserve(self) -> AttemptIdentity:
        if self.tags.session_id != self.gov.log.session.id or self.tags.principal != (
            self.gov.actor.principal or self.gov.actor.id
        ):
            raise ModelAttemptDenied("invalid_attempt_identity")
        trace = self.gov.observers.tracer.current_ids()
        identity = AttemptIdentity(
            session_id=self.tags.session_id,
            turn_id=self.tags.turn_id,
            model_call_id=self.tags.model_call_id,
            attempt_id=str(uuid4()),
            purpose=self.tags.purpose,
            trace_id=trace.trace_id if trace is not None else None,
            span_id=trace.span_id if trace is not None else None,
        )
        self.reserved[identity.attempt_id] = identity
        return identity

    async def start(
        self,
        identity: AttemptIdentity,
        *,
        request_digest: str,
        wire_request: JsonValue,
        codec: str = "model-port-v1",
    ) -> None:
        if (
            self.reserved.pop(identity.attempt_id, None) != identity
            or identity.attempt_id in self.started
            or digest(wire_request) != request_digest
        ):
            raise ModelAttemptDenied("invalid_attempt_identity")
        try:
            await self.gov.authorize("read_session", self.gov.log.session.id)
            if self.recheck is not None:
                await self.recheck()
            await self.gov.authorize("call_model", "model:" + self.tier, purpose=identity.purpose)
        except ExecutionDenied as error:
            raise ModelAttemptDenied(error.reason) from None
        ref = None
        if self.history is not None:
            record = HistoryRecord(kind="model_exchange", request=wire_request)
            ref = await self.history.put(
                actor=self.gov.actor,
                session_id=identity.session_id,
                key=f"attempt:{identity.attempt_id}:{codec}:{digest(record.model_dump(mode='json'))}",
                record=record,
            )
        await self.gov.fact(
            "model_attempt",
            key="attempt:" + identity.attempt_id,
            identity=identity.model_dump(mode="json"),
            request_digest=request_digest,
            tier=self.tier,
            status="prepared",
            codec=codec,
            history_ref=ref.model_dump(mode="json") if ref else None,
        )
        if self.admit is not None:
            self.admit()
        self.started[identity.attempt_id] = (identity, wire_request, codec)
        self.live_offsets[identity.attempt_id] = 0

    async def disclose_delta(self, identity: AttemptIdentity | None, text: str) -> None:
        """Disclose only text bound to an accepted physical start and current read policy."""
        if not text or self.live_text is None or identity is None:
            return
        value = self.started.get(identity.attempt_id)
        if value is None or value[0] != identity:
            raise ModelAttemptDenied("invalid_attempt_identity")
        try:
            await self.gov.authorize("read_session", self.gov.log.session.id)
        except ExecutionDenied as error:
            raise ModelAttemptDenied(error.reason) from None
        offset = self.live_offsets[identity.attempt_id]
        await self.live_text(identity.model_copy(deep=True), offset, text)
        self.live_offsets[identity.attempt_id] = offset + len(text)

    async def observe(
        self,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        *,
        wire_response: JsonValue | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        value = self.started.get(identity.attempt_id)
        if value is None or value[0] != identity:
            raise ModelAttemptDenied("invalid_attempt_identity")
        key = "attempt:" + identity.attempt_id
        usage_change(
            self.gov.accounting.values.get(key), usage, exists=key in self.gov.accounting.values
        )
        _, request, codec = value
        ref = None
        if self.history is not None:
            record = HistoryRecord(kind="model_exchange", request=request, response=wire_response)
            ref = await self.history.put(
                actor=self.gov.actor,
                session_id=identity.session_id,
                key=f"attempt:{identity.attempt_id}:{codec}:{digest(record.model_dump(mode='json'))}",
                record=record,
            )
        self.latest_exchange = ref
        observation: dict[str, JsonValue] = {
            "usage": usage.model_dump(mode="json") if usage is not None else None,
            "receipt": receipt.model_dump(mode="json"),
            "response_digest": digest(wire_response) if wire_response is not None else None,
        }
        await self.gov.fact(
            "model_receipt",
            key=f"receipt:{identity.attempt_id}:{digest(observation)}",
            identity=identity.model_dump(mode="json"),
            receipt=receipt.model_dump(mode="json"),
            usage=usage.model_dump(mode="json") if usage is not None else None,
            history_ref=ref.model_dump(mode="json") if ref else None,
        )
