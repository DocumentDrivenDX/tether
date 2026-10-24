"""Real native SDK + Pydantic model over a fabricated HTTPX transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx2 as httpx
import pytest

from tether.adapters.openai_compat import OpenAICompatModel
from tether.models import complete
from tether.models.canonical import digest
from tether.models.testing import ListAttemptRecorder
from tether.runtime.pydantic_ai.bridge import run_agent
from tether.types import (
    ModelExecutionLimits,
    ModelRequest,
    ModelUnavailable,
    TokenUsage,
    ToolSpec,
)


async def authorize_history() -> None:
    pass


def wire_response(
    *, finish: str | None = "stop", calls: list | None = None, usage: dict | None = None
) -> dict:
    return dict(
        id="fabricated-body-id",
        object="chat.completion",
        created=1,
        model="fabricated-wire-model",
        choices=[
            dict(
                index=0,
                finish_reason=finish,
                message=dict(role="assistant", content="Fabricated answer.", tool_calls=calls),
            )
        ],
        usage=usage,
    )


def adapter(limits: ModelExecutionLimits, handler: Callable, **kwargs) -> OpenAICompatModel:
    return OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "fabricated-wire-model"},
        limits=limits,
        output_limit_field="max_tokens",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


class CredentialProbe:
    def __init__(self, actor: str = "fabricated") -> None:
        self.actor = actor
        self.reads = 0

    async def authorization(self) -> str:
        self.reads += 1
        return " ".join(("Synthetic", self.actor, str(self.reads)))


def test_native_wire_mapping_receipts_and_usage(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        seen: list[dict] = []
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            tags = json.loads(request.headers["X-Tether-Request-Tags"])
            assert recorder.started[-1][0].attempt_id == tags["attempt_id"]
            assert digest(recorder.started[-1][2]) == recorder.started[-1][1]
            assert tags["principal"] == model_request.tags.principal
            assert tags["spec_version"] == "1"
            assert body["max_tokens"] == 64
            assert body["fabricated_option"] == {"unchanged": [1, 2]}
            assert body["model"] == "fabricated-wire-model"
            assert request.headers.get("authorization") is None
            seen.append(body)
            return httpx.Response(
                200,
                json=wire_response(
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                        "prompt_tokens_details": {"cached_tokens": 3},
                        "completion_tokens_details": {"reasoning_tokens": 2},
                    }
                ),
                headers={"x-request-id": "fabricated-header-id"},
            )

        model = adapter(limits, handler)
        try:
            response = await complete(
                model,
                model_request.model_copy(
                    update={"extra": {"fabricated_option": {"unchanged": [1, 2]}}}
                ),
            )
        finally:
            await model.aclose()
        assert len(seen) == 1
        assert response.content == "Fabricated answer."
        assert response.request_id == "fabricated-header-id"
        assert response.receipt
        assert response.receipt.response_id == "fabricated-body-id"
        assert response.receipt.provider_invocation_id is None
        assert response.usage == TokenUsage(
            input_tokens=10, output_tokens=4, cache_read_tokens=3, reasoning_tokens=2
        )
        assert len(recorder.observed) == 2
        assert recorder.observed[0][1].response_id is None
        assert recorder.observed[1][1].response_id == "fabricated-body-id"

    asyncio.run(scenario())


def test_native_agent_proposals_share_codecs(
    model_request: ModelRequest, limits: ModelExecutionLimits, tool_spec: ToolSpec
) -> None:
    async def scenario() -> None:
        calls = [
            dict(
                id="fabricated-tool-id",
                type="function",
                function=dict(name="lookup", arguments='{"count":2}'),
            )
        ]
        model = adapter(
            limits,
            lambda _: httpx.Response(200, json=wire_response(finish="tool_calls", calls=calls)),
        )

        async def prepare(request: ModelRequest) -> ModelRequest:
            return request

        try:
            result = await run_agent(
                model,
                model_request.model_copy(update={"tools": (tool_spec,)}),
                limits,
                prepare=prepare,
                authorize_history=authorize_history,
            )
        finally:
            await model.aclose()
        assert result.response.tool_calls[0].arguments == {"count": 2}
        assert result.response.usage is None

    asyncio.run(scenario())


def test_bounded_retries_refresh_identity_and_tags(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        credential = CredentialProbe()
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tags = json.loads(request.headers["X-Tether-Request-Tags"])
            seen.append((tags["attempt_id"], request.headers["authorization"]))
            if len(seen) == 1:
                return httpx.Response(
                    503,
                    json={"fabricated": "outage"},
                    headers={"retry-after": "NaN", "x-request-id": "failed-header"},
                )
            return httpx.Response(200, json=wire_response())

        model = adapter(limits, handler)
        try:
            response = await complete(
                model,
                model_request.model_copy(
                    update={"identity_mode": "caller", "credential": credential}
                ),
            )
        finally:
            await model.aclose()
        assert response.usage is None
        assert len(seen) == credential.reads == 2
        assert seen[0][0] != seen[1][0]
        assert seen[0][1] != seen[1][1]
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        assert len(recorder.started) == 2
        assert recorder.observed[0][1].provider_request_id == "failed-header"
        assert all("Synthetic" not in json.dumps(entry[2]) for entry in recorder.started)

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["reserve", "start", "observe"])
def test_local_callback_failures_are_not_sdk_wrapped_or_retried(
    model_request: ModelRequest, limits: ModelExecutionLimits, stage: str
) -> None:
    class CommitUnknown(Exception):
        pass

    class Recorder(ListAttemptRecorder):
        async def reserve(self):
            if stage == "reserve":
                raise error
            return await super().reserve()

        async def start(self, *args, **kwargs):
            if stage == "start":
                raise error
            await super().start(*args, **kwargs)

        async def observe(self, *args, **kwargs):
            if stage == "observe":
                raise error
            await super().observe(*args, **kwargs)

    error = CommitUnknown("fabricated_commit_unknown")

    async def scenario() -> None:
        seen: list[bool] = []

        def handler(_: httpx.Request) -> httpx.Response:
            seen.append(True)
            return httpx.Response(200, json=wire_response())

        model = adapter(limits, handler)
        try:
            with pytest.raises(CommitUnknown) as caught:
                await complete(
                    model,
                    model_request.model_copy(update={"attempts": Recorder(model_request.tags)}),
                )
        finally:
            await model.aclose()
        assert caught.value is error
        assert len(seen) == (1 if stage == "observe" else 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"attempts": None}, "missing_attempt_recorder"),
        ({"model": "unknown"}, "unconfigured tier"),
        ({"extra": {"max_tokens": 2}}, "parameter_conflict"),
        ({"identity_mode": "application"}, "missing_application_credential"),
        ({"identity_mode": "caller"}, "missing_credential"),
        ({"effort": "high"}, "unsupported_effort"),
    ],
)
def test_local_refusals_never_egress(
    model_request: ModelRequest, limits: ModelExecutionLimits, change: dict, reason: str
) -> None:
    async def scenario() -> None:
        seen: list[bool] = []

        def handler(_: httpx.Request) -> httpx.Response:
            seen.append(True)
            return httpx.Response(200, json=wire_response())

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelUnavailable, match=reason):
                await complete(model, model_request.model_copy(update=change))
        finally:
            await model.aclose()
        assert not seen

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        b"broken",
        b'{"choices":[]}',
        json.dumps(
            wire_response(
                calls=[
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{"},
                    }
                ]
            )
        ).encode(),
        json.dumps(
            wire_response(
                calls=[
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"x":1,"x":2}'},
                    }
                ]
            )
        ).encode(),
        json.dumps(
            wire_response(
                calls=[
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "unknown", "arguments": "{}"},
                    }
                ]
            )
        ).encode(),
    ],
)
def test_malformed_native_responses_fail_before_sdk_dispatch(
    model_request: ModelRequest, limits: ModelExecutionLimits, payload: bytes
) -> None:
    async def scenario() -> None:
        seen: list[bool] = []

        def handler(_: httpx.Request) -> httpx.Response:
            seen.append(True)
            return httpx.Response(200, content=payload)

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelUnavailable, match="invalid_response"):
                await complete(model, model_request)
        finally:
            await model.aclose()
        assert len(seen) == 1
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        assert recorder.observed[-1][2] is not None

    asyncio.run(scenario())


def test_deadline_and_cancellation_stop_underlying_request(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def handler(_: httpx.Request) -> httpx.Response:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
            return httpx.Response(200, json=wire_response())

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelUnavailable, match="timeout"):
                await complete(model, model_request.model_copy(update={"timeout_seconds": 0.01}))
            assert closed.is_set()
            entered.clear()
            closed.clear()
            task = asyncio.create_task(complete(model, model_request))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed.is_set()
        finally:
            await model.aclose()

    asyncio.run(scenario())
