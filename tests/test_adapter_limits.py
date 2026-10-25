"""Bounds, isolation and cancellation scenarios for the selected compatible subset."""

import asyncio
import gzip
import json
from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from test_compatible_adapter import CredentialProbe, adapter, wire_response

from tether.adapters.openai_compat import OpenAICompatModel
from tether.adapters.pydantic_ai.compatible import retry_delay
from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.types import ModelAttemptDenied, ModelExecutionLimits, ModelRequest, ModelUnavailable


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("compressed", [False, True])
def test_decoded_body_bounds_and_cleanup(
    model_request: ModelRequest, limits: ModelExecutionLimits, compressed: bool
) -> None:
    async def scenario() -> None:
        data = json.dumps(wire_response()).encode()
        stream = Chunks([gzip.compress(data)] if compressed else [data[:30], data[30:]])
        headers = {"content-encoding": "gzip"} if compressed else {}
        model = adapter(limits, lambda _: httpx.Response(200, stream=stream, headers=headers))
        try:
            if compressed:
                with pytest.raises(ModelUnavailable, match="unsupported_content_encoding"):
                    await complete(model, model_request)
                assert stream.reads == 0
            else:
                assert (await complete(model, model_request)).content == "Fabricated answer."
        finally:
            await model.aclose()
        assert stream.closed
        huge = Chunks([b" " * 100, b" " * 100])
        model = adapter(
            limits.model_copy(update={"max_response_bytes": 128}),
            lambda _: httpx.Response(
                200, stream=huge, headers={"x-request-id": "fabricated-oversize"}
            ),
        )
        try:
            with pytest.raises(ModelUnavailable, match="response_too_large") as error:
                await complete(model, model_request)
        finally:
            await model.aclose()
        assert huge.closed
        assert error.value.receipt
        assert error.value.receipt.provider_request_id == "fabricated-oversize"

    asyncio.run(scenario())


def test_argument_byte_cap_and_unsupported_extensions(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        calls = [
            dict(
                id="c",
                type="function",
                function=dict(name="lookup", arguments=json.dumps({"x": "é" * 300})),
            )
        ]
        payload = wire_response(finish="tool_calls", calls=calls)
        for value, reason in (
            (payload, "response_too_large"),
            (
                dict(
                    wire_response(),
                    choices=[
                        dict(
                            index=0,
                            finish_reason="stop",
                            message=dict(
                                role="assistant", content="fabricated", images=["unsupported"]
                            ),
                        )
                    ],
                ),
                "unsupported_content",
            ),
        ):
            model = adapter(limits, lambda _, value=value: httpx.Response(200, json=value))
            try:
                with pytest.raises(ModelUnavailable, match=reason):
                    await complete(model, model_request)
            finally:
                await model.aclose()

    asyncio.run(scenario())


def test_parallel_callers_and_application_identity_are_isolated(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        seen: dict[str, str | None] = {}
        app = CredentialProbe("application")

        async def handler(request: httpx.Request) -> httpx.Response:
            tags = json.loads(request.headers["X-Tether-Request-Tags"])
            seen[tags["model_call_id"]] = request.headers.get("authorization")
            await asyncio.sleep(0)
            return httpx.Response(200, json=wire_response())

        model = adapter(limits, handler, application_credential=app)
        callers = [CredentialProbe("actor-one"), CredentialProbe("actor-two")]
        requests = []
        for index, mode in enumerate(("caller", "caller", "application", "anonymous")):
            tags = model_request.tags.model_copy(update={"model_call_id": f"call-{index}"})
            requests.append(
                model_request.model_copy(
                    update={
                        "tags": tags,
                        "attempts": ListAttemptRecorder(tags),
                        "identity_mode": mode,
                        "credential": callers[index] if mode == "caller" else None,
                    }
                )
            )
        try:
            await asyncio.gather(*(complete(model, r) for r in requests))
            assert seen["call-0"] != seen["call-1"]
            assert seen["call-2"] == " ".join(("Synthetic", "application", "1"))
            assert seen["call-3"] is None
            assert app.reads == 1
            assert [c.reads for c in callers] == [1, 1]
            with pytest.raises(ModelUnavailable, match="identity_mode_conflict"):
                await complete(model, model_request.model_copy(update={"credential": callers[0]}))
        finally:
            await model.aclose()

    asyncio.run(scenario())


def test_foreign_attempt_identity_prevents_egress(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        tags = model_request.tags.model_copy(update={"session_id": "wrong-session"})
        seen = []

        def handler(_: httpx.Request) -> httpx.Response:
            seen.append(True)
            return httpx.Response(200, json=wire_response())

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelAttemptDenied, match="attempt_identity_mismatch"):
                await complete(
                    model, model_request.model_copy(update={"attempts": ListAttemptRecorder(tags)})
                )
        finally:
            await model.aclose()
        assert not seen

    asyncio.run(scenario())


def test_retry_exhaustion_and_backoff_cancellation(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        count = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal count
            count += 1
            return httpx.Response(503, json={"fabricated": "outage"}, headers={"retry-after": "0"})

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelUnavailable, match=r"http 503"):
                await complete(model, model_request)
            assert count == limits.max_attempts
        finally:
            await model.aclose()
        observed = asyncio.Event()

        class Recorder(ListAttemptRecorder):
            async def observe(self, *args, **kwargs):
                await super().observe(*args, **kwargs)
                observed.set()

        model = adapter(
            limits.model_copy(update={"max_retry_delay_seconds": 30}),
            lambda _: httpx.Response(503, json={}, headers={"retry-after": "30"}),
        )
        recorder = Recorder(model_request.tags)
        try:
            task = asyncio.create_task(
                complete(model, model_request.model_copy(update={"attempts": recorder}))
            )
            await observed.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(recorder.started) == 1
        finally:
            await model.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "header", [None, "NaN", "Infinity", "-1", "broken", "999999999999999999999"]
)
def test_retry_after_values_remain_finite_and_bounded(header: str | None) -> None:
    assert 0 <= retry_delay(header, 0.001, 50) <= 0.001


@pytest.mark.parametrize(
    ("url", "tiers", "field"),
    [
        ("ftp://fabricated.invalid", {"demo": "fabricated"}, "max_tokens"),
        ("https://fabricated.invalid", {}, "max_tokens"),
        ("https://fabricated.invalid", {"demo": "fabricated"}, "ignored"),
    ],
)
def test_explicit_configuration_refuses_invalid_profiles(
    limits: ModelExecutionLimits,
    url: str,
    tiers: dict[str, str],
    field: str,
) -> None:
    with pytest.raises(ValueError, match=r"invalid_|unsupported_"):
        OpenAICompatModel(base_url=url, tiers=tiers, limits=limits, output_limit_field=field)


def test_invalid_native_usage_is_not_retried(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        seen: list[bool] = []

        def handler(_: httpx.Request) -> httpx.Response:
            seen.append(True)
            return httpx.Response(
                200,
                json=wire_response(
                    usage={
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "prompt_tokens_details": "bad",
                    }
                ),
            )

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelUnavailable, match="invalid_response"):
                await complete(model, model_request)
        finally:
            await model.aclose()
        assert len(seen) == 1

    asyncio.run(scenario())


def test_failed_response_content_stays_in_authorized_capture_not_errors(
    model_request: ModelRequest, limits: ModelExecutionLimits
) -> None:
    async def scenario() -> None:
        model = adapter(
            limits,
            lambda _: httpx.Response(
                400,
                content=b"fabricated-body-marker",
                headers={"x-request-id": "fabricated-failure-id"},
            ),
        )
        try:
            with pytest.raises(ModelUnavailable, match=r"http 400") as error:
                await complete(model, model_request)
        finally:
            await model.aclose()
        assert "fabricated-body-marker" not in str(error.value)
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        assert len(recorder.observed) == 2
        from base64 import b64encode

        assert recorder.observed[-1][2] == {
            "status": 400,
            "body": {"raw_base64": b64encode(b"fabricated-body-marker").decode("ascii")},
        }

    asyncio.run(scenario())
