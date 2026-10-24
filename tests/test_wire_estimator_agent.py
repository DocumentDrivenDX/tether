"""Actual Agent fit/refusal and governed summary calls use selected native estimation."""

import json

import httpx2 as httpx
import pytest
from test_agent_context import fixture, run
from test_agent_summaries import configuration, turn
from test_agent_summaries import fixture as summary_fixture
from test_compatible_adapter import CredentialProbe, wire_response
from test_wire_estimator import estimator, profile, vocabulary

from tether.adapters.openai_compat import OpenAICompatModel
from tether.types.context import ModelLimits
from tether.types.execution import Actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("overflow", [False, True])
async def test_final_native_estimate_controls_actual_agent_one_token_boundary(limits, overflow):
    counted = estimator()
    async with fixture(limits, estimator=counted) as first:
        assert (await run(first)).stop_reason == "final"
        total = (await counted.estimate(first.model.requests[0])).tokens
    settings = configuration()
    old = settings["context_config"]
    settings["context_config"] = old.model_copy(
        update={
            "limits": {
                "demo": ModelLimits(
                    profile_id="fabricated-native-fit",
                    revision="1",
                    context_window_tokens=total + 1024 + 10 - int(overflow),
                    output_tokens=1024,
                    safety_margin_tokens=10,
                )
            }
        }
    )
    settings["estimator"] = counted
    async with fixture(limits, **settings) as h:
        result = await run(h)
        if overflow:
            assert (result.stop_reason, result.reason) == (
                "context_unavailable",
                "context_exhausted",
            )
            assert not h.model.requests
            assert not any(e.kind == "context_use" for e in h.log.entries)
        else:
            assert result.stop_reason == "final"
            accepted = next(e for e in h.log.entries if e.kind == "context_use")
            assert accepted.facts["estimate"]["tokens"] == total
            assert accepted.facts["estimate"]["method"] == "native-json-bpe-v1"


async def test_native_estimation_covers_governed_summary_and_main_requests(limits):
    seen = []

    def handler(request):
        seen.append(
            (json.loads(request.content), json.loads(request.headers["x-tether-request-tags"]))
        )
        return httpx.Response(200, json=wire_response())

    model = OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "main", "summary": "summary"},
        limits=limits,
        output_limit_field="max_tokens",
        transport=httpx.MockTransport(handler),
    )
    settings = configuration()
    settings["estimator"] = model.estimator(profile=profile(), vocabulary=vocabulary())
    try:
        async with summary_fixture(
            limits,
            model=model,
            settings=settings,
            actor=Actor(id="fabricated-owner", credential=CredentialProbe()),
        ) as h:
            assert (await turn(h, "first"))[0].stop_reason == "final"
            result, entries = await turn(h, "second")
            assert result.stop_reason == "final"
            assert [tags["purpose"] for _, tags in seen] == ["answer", "summary", "answer"]
            assert seen[1][0]["max_tokens"] == 256
            assert "tools" not in seen[1][0]
            uses = [e for e in entries if e.kind == "context_use"]
            assert all(e.facts["estimate"]["method"] == "native-json-bpe-v1" for e in uses)
            assert all(e.facts["estimate"]["quality"] == "approximate" for e in uses)
            assert sum(e.kind == "compaction" for e in entries) == 1
    finally:
        await model.aclose()
