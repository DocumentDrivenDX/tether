"""Doubles preserve text boundaries; physical identities are regenerated, never cassette keys."""

import json

import httpx2 as httpx
import pytest
from test_compatible_adapter import adapter, wire_response
from test_stream_frames import identity

from tether.models import complete
from tether.models.testing import ListAttemptRecorder, RecordingModel, ReplayModel, ScriptedModel
from tether.types import ModelResponse, ModelUnavailable, TextDelta

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_script_record_replay_emits_explicit_unicode_boundaries_once(model_request):
    response = ModelResponse(content="é😀", finish_reason="stop")
    scripted = ScriptedModel(
        [TextDelta(text="é"), TextDelta(text=""), TextDelta(text="😀"), response]
    )
    lines = []

    async def writer(line):
        lines.append(line)

    values = [
        item
        async for item in RecordingModel(scripted, writer, fabricated=True).stream(model_request)
    ]
    deltas = [v for v in values if isinstance(v, TextDelta)]
    assert [v.text for v in deltas] == ["é", "", "😀"]
    assert all(v.identity == model_request.attempts.reserved[0] for v in deltas)
    encoded = "".join(lines)
    assert json.loads(lines[1])["stream"] == ["é", "", "😀"]
    assert "attempt_id" not in encoded
    tags = model_request.tags.model_copy(
        update={"session_id": "another", "turn_id": "other", "model_call_id": "fresh"}
    )
    recorder = ListAttemptRecorder(tags)
    changed = model_request.model_copy(update={"tags": tags, "attempts": recorder})
    replayed = [item async for item in ReplayModel(encoded).stream(changed)]
    assert [v.text for v in replayed if isinstance(v, TextDelta)] == ["é", "", "😀"]
    assert all(v.identity == recorder.reserved[0] for v in replayed if isinstance(v, TextDelta))
    assert replayed[-1] == response


async def test_explicit_forged_fixture_claim_is_not_repaired(model_request):
    claim = identity(model_request, session_id="foreign")
    model = ScriptedModel(
        [TextDelta(text="a", identity=claim), ModelResponse(content="a", finish_reason="stop")]
    )
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(model, model_request)


@pytest.mark.parametrize("pieces", [["wrong"], ["a", "ab"], ["é", "a"]])
async def test_successful_cassette_with_inconsistent_text_refuses_at_load(model_request, pieces):
    from tether.models.canonical import cassette_request, digest, matching_request

    value = {
        "key": digest(matching_request(model_request)),
        "request": cassette_request(model_request),
        "stream": pieces,
        "outcome": {
            "response": ModelResponse(content="abc", finish_reason="stop").model_dump(mode="json")
        },
    }
    encoded = (
        json.dumps({"cassette": 1, "provider": "fabricated"}) + "\n" + json.dumps(value) + "\n"
    )
    with pytest.raises(ValueError, match="cassette_text_mismatch"):
        ReplayModel(encoded)


async def test_native_retry_binds_whole_text_to_successful_explicit_attempt(limits, model_request):
    seen = []

    async def handler(request):
        tags = json.loads(request.headers["x-tether-request-tags"])
        seen.append(tags["attempt_id"])
        return httpx.Response(
            503 if len(seen) == 1 else 200, json={} if len(seen) == 1 else wire_response()
        )

    model = adapter(limits, handler)
    try:
        values = [item async for item in model.stream(model_request)]
        assert len(seen) == 2
        assert seen[0] != seen[1]
        delta = values[0]
        assert isinstance(delta, TextDelta)
        assert delta.identity is not None
        assert delta.identity.attempt_id == seen[1]
        assert delta.identity == model_request.attempts.started[-1][0]
        assert isinstance(values[-1], ModelResponse)
        assert delta.text == values[-1].content
    finally:
        await model.aclose()


async def test_malformed_falsy_identity_is_not_auto_repaired_by_scripted(model_request):
    malformed = TextDelta(text="a").model_copy(update={"identity": {}})
    model = ScriptedModel([malformed, ModelResponse(content="a", finish_reason="stop")])
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(model, model_request)
