"""Common model-port behavior and fabricated cassette conformance."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from tether.models import complete
from tether.models.canonical import canonical, cassette_request, digest, matching_request
from tether.models.testing import ListAttemptRecorder, RecordingModel, ReplayModel, ScriptedModel
from tether.types import (
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    StreamItem,
    TextDelta,
    TokenUsage,
)


class CredentialProbe:
    def __init__(self) -> None:
        self.reads = 0

    async def authorization(self) -> str:
        self.reads += 1
        return " ".join(("Synthetic", "fabricated", str(self.reads)))


@pytest.mark.parametrize("kind", ["scripted", "recording", "sequence", "keyed"])
def test_shared_success_conformance(
    model_request: ModelRequest, kind: Literal["scripted", "recording", "sequence", "keyed"]
) -> None:
    async def scenario() -> None:
        response = ModelResponse(
            content="Fabricated answer.",
            finish_reason="stop",
            usage=TokenUsage(input_tokens=4, output_tokens=2, cache_read_tokens=0),
        )
        lines: list[str] = []

        async def writer(line: str) -> None:
            lines.append(line)

        scripted = ScriptedModel([response])
        recording = RecordingModel(scripted, writer, fabricated=True)
        if kind == "scripted":
            model = scripted
        elif kind == "recording":
            model = recording
        else:
            await complete(recording, model_request)
            model = ReplayModel("".join(lines), mode=kind)
        assert await complete(model, model_request) == response
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        assert recorder.started
        assert recorder.observed[-1][1].response_id is None

    asyncio.run(scenario())


def test_script_exhaustion_and_request_snapshot(model_request: ModelRequest) -> None:
    async def scenario() -> None:
        credential = CredentialProbe()
        original = model_request.model_copy(
            update={"credential": credential, "extra": {"nested": {"a": 1}}}
        )
        model = ScriptedModel([ModelResponse(finish_reason="stop")])
        await complete(model, original)
        original.extra["nested"] = "changed"
        assert model.requests[0].credential is None
        assert model.requests[0].attempts is None
        assert model.requests[0].extra == {"nested": {"a": 1}}
        assert credential.reads == 0
        with pytest.raises(AssertionError, match="script_exhausted"):
            await complete(model, model_request)

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["timeout", "http 503", "unconfigured tier"])
def test_record_replay_unavailable(model_request: ModelRequest, reason: str) -> None:
    async def scenario() -> None:
        lines: list[str] = []

        async def writer(line: str) -> None:
            lines.append(line)

        model = RecordingModel(
            ScriptedModel([ModelUnavailable(reason, "fabricated-id")]), writer, fabricated=True
        )
        for item in (model,):
            with pytest.raises(ModelUnavailable, match=reason):
                await complete(item, model_request)
        replay = ReplayModel("".join(lines))
        with pytest.raises(ModelUnavailable, match=reason) as error:
            await complete(replay, model_request)
        assert error.value.request_id == "fabricated-id"

    asyncio.run(scenario())


def test_privacy_keys_and_first_mismatch(model_request: ModelRequest) -> None:
    async def scenario() -> None:
        lines: list[str] = []
        credential = CredentialProbe()

        async def writer(line: str) -> None:
            lines.append(line)

        model = RecordingModel(
            ScriptedModel([ModelResponse(content="fabricated", finish_reason="stop")]),
            writer,
            fabricated=True,
        )
        await complete(model, model_request.model_copy(update={"credential": credential}))
        text = "".join(lines)
        record = json.loads(lines[1])
        assert record["request"]["tags"]["principal"] == "recorded"
        assert "credential" not in record["request"]
        assert "attempts" not in record["request"]
        assert "traceparent" not in record["request"]
        assert "timeout_seconds" not in record["request"]
        changed_ids = model_request.tags.model_copy(
            update={
                "session_id": "different",
                "turn_id": "another",
                "model_call_id": "changed",
                "principal": "new",
            }
        )
        matched = model_request.model_copy(update={"tags": changed_ids, "attempts": None})
        assert digest(matching_request(model_request)) == digest(matching_request(matched))
        assert (await complete(ReplayModel(text), matched)).content == "fabricated"
        with pytest.raises(AssertionError, match=r"request\.model") as error:
            await complete(
                ReplayModel(text), model_request.model_copy(update={"model": "sensitive-marker"})
            )
        assert "sensitive-marker" not in str(error.value)
        assert "Synthetic" not in text
        assert credential.reads == 0

    asyncio.run(scenario())


def test_cassette_fail_closed(model_request: ModelRequest) -> None:
    response = ModelResponse(finish_reason="stop")
    record = dict(
        key=digest(matching_request(model_request)),
        request=cassette_request(model_request),
        stream=[],
        outcome={"response": response.model_dump(mode="json")},
    )
    header = '{"cassette":1,"provider":"scripted"}\n'

    def encoded(value: dict) -> str:
        return header + json.dumps(value) + "\n"

    assert ReplayModel(encoded(record))
    for bad in ("", '{"cassette":2,"provider":"scripted"}', header + "broken"):
        with pytest.raises(ValueError, match="invalid_cassette"):
            ReplayModel(bad)
    for changed in (
        dict(record, key="a" * 64),
        dict(record, outcome={}),
        dict(record, outcome={"response": {}, "unavailable": {"reason": "timeout"}}),
        dict(record, request=dict(cassette_request(model_request), credential="forbidden")),
    ):
        with pytest.raises(ValueError, match=r"invalid_|cassette_|noncanonical_"):
            ReplayModel(encoded(changed))
    with pytest.raises(ValueError, match="unsupported_replay_mode"):
        ReplayModel(encoded(record), mode=cast(Literal["keyed"], "invalid"))

    async def writer(_: str) -> None:
        pass

    with pytest.raises(TypeError):
        RecordingModel(ScriptedModel([]), writer, fabricated=cast("Literal[True]", False))


@pytest.mark.parametrize(
    ("shape", "reason"),
    [
        ([], "incomplete_stream"),
        ([ModelResponse(finish_reason="stop"), TextDelta(text="late")], "invalid_response"),
    ],
)
def test_stream_shape_and_cleanup(
    model_request: ModelRequest, shape: list[StreamItem], reason: str
) -> None:
    async def scenario() -> None:
        closed: list[bool] = []

        class Port:
            provider = "fabricated"

            async def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
                try:
                    for item in shape:
                        yield item
                finally:
                    closed.append(True)

        with pytest.raises(ModelUnavailable, match=reason):
            await complete(Port(), model_request)
        assert closed == [True]

    asyncio.run(scenario())


def test_payload_validation_and_canonical_unicode(model_request: ModelRequest) -> None:
    assert canonical({"z": "é", "a": 2}) == '{"a":2,"z":"é"}'.encode()
    with pytest.raises(ValueError, match="Out of range"):
        canonical({"x": float("nan")})
    for kwargs in (
        {"max_output_tokens": 0},
        {"max_output_tokens": True},
        {"timeout_seconds": float("inf")},
    ):
        with pytest.raises(ValidationError):
            ModelRequest.model_validate(model_request.model_dump() | kwargs)
    assert "credential" not in ModelRequest.model_json_schema()["properties"]
    assert ModelUnavailable("content must not appear").reason == "model_unavailable"


def test_partial_outage_is_recorded_and_replayed(model_request: ModelRequest) -> None:
    async def scenario() -> None:
        lines: list[str] = []

        async def writer(line: str) -> None:
            lines.append(line)

        async def collect(model, deltas: list[str]) -> None:
            async for item in model.stream(model_request):
                if isinstance(item, TextDelta):
                    deltas.append(item.text)

        recording = RecordingModel(
            ScriptedModel([TextDelta(text="provisional"), ModelUnavailable("timeout")]),
            writer,
            fabricated=True,
        )
        deltas: list[str] = []
        with pytest.raises(ModelUnavailable, match="timeout"):
            await collect(recording, deltas)
        assert deltas == ["provisional"]
        replay = ReplayModel("".join(lines))
        deltas = []
        with pytest.raises(ModelUnavailable, match="timeout"):
            await collect(replay, deltas)
        assert deltas == ["provisional"]

    asyncio.run(scenario())


def test_record_replay_retains_unavailable_receipt(model_request: ModelRequest) -> None:
    from tether.types import ResponseReceipt

    async def scenario() -> None:
        receipt = ResponseReceipt(
            provider="fabricated",
            endpoint="offline",
            schema_profile="fabricated-v1",
            provider_request_id="header-id",
            provider_invocation_id="invocation-id",
            request_id_source="header",
        )
        lines: list[str] = []

        async def writer(line: str) -> None:
            lines.append(line)

        model = RecordingModel(
            ScriptedModel([ModelUnavailable("http 503", "header-id", receipt)]),
            writer,
            fabricated=True,
        )
        with pytest.raises(ModelUnavailable, match=r"http 503"):
            await complete(model, model_request)
        with pytest.raises(ModelUnavailable, match=r"http 503") as error:
            await complete(ReplayModel("".join(lines)), model_request)
        assert error.value.receipt == receipt
        assert isinstance(model_request.attempts, ListAttemptRecorder)
        assert model_request.attempts.observed[-1][1] == receipt

    asyncio.run(scenario())
