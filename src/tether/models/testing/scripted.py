"""Deterministic provider-free responses and explicit physical-attempt fixtures."""

from collections.abc import AsyncIterator, Sequence

from pydantic import JsonValue

from tether.models.attempts import validate_identity
from tether.models.canonical import digest, snapshot
from tether.types import (
    AttemptIdentity,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    RequestTags,
    ResponseReceipt,
    StreamItem,
    TextDelta,
    TokenUsage,
)


class ListAttemptRecorder:
    """Volatile test double; does not claim journal durability or policy enforcement."""

    def __init__(self, tags: RequestTags) -> None:
        self.tags = tags.model_copy(deep=True)
        self.reserved: list[AttemptIdentity] = []
        self.started: list[tuple[AttemptIdentity, str, JsonValue, str]] = []
        self.observed_usage: list[TokenUsage | None] = []
        self.observed: list[tuple[AttemptIdentity, ResponseReceipt, JsonValue | None]] = []

    async def reserve(self) -> AttemptIdentity:
        identity = AttemptIdentity(
            session_id=self.tags.session_id,
            turn_id=self.tags.turn_id,
            model_call_id=self.tags.model_call_id,
            purpose=self.tags.purpose,
            attempt_id=f"fixture-{len(self.reserved) + 1}",
        )
        self.reserved.append(identity)
        return identity

    async def start(
        self,
        identity: AttemptIdentity,
        *,
        request_digest: str,
        wire_request: JsonValue,
        codec: str = "model-port-v1",
    ) -> None:
        if identity not in self.reserved or any(x[0] == identity for x in self.started):
            raise ValueError("invalid_attempt_identity")
        if request_digest != digest(wire_request):
            raise ValueError("request_digest_mismatch")
        self.started.append((identity, request_digest, wire_request, codec))

    async def observe(
        self,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        *,
        wire_response: JsonValue | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        if not any(x[0] == identity for x in self.started):
            raise ValueError("attempt_not_started")
        self.observed.append((identity, receipt, wire_response))
        self.observed_usage.append(usage.model_copy(deep=True) if usage is not None else None)


async def prepare_fixture(request: ModelRequest) -> AttemptIdentity | None:
    if request.attempts is None:
        return None
    identity = await request.attempts.reserve()
    validate_identity(request, identity)
    wire = request.model_dump(mode="json")
    wire["attempt_identity"] = identity.model_dump(mode="json")
    await request.attempts.start(identity, request_digest=digest(wire), wire_request=wire)
    return identity


async def observe_fixture(
    request: ModelRequest, identity: AttemptIdentity | None, response: ModelResponse, provider: str
) -> None:
    if request.attempts is not None and identity is not None:
        receipt = response.receipt or ResponseReceipt(
            provider=provider,
            endpoint="offline",
            schema_profile="model-port-v1",
            response_id=response.request_id,
            request_id_source="none",
        )
        await request.attempts.observe(
            identity, receipt, wire_response=response.model_dump(mode="json"), usage=response.usage
        )


class ScriptedModel:
    provider = "scripted"

    def __init__(self, script: Sequence[StreamItem | Exception]) -> None:
        self._script = tuple(
            item.model_copy(deep=True) if isinstance(item, (ModelResponse, TextDelta)) else item
            for item in script
        )
        self._position = 0
        self.requests: list[ModelRequest] = []

    def _next_step(self) -> tuple[StreamItem | Exception, ...]:
        step: list[StreamItem | Exception] = []
        while self._position < len(self._script):
            item = self._script[self._position]
            self._position += 1
            step.append(item)
            if not isinstance(item, TextDelta):
                return tuple(step)
        raise AssertionError("script_exhausted")

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        self.requests.append(snapshot(request))
        step = self._next_step()
        identity = await prepare_fixture(request)
        for item in step:
            if isinstance(item, Exception):
                if (
                    isinstance(item, ModelUnavailable)
                    and item.receipt is not None
                    and request.attempts is not None
                    and identity is not None
                ):
                    await request.attempts.observe(identity, item.receipt)
                raise item
            if isinstance(item, TextDelta):
                yield item.model_copy(deep=True)
            else:
                response = item.model_copy(deep=True)
                await observe_fixture(request, identity, response, self.provider)
                yield TextDelta(text=response.content)
                yield response
