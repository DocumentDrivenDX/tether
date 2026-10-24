"""Actual Agent recovery from committed prefixes, with no cached runner."""

from types import SimpleNamespace

import pytest
from test_gated_execution import DemoTool, final, proposal

from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.execution import Actor, AgentSpec, Answer
from tether.types.storage import SessionInit

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["memory", "jsonl"])
async def recovery(request, tmp_path, limits):
    root = None if request.param == "memory" else await JsonlRoot.open(tmp_path)
    files = root or MemoryFiles()
    h = SimpleNamespace(
        store=await JsonlSessionStore.open(files),
        provider=await JsonlHistory.open(files),
        actor=Actor(id="owner"),
        engines=[],
        limits=limits,
    )
    yield h
    for engine in h.engines:
        assert await engine.aclose()
    assert await h.store.aclose()
    if root is not None:
        assert await root.aclose()


def build(h, *, safe=False, steps=20, tool=None, model=None, spec=None):
    tool = tool or DemoTool()
    tool.replay = "safe" if safe else "unsafe"
    spec = spec or AgentSpec(
        id="demo",
        version="1",
        title="Demo",
        instructions="Fabricated",
        model="demo",
        tools=(tool.name,),
        max_steps=steps,
    )
    model = model or ScriptedModel([proposal(), final()])
    engine = Engine(
        model=model,
        history=MessageHistoryAdapter(h.provider),
        history_provider=h.provider,
        model_limits=h.limits,
        tools=(tool,),
        specs=(spec,),
    )
    h.engines.append(engine)
    return SimpleNamespace(engine=engine, tool=tool, spec=spec, model=model)


def triggered(stage, entries):
    if stage == "input":
        return any(e.kind == "turn_started" for e in entries)
    if stage == "proposal":
        return any(e.call_manifest for e in entries)
    if stage in {"intent", "result"}:
        return any(e.key and e.key.startswith(stage + ":") for e in entries)
    if stage == "pending":
        return any(
            e.kind == "confirmation" and e.facts.get("state") == "requested" for e in entries
        )
    return any(e.kind == "turn_ended" and e.facts.get("stop_reason") == "final" for e in entries)


async def interrupted(h, run, stage, monkeypatch):
    session = await h.store.create(
        h.actor.id, SessionInit(settings={"approval": "manual" if stage == "pending" else "auto"})
    )
    original = h.store.append
    fired = False

    async def crash(sid, owner, generation, entries):
        nonlocal fired
        if not fired and triggered(stage, entries):
            fired = True
            if stage not in {"result", "final"}:
                await original(sid, owner, generation, entries)
            raise RuntimeError("fabricated_process_loss")
        return await original(sid, owner, generation, entries)

    monkeypatch.setattr(h.store, "append", crash)
    with pytest.raises(RuntimeError, match="fabricated_process_loss"):
        async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
            await run.engine.run_turn(h.actor, log, "Fabricated", spec=run.spec)
    monkeypatch.setattr(h.store, "append", original)
    assert fired
    assert (await h.store.get(session.id, h.actor.id)).summary.resumable
    return session.id


@pytest.mark.parametrize(
    ("stage", "safe", "effects"),
    [
        ("input", False, 1),
        ("proposal", False, 1),
        ("intent", False, 0),
        ("intent", True, 1),
        ("result", False, 1),
        ("result", True, 2),
        ("final", False, 1),
    ],
)
async def test_fresh_agent_recovers_prefix_with_stable_invocation_ids(
    recovery, monkeypatch, stage, safe, effects
):
    h = recovery
    run = build(h, safe=safe)
    sid = await interrupted(h, run, stage, monkeypatch)
    before = (await h.store.entries(sid, h.actor.id, start=0, limit=1000)).items
    prior_calls = len(run.model.requests)
    fresh = build(h, safe=safe, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "final"
    assert result.turn_id == "continued"
    assert len(run.tool.calls) == effects
    entries = (await h.store.entries(sid, h.actor.id, start=0, limit=1000)).items
    assert entries[: len(before)] == before
    assert (await h.store.get(sid, h.actor.id)).summary.turn_count == 2
    if stage == "final":
        assert len(run.model.requests) == prior_calls
    if safe and stage in {"intent", "result"}:
        intent = next(e for e in entries if e.key and e.key.startswith("intent:"))
        assert {inv for inv, _ in run.tool.calls} == {intent.facts["invocation_id"]}
        assert sum(e.key == intent.key for e in entries) == 1
    if not safe and stage in {"intent", "result"}:
        assert (await h.store.get(sid, h.actor.id)).summary.unresolved_effect_count == 1
        assert any(e.facts.get("reason") == "outcome_unknown" for e in entries)


async def test_interrupted_pending_approval_moves_to_new_phase_without_approval(
    recovery, monkeypatch
):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "pending", monkeypatch)
    old = (await h.store.get(sid, h.actor.id)).pending
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "confirmation_required"
    assert result.pending[0].invocation_id == old.invocation_id
    assert not run.tool.calls
    assert len(run.model.requests) == 1
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        completed = await fresh.engine.answer(
            h.actor, log, old.invocation_id, Answer(approved=True)
        )
    assert completed.stop_reason == "final"
    assert len(run.tool.calls) == 1
    assert (await h.store.get(sid, h.actor.id)).summary.turn_count == 2


async def test_model_step_budget_is_cumulative_across_resume(recovery, monkeypatch):
    h = recovery
    run = build(h, steps=1)
    sid = await interrupted(h, run, "proposal", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "step_limit"
    assert len(run.model.requests) == 1
    assert len(run.tool.calls) == 1


async def test_resume_chain_limit_survives_post_acceptance_setup_failures(recovery, monkeypatch):
    from tether.types.execution import ResumeLimit

    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "input", monkeypatch)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    accepted = []

    def fail_setup(*args):
        raise RuntimeError("fabricated_setup_failure")

    monkeypatch.setattr(fresh.engine, "_create", fail_setup)
    for attempt in range(3):
        with pytest.raises(RuntimeError, match="fabricated_setup_failure"):
            async with h.store.turn(sid, h.actor.id, turn_id="continued:" + str(attempt)) as log:
                await fresh.engine.resume(h.actor, log, accepted=lambda: accepted.append(True))
    before = await h.store.snapshot(sid, h.actor.id)
    with pytest.raises(ResumeLimit):
        async with h.store.turn(sid, h.actor.id, turn_id="over-limit") as log:
            await fresh.engine.resume(h.actor, log)
    assert await h.store.snapshot(sid, h.actor.id) == before
    assert len(accepted) == 3
    assert before.view.summary.turn_count == 4
    assert not run.model.requests
    assert not run.tool.calls


async def test_model_hidden_safe_invocation_can_resume_without_exposure(recovery, monkeypatch):
    h = recovery
    run = build(h, safe=True)
    sid = await interrupted(h, run, "intent", monkeypatch)
    run.tool.model_visible = False
    fresh = build(h, safe=True, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "final"
    assert len(run.tool.calls) == 1
    assert not run.model.requests[-1].tools


async def test_prepared_unaccepted_model_attempt_consumes_resume_step_budget(recovery, monkeypatch):
    h = recovery
    run = build(h, steps=1)
    session = await h.store.create(h.actor.id, SessionInit())
    original = h.store.append

    async def fail_source(sid, owner, generation, entries):
        if any(e.call_manifest for e in entries):
            raise RuntimeError("fabricated_source_commit_failure")
        return await original(sid, owner, generation, entries)

    monkeypatch.setattr(h.store, "append", fail_source)
    with pytest.raises(RuntimeError):
        async with h.store.turn(session.id, h.actor.id, turn_id="original") as log:
            await run.engine.run_turn(h.actor, log, "Fabricated", spec=run.spec)
    monkeypatch.setattr(h.store, "append", original)
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(session.id, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "step_limit"
    assert len(run.model.requests) == 1
    assert not run.tool.calls
    assert not fresh.engine._runs[session.id].gov.usage.usage_complete


@pytest.mark.parametrize("stage", ["proposal", "intent"])
@pytest.mark.parametrize("safe", [False, True])
async def test_recovered_unknown_result_remains_resolvable(recovery, monkeypatch, stage, safe):
    from tether.types.execution import Resolution

    h = recovery
    run = build(h, safe=safe)
    sid = await interrupted(h, run, stage, monkeypatch)

    def fail(ctx, args):
        raise RuntimeError("fabricated_effect_failure")

    run.tool.run = fail
    fresh = build(h, safe=safe, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        await fresh.engine.resume(h.actor, log)
    entries = (await h.store.entries(sid, h.actor.id, start=0, limit=1000)).items
    intent = next(e for e in entries if e.key and e.key.startswith("intent:"))
    invocation_id = intent.facts["invocation_id"]
    result = next(e for e in entries if e.key == "result:" + invocation_id)
    assert intent.turn_id == result.turn_id == "original"
    resolver = build(h, safe=safe, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="resolve") as log:
        await resolver.engine.resolve(
            h.actor,
            log,
            Resolution(invocation_id=invocation_id, outcome="not_completed", reason="checked"),
        )
    assert (await h.store.get(sid, h.actor.id)).summary.unresolved_effect_count == 0
    async with h.store.turn(sid, h.actor.id, turn_id="close") as log:
        await resolver.engine.close_session(h.actor, log)
    assert (await h.store.get(sid, h.actor.id)).summary.status == "closed"


async def test_changed_pending_definition_closes_approval_without_execution(recovery, monkeypatch):
    h = recovery
    run = build(h)
    sid = await interrupted(h, run, "pending", monkeypatch)
    run.tool.description += " Changed"
    fresh = build(h, tool=run.tool, model=run.model, spec=run.spec)
    async with h.store.turn(sid, h.actor.id, turn_id="continued") as log:
        result = await fresh.engine.resume(h.actor, log)
    assert result.stop_reason == "final"
    assert not run.tool.calls
    view = await h.store.get(sid, h.actor.id)
    assert view.pending is None
    entries = (await h.store.entries(sid, h.actor.id, start=0, limit=1000)).items
    assert any(e.kind == "confirmation" and e.facts.get("state") == "invalid" for e in entries)
    assert any(e.facts.get("reason") == "approval_invalid" for e in entries)
