"""Physical retry accounting is shared by policy, reconstruction and projections."""

from datetime import UTC, datetime

import pytest

from tether.context.testing import fabricated_context
from tether.engine.attempts import JournalAttemptRecorder
from tether.engine.governance import AllowAll, Governance
from tether.engine.reconstruction import restored_usage
from tether.models.canonical import digest
from tether.models.testing import ListAttemptRecorder, ScriptedModel
from tether.stores.projections import create_index
from tether.testing.execution import MemoryHistory, MemoryTurnLog
from tether.types import ModelResponse, RequestTags, ResponseReceipt, TokenUsage
from tether.types.execution import (
    Actor,
    AgentSpec,
    AuthzRequest,
    Decision,
    Entry,
    ExecutionLimits,
    SessionState,
)
from tether.types.storage import IntegrityError, SessionInit
from tether.types.usage import UsageLedger, usage_change

AT = datetime(2026, 1, 1, tzinfo=UTC)
RECEIPT = ResponseReceipt(
    provider="fabricated", endpoint="offline", schema_profile="fixture", request_id_source="none"
)


def event(
    kind,
    index,
    *,
    attempt="a",
    call: str | int | None = "logical",
    purpose="answer",
    usage=None,
    **facts,
):
    if kind == "model_call":
        facts.update(model_call_id=call, purpose=purpose, attempt_ids=[attempt])
    else:
        facts["identity"] = dict(
            session_id="session",
            turn_id="turn",
            attempt_id=attempt,
            model_call_id=call,
            purpose=purpose,
        )
    facts["usage"] = usage.model_dump(mode="json") if usage is not None else None
    return Entry(kind=kind, index=index, at=AT, turn_id="turn", key=str(index), facts=facts)


@pytest.mark.parametrize("purpose", ["answer", "summary", "correction"])
def test_every_prefix_matches_projection_and_restart(purpose):
    known = TokenUsage(input_tokens=10, output_tokens=2, cache_read_tokens=5)
    partial = TokenUsage(input_tokens=10, output_tokens=2)
    entries = [
        event("model_attempt", 0, purpose=purpose),
        event("model_receipt", 1, purpose=purpose),
        event("model_receipt", 2, purpose=purpose, usage=known),
        event("model_receipt", 3, purpose=purpose),
        event("model_attempt", 4, attempt="b", purpose=purpose),
        event(
            "model_receipt",
            5,
            attempt="b",
            purpose=purpose,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        ),
        event(
            "model_call",
            6,
            purpose=purpose,
            attempt="b",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        ),
        event("model_receipt", 7, purpose=purpose, usage=partial),
        event("model_receipt", 8, purpose=purpose, usage=known),
    ]
    ledger = UsageLedger()
    projection = create_index("session", "owner", SessionInit(), AT)
    for i, entry in enumerate(entries):
        ledger.apply(entry)
        ledger.apply(entry)  # accepted-key retry
        projection.apply(entry)
        usage = ledger.snapshot()
        assert usage == restored_usage(entries[: i + 1], "turn")
        assert projection.summary.usage == ledger.total
        assert projection.summary.usage_complete == usage.usage_complete
        assert usage.model_calls == 1
        assert usage.steps == (purpose == "answer")
        assert usage.summary_calls == (purpose == "summary")
        assert usage.by_purpose[purpose] == ledger.total
        assert usage.missing_by_purpose[purpose] == ledger.missing
    usage = ledger.snapshot()
    assert usage.input_tokens == 10
    assert usage.output_tokens == 2
    assert usage.usage_complete
    assert ledger.total.cache_read_tokens == 5


@pytest.mark.parametrize("reverse", [False, True])
def test_optional_subcounts_enrich_in_both_orders(reverse):
    partial = TokenUsage(input_tokens=10, output_tokens=2)
    full = partial.model_copy(update={"cache_read_tokens": 5, "reasoning_tokens": 1})
    first, second = (full, partial) if reverse else (partial, full)
    change = usage_change(first, second, exists=True)
    assert change.value == full
    assert change.added.input_tokens == change.added.output_tokens == 0
    assert change.added.cache_read_tokens == (None if reverse else 5)
    assert usage_change(full, None, exists=True).value == full
    with pytest.raises(IntegrityError, match="usage_changed"):
        usage_change(full, full.model_copy(update={"cache_read_tokens": 6}), exists=True)


def test_identity_and_purpose_cannot_change():
    for changed in ({"call": "other"}, {"purpose": "summary"}):
        ledger = UsageLedger()
        ledger.apply(event("model_attempt", 0))
        before = ledger.snapshot()
        with pytest.raises(IntegrityError, match=r"usage_(identity|purpose)_changed"):
            ledger.apply(event("model_receipt", 1, **changed))
        assert ledger.snapshot() == before


def test_custom_recorder_coverage_is_missing_without_extra_generation():
    ledger = UsageLedger()
    ledger.apply(
        event(
            "model_call",
            0,
            usage=TokenUsage(input_tokens=4, output_tokens=2),
            attempts_complete=False,
        )
    )
    usage = ledger.snapshot()
    assert usage.input_tokens == 4
    assert usage.model_calls == usage.steps == 1
    assert not usage.usage_complete
    assert usage.missing_by_purpose == {"answer": 1}


def test_committed_retry_cost_and_actual_purpose_reach_next_policy():
    import asyncio

    class Policy(AllowAll):
        def __init__(self):
            self.seen = []

        async def authorize(self, request: AuthzRequest) -> Decision:
            if request.action == "call_model":
                self.seen.append(request)
            return Decision(allowed=True, reason="allowed")

    async def scenario():
        policy = Policy()
        log = MemoryTurnLog(SessionState(id="session", owner="owner"))
        gov = Governance(Actor(id="owner"), log, MemoryHistory(), policy, "turn", ExecutionLimits())
        tags = RequestTags(
            session_id="session",
            turn_id="turn",
            model_call_id="logical",
            principal="owner",
            spec_id="fixture",
            spec_version="1",
            purpose="summary",
        )
        recorder = JournalAttemptRecorder(gov, tags, "demo")
        first = await recorder.reserve()
        await recorder.start(first, request_digest=digest({}), wire_request={})
        await recorder.observe(first, RECEIPT)
        await recorder.observe(first, RECEIPT, usage=TokenUsage(input_tokens=8, output_tokens=3))
        second = await recorder.reserve()
        await recorder.start(second, request_digest=digest({}), wire_request={})
        request = policy.seen[-1]
        assert request.purpose == "summary"
        assert request.usage.input_tokens == 8
        assert request.usage.summary_calls == request.usage.model_calls == 1
        assert request.usage.steps == 0
        assert request.usage.usage_complete
        assert not gov.usage.usage_complete
        assert gov.usage.missing_by_purpose == {"summary": 1}
        assert gov.usage == restored_usage(log.entries, "turn")

    asyncio.run(scenario())


def test_falsey_policy_and_recorder_factory_are_used(limits):
    import asyncio

    from tether.engine import Engine

    class Deny(AllowAll):
        def __bool__(self):
            return False

        async def authorize(self, request: AuthzRequest) -> Decision:
            return Decision(allowed=request.action != "call_model", reason="denied")

    class Factory:
        def __init__(self):
            self.calls = 0

        def __bool__(self):
            return False

        def __call__(self, tags: RequestTags):
            self.calls += 1
            return ListAttemptRecorder(tags)

    async def scenario():
        spec = AgentSpec(
            id="fixture", version="1", title="Fixture", instructions="Fabricated", model="demo"
        )
        for policy in (Deny(), AllowAll()):
            factory = Factory()
            model = ScriptedModel([ModelResponse(content="Fabricated", finish_reason="stop")])
            engine = Engine(
                **fabricated_context(),
                model=model,
                history=MemoryHistory(),
                model_limits=limits,
                policy=policy,
                attempt_factory=factory,
            )
            log = MemoryTurnLog(SessionState(id="session", owner="owner"))
            try:
                await engine.run_turn(Actor(id="owner"), log, "Fabricated", spec=spec)
                denied = isinstance(policy, Deny)
                assert len(model.requests) == factory.calls == (0 if denied else 1)
            finally:
                assert await engine.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unknown", "cancel"])
def test_uncertain_observation_never_publishes_optimistic_usage(failure):
    import asyncio

    from tether.types.execution import CommitUnknown

    class Log(MemoryTurnLog):
        async def append(self, entries):
            if any(e.kind == "model_receipt" for e in entries):
                if failure == "cancel":
                    raise asyncio.CancelledError()
                raise CommitUnknown("uncertain")
            return await super().append(entries)

    async def scenario():
        log = Log(SessionState(id="session", owner="owner"))
        gov = Governance(
            Actor(id="owner"), log, MemoryHistory(), AllowAll(), "turn", ExecutionLimits()
        )
        tags = RequestTags(
            session_id="session",
            turn_id="turn",
            model_call_id="logical",
            principal="owner",
            spec_id="fixture",
            spec_version="1",
        )
        recorder = JournalAttemptRecorder(gov, tags, "demo")
        identity = await recorder.reserve()
        await recorder.start(identity, request_digest=digest({}), wire_request={})
        before = gov.usage.model_copy(deep=True)
        expected = asyncio.CancelledError if failure == "cancel" else CommitUnknown
        with pytest.raises(expected):
            await recorder.observe(
                identity, RECEIPT, usage=TokenUsage(input_tokens=8, output_tokens=3)
            )
        assert gov.usage == before
        assert not gov.usage.usage_complete
        assert gov.usage.input_tokens == 0

    asyncio.run(scenario())


def test_conflicting_observation_refuses_before_journal_commit():
    import asyncio

    async def scenario():
        log = MemoryTurnLog(SessionState(id="session", owner="owner"))
        gov = Governance(
            Actor(id="owner"), log, MemoryHistory(), AllowAll(), "turn", ExecutionLimits()
        )
        tags = RequestTags(
            session_id="session",
            turn_id="turn",
            model_call_id="logical",
            principal="owner",
            spec_id="fixture",
            spec_version="1",
        )
        recorder = JournalAttemptRecorder(gov, tags, "demo")
        identity = await recorder.reserve()
        await recorder.start(identity, request_digest=digest({}), wire_request={})
        known = TokenUsage(input_tokens=8, output_tokens=3)
        await recorder.observe(identity, RECEIPT, usage=known)
        entries = log.entries
        with pytest.raises(IntegrityError, match="usage_changed"):
            await recorder.observe(
                identity, RECEIPT, usage=known.model_copy(update={"input_tokens": 9})
            )
        assert log.entries == entries
        assert gov.usage.input_tokens == 8

    asyncio.run(scenario())


def test_legacy_none_logical_ids_are_independent_and_invalid_ids_refuse():
    ledger = UsageLedger()
    entries = [
        event("model_call", 0, call=None, usage=TokenUsage(input_tokens=3, output_tokens=1)),
        event("model_call", 1, call=None, usage=TokenUsage(input_tokens=4, output_tokens=2)),
    ]
    entries = [
        e.model_copy(update={"facts": {k: v for k, v in e.facts.items() if k != "attempt_ids"}})
        for e in entries
    ]
    for entry in entries:
        ledger.apply(entry)
    assert ledger.snapshot().input_tokens == 7
    assert ledger.snapshot().model_calls == 2
    assert ledger.snapshot().usage_complete
    before = ledger.snapshot()
    with pytest.raises(IntegrityError, match="usage_identity_invalid"):
        ledger.apply(event("model_call", 2, call=123))
    assert ledger.snapshot() == before


@pytest.mark.parametrize("identity", [None, {"attempt_id": 12}])
def test_typed_receipt_requires_original_full_identity(identity):
    entry = event("model_receipt", 0)
    entry = entry.model_copy(update={"facts": {"usage": None, "identity": identity}})
    with pytest.raises(IntegrityError, match="usage_identity_invalid"):
        UsageLedger().apply(entry)
