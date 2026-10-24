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
)
from tether.types.canonical import digest
from tether.types.execution import ExecutionDenied, HistoryRef
from tether.types.history import HistoryProvider, HistoryRecord

from .governance import Governance


class JournalAttemptRecorder:
    def __init__(
        self,
        gov: Governance,
        tags: RequestTags,
        tier: str,
        history: HistoryProvider | None = None,
        recheck: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        self.gov, self.tags, self.tier, self.history = gov, tags, tier, history
        self.recheck = recheck
        self.latest_exchange: HistoryRef | None = None
        self.baseline_usage_complete = gov.usage.usage_complete
        self.reserved: dict[str, AttemptIdentity] = {}
        self.started: dict[str, tuple[AttemptIdentity, JsonValue, str]] = {}

    async def reserve(self) -> AttemptIdentity:
        if self.tags.session_id != self.gov.log.session.id or self.tags.principal != (
            self.gov.actor.principal or self.gov.actor.id
        ):
            raise ModelAttemptDenied("invalid_attempt_identity")
        identity = AttemptIdentity(
            session_id=self.tags.session_id,
            turn_id=self.tags.turn_id,
            model_call_id=self.tags.model_call_id,
            attempt_id=str(uuid4()),
            purpose=self.tags.purpose,
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
            await self.gov.authorize("call_model", "model:" + self.tier)
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
        self.started[identity.attempt_id] = (identity, wire_request, codec)
        self.gov.usage = self.gov.usage.model_copy(update={"usage_complete": False})

    async def observe(
        self,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        *,
        wire_response: JsonValue | None = None,
    ) -> None:
        value = self.started.get(identity.attempt_id)
        if value is None or value[0] != identity:
            raise ModelAttemptDenied("invalid_attempt_identity")
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
            "receipt": receipt.model_dump(mode="json"),
            "response_digest": digest(wire_response) if wire_response is not None else None,
        }
        await self.gov.fact(
            "model_receipt",
            key=f"receipt:{identity.attempt_id}:{digest(observation)}",
            identity=identity.model_dump(mode="json"),
            receipt=receipt.model_dump(mode="json"),
            history_ref=ref.model_dump(mode="json") if ref else None,
        )
