"""Explicit estimator binding, native expansion bounds and immutable composition inputs."""

import pytest
from test_wire_estimator import estimator, profile, vocabulary

from tether.adapters.pydantic_ai.estimator import OpenAIWireEstimator
from tether.types import Message, ToolCall
from tether.types.canonical import canonical
from tether.types.context import ContextError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "change", ["profile", "vocabulary_revision", "framing", "tier", "field", "capacity"]
)
async def test_estimate_revision_binds_explicit_profile_codec_and_native_mapping(
    model_request, change
):
    first = await estimator().estimate(model_request)
    settings = profile()
    tiers, field = {"demo": "fabricated-native"}, "max_tokens"
    if change == "profile":
        settings = profile(id="different")
    elif change == "vocabulary_revision":
        settings = profile(revision="2")
    elif change == "framing":
        settings = profile(message_framing_tokens=9)
    elif change == "tier":
        tiers = {"demo": "different-native"}
    elif change == "field":
        field = "max_completion_tokens"
    else:
        settings = profile(max_count_operations=3)
    second = await OpenAIWireEstimator(
        tiers=tiers, output_limit_field=field, profile=settings, vocabulary=vocabulary()
    ).estimate(model_request)
    assert first.revision != second.revision
    assert first.quality == second.quality == "approximate"


async def test_estimator_copies_configuration_before_any_await(model_request):
    settings, tiers = profile(), {"demo": "fabricated-native"}
    counted = OpenAIWireEstimator(
        tiers=tiers, output_limit_field="max_tokens", profile=settings, vocabulary=vocabulary()
    )
    before = await counted.estimate(model_request)
    tiers["demo"] = "changed"
    object.__setattr__(settings, "message_framing_tokens", 999)
    assert await counted.estimate(model_request) == before


async def test_native_json_expansion_is_bounded_before_counting(model_request, monkeypatch):
    proposal = Message(
        role="assistant",
        content="",
        tool_calls=(ToolCall(id="call", name="lookup", arguments={"value": '"' * 3000}),),
    )
    request = model_request.model_copy(
        update={
            "messages": (
                *model_request.messages,
                proposal,
                Message(role="tool", content="Result", tool_call_id="call"),
            )
        }
    )
    size = len(canonical(request.model_dump(mode="json")))
    counted = estimator(profile(max_request_bytes=size))

    def unexpected(body):
        raise AssertionError("oversized_native_payload_counted")

    monkeypatch.setattr(counted, "_count", unexpected)
    with pytest.raises(ContextError, match="context_unmeasurable"):
        await counted.estimate(request)
    assert counted._active == 0
