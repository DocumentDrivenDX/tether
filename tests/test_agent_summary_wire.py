"""Native retry admission for auxiliary calls has no custom-recorder bypass."""

import json

import httpx2 as httpx
import pytest
from test_agent_summaries import fixture, turn
from test_compatible_adapter import CredentialProbe, wire_response

from tether.adapters.openai_compat import OpenAICompatModel
from tether.types.execution import Actor, Decision

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("revoke", [False, True])
async def test_native_summary_retry_preserves_request_and_current_authority(limits, custom, revoke):
    class Policy:
        denied = False

        async def authorize(self, request):
            return Decision(
                allowed=not (self.denied and request.action == "read_session"), reason="denied"
            )

    policy, seen = Policy(), []

    def handler(request):
        body = json.loads(request.content)
        tags = json.loads(request.headers["X-Tether-Request-Tags"])
        seen.append((body, tags))
        if tags["purpose"] == "summary":
            assert "tools" not in body
            assert body["max_tokens"] == 256
            if sum(t["purpose"] == "summary" for _, t in seen) == 1:
                policy.denied = revoke
                return httpx.Response(
                    503,
                    json={"error": {"message": "Fabricated retry"}},
                    headers={"retry-after": "0"},
                )
        return httpx.Response(
            200,
            json=wire_response(
                usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
            ),
        )

    model = OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "main-model", "summary": "summary-model"},
        limits=limits,
        output_limit_field="max_tokens",
        transport=httpx.MockTransport(handler),
    )
    try:
        async with fixture(
            limits,
            model=model,
            custom=custom,
            policy=policy,
            actor=Actor(id="fabricated-owner", credential=CredentialProbe()),
        ) as h:
            await turn(h, "first")
            result, entries = await turn(h, "second")
            assert result.stop_reason == ("context_unavailable" if revoke else "final")
            assert len(seen) == (2 if revoke else 4)
            summaries = [(b, t) for b, t in seen if t["purpose"] == "summary"]
            if not revoke:
                assert summaries[0][0] == summaries[1][0]
                assert summaries[0][1]["model_call_id"] == summaries[1][1]["model_call_id"]
                assert summaries[0][1]["attempt_id"] != summaries[1][1]["attempt_id"]
                assert sum(e.kind == "compaction" for e in entries) == 1
    finally:
        await model.aclose()
