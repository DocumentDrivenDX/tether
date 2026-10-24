"""Custom attempt recorders retain accounting semantics, never an Engine context bypass."""

from tether.types import (
    AttemptIdentity,
    AttemptRecorder,
    JsonValue,
    RequestTags,
    ResponseReceipt,
    TokenUsage,
)

from ..governance import Governance
from .step import ContextStep


class CheckedRecorder:
    def __init__(
        self, recorder: AttemptRecorder, step: ContextStep, gov: Governance, tags: RequestTags
    ) -> None:
        self.recorder, self.step, self.gov, self.tags = recorder, step, gov, tags

    async def reserve(self) -> AttemptIdentity:
        return await self.recorder.reserve()

    async def start(
        self,
        identity: AttemptIdentity,
        *,
        request_digest: str,
        wire_request: JsonValue,
        codec: str = "model-port-v1",
    ) -> None:
        await self.step.recheck()
        await self.gov.authorize("call_model", "model:" + self.step.tier, purpose=self.tags.purpose)
        await self.recorder.start(
            identity, request_digest=request_digest, wire_request=wire_request, codec=codec
        )
        self.step.check()

    async def observe(
        self,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        *,
        wire_response: JsonValue | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        await self.recorder.observe(identity, receipt, wire_response=wire_response, usage=usage)
