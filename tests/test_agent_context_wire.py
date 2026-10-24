"""Selected native adapter retries recheck context before each fabricated HTTP request."""

import json

import httpx2 as httpx
import pytest
from test_agent_context import fixture, run
from test_compatible_adapter import CredentialProbe, adapter, wire_response

from tether.engine.governance import AllowAll
from tether.models.testing import ListAttemptRecorder
from tether.types.execution import Decision

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("revoke", [False, True])
async def test_native_retry_preserves_projection_identity_and_rechecks_current_reads(
    limits, custom, revoke
):
    class Policy(AllowAll):
        denied = False

        async def authorize(self, request):
            return Decision(
                allowed=not (self.denied and request.action == "read_session"),
                reason="denied" if self.denied else "allowed",
            )

    policy = Policy()
    seen = []

    def handler(request):
        seen.append(
            (json.loads(request.content), json.loads(request.headers["X-Tether-Request-Tags"]))
        )
        assert any(e.kind == "context_use" for e in h.log.entries)
        if len(seen) == 1:
            policy.denied = revoke
            return httpx.Response(
                503, json={"error": {"message": "Fabricated retry"}}, headers={"retry-after": "0"}
            )
        return httpx.Response(
            200,
            json=wire_response(
                usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
            ),
        )

    model = adapter(limits, handler)
    try:
        async with fixture(
            limits,
            model=model,
            policy=policy,
            attempt_factory=ListAttemptRecorder if custom else None,
        ) as h:
            h.actor = h.actor.model_copy(update={"credential": CredentialProbe()})
            result = await run(h)
            assert result.stop_reason == ("model_denied" if revoke else "final")
            assert len(seen) == (1 if revoke else 2)
            assert len([e for e in h.log.entries if e.kind == "context_use"]) == 1
            assert len([e for e in h.log.entries if e.kind == "context_preparation"]) == 1
            if not revoke:
                assert seen[0][0] == seen[1][0]
                assert seen[0][1]["model_call_id"] == seen[1][1]["model_call_id"]
                assert seen[0][1]["attempt_id"] != seen[1][1]["attempt_id"]
                assert seen[0][0]["max_tokens"] == 1024
    finally:
        await model.aclose()
