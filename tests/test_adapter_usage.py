"""Typed usage survives failed/unsupported bounded responses before retry policy."""

import asyncio

import httpx2 as httpx
import pytest
from test_compatible_adapter import CredentialProbe, adapter, wire_response

from tether.engine import Engine
from tether.engine.attempts import JournalAttemptRecorder
from tether.engine.governance import AllowAll, Governance
from tether.engine.reconstruction import restored_usage
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.stores.jsonl import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.types import ModelAttemptDenied, ModelUnavailable, TokenUsage
from tether.types.execution import (
    Actor,
    AgentSpec,
    AuthzRequest,
    Decision,
    ExecutionLimits,
    SessionState,
)
from tether.types.storage import SessionInit


@pytest.mark.parametrize("status", [200, 400, 503])
def test_valid_usage_on_invalid_or_failed_output_reaches_recorder(model_request, limits, status):
    async def scenario():
        def handler(request):
            body = wire_response(usage={"prompt_tokens": 6, "completion_tokens": 2})
            body["choices"][0]["message"]["reasoning_content"] = "Fabricated unsupported"
            return httpx.Response(status, json=body)

        model = adapter(limits.model_copy(update={"max_attempts": 1}), handler)
        recorder = model_request.attempts
        assert isinstance(recorder, ListAttemptRecorder)
        try:
            with pytest.raises(ModelUnavailable):
                await complete(model, model_request)
        finally:
            await model.aclose()
        assert recorder.observed_usage == [None, TokenUsage(input_tokens=6, output_tokens=2)]

    asyncio.run(scenario())


def test_failed_native_attempt_cost_can_deny_retry_before_egress(model_request, limits):
    class Budget(AllowAll):
        async def authorize(self, request: AuthzRequest) -> Decision:
            deny = (
                request.action == "call_model"
                and request.usage is not None
                and request.usage.input_tokens >= 6
            )
            return Decision(allowed=not deny, reason="budget_exceeded" if deny else "allowed")

    async def scenario():
        tags = model_request.tags
        log = MemoryTurnLog(SessionState(id=tags.session_id, owner=tags.principal))
        gov = Governance(
            Actor(id=tags.principal),
            log,
            MemoryHistory(),
            Budget(),
            tags.turn_id,
            ExecutionLimits(),
        )
        recorder = JournalAttemptRecorder(gov, tags, "demo")
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(503, json={"usage": {"prompt_tokens": 6, "completion_tokens": 2}})

        model = adapter(limits, handler)
        try:
            with pytest.raises(ModelAttemptDenied, match="budget_exceeded"):
                await complete(model, model_request.model_copy(update={"attempts": recorder}))
        finally:
            await model.aclose()
        assert len(calls) == len(recorder.started) == 1
        assert gov.usage.input_tokens == 6
        assert gov.usage.usage_complete

    asyncio.run(scenario())


@pytest.mark.parametrize("attempts", [1, 2])
def test_terminal_failure_records_physical_coverage_without_phantom_missing(limits, attempts):

    async def scenario():
        files = MemoryFiles()
        store = await JsonlSessionStore.open(files)
        provider = await JsonlHistory.open(files)
        actor = Actor(id="owner", credential=CredentialProbe())
        session = await store.create(actor.id, SessionInit())
        model = adapter(
            limits.model_copy(update={"max_attempts": attempts}),
            lambda request: httpx.Response(
                503, json={"usage": {"prompt_tokens": 6, "completion_tokens": 2}}
            ),
        )
        engine = Engine(
            model=model,
            history=MessageHistoryAdapter(provider),
            history_provider=provider,
            model_limits=limits,
        )
        spec = AgentSpec(
            id="fixture", version="1", title="Fixture", instructions="Fabricated", model="demo"
        )
        try:
            async with store.turn(session.id, actor.id, turn_id="turn") as log:
                result = await engine.run_turn(actor, log, "Fabricated", spec=spec)
                assert result.stop_reason == "model_unavailable"
                assert result.reason == "http 503"
                usage = restored_usage(log.entries, "turn")
                live = engine._runs[session.id].gov.usage
                assert live == usage
                assert usage.usage_complete
                assert usage.input_tokens == 6 * attempts
                assert usage.output_tokens == 2 * attempts
                assert usage.model_calls == usage.steps == 1
                assert usage.missing_by_purpose == {"answer": 0}
                logical = next(e for e in log.entries if e.kind == "model_call")
                ids = logical.facts["attempt_ids"]
                assert isinstance(ids, list)
                assert len(ids) == attempts
            summary = (await store.snapshot(session.id, actor.id)).view.summary
            assert summary.usage_complete
            assert summary.usage.input_tokens == usage.input_tokens
            assert summary.usage.output_tokens == usage.output_tokens
        finally:
            assert await engine.aclose()
            await model.aclose()

    asyncio.run(scenario())
