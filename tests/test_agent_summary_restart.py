"""Actual root reopen qualifies summary identity and content-before-provenance recovery."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_agent_summaries import Strategy, configuration, response, turn

from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, AgentSpec, CommitUnknown
from tether.types.storage import SessionInit, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def engine(limits, provider, script):
    return Engine(
        **configuration(),
        context=Strategy(),
        model=ScriptedModel(script),
        history=MessageHistoryAdapter(provider),
        history_provider=provider,
        model_limits=limits,
    )


async def open_state(path, now, limits, actor, session=None, script=None):
    root = await JsonlRoot.open(path)
    store = (await JsonlSessionStore.open(root, clock=lambda: now[0])).for_actor(actor)
    provider = await JsonlHistory.open(root)
    session = session or await store.create(actor.id, SessionInit())
    current = engine(
        limits,
        provider,
        script
        if script is not None
        else [response("Fabricated old answer"), response("Accepted summary", 7), response()],
    )
    spec = AgentSpec(id="demo", version="1", title="Demo", instructions="Fabricated", model="demo")
    return SimpleNamespace(
        root=root,
        store=store,
        provider=provider,
        engine=current,
        model=current.model,
        actor=actor,
        session=session,
        spec=spec,
    )


def lose_ack(root, kind):
    original = root._append

    def write(name, value, guard):
        offset = original(name, value, guard)
        journal = name == "journal" and isinstance(value, dict)
        entries = value.get("entries", []) if journal else []
        content = (
            name == "history"
            and isinstance(value, dict)
            and value.get("record", {}).get("kind") == "summary"
        )
        if (kind == "summary_content" and content) or (
            kind != "none" and journal and any(e.get("kind") == kind for e in entries)
        ):
            raise CommitUnknown("fabricated_summary_unknown")
        return offset

    return write


@pytest.mark.parametrize(
    "kind", ["none", "context_preparation", "summary_content", "compaction", "context_use"]
)
async def test_summary_unknown_ack_requires_reopen_and_only_accepted_provenance_reuses(
    tmp_path, limits, kind
):
    now, actor = [datetime.now(UTC)], Actor(id="fabricated-owner")
    h = await open_state(tmp_path, now, limits, actor)
    assert (await turn(h, "first"))[0].stop_reason == "final"
    h.root._append = lose_ack(h.root, kind)
    try:
        if kind == "none":
            assert (await turn(h, "second"))[0].stop_reason == "final"
        else:
            with pytest.raises(CommitUnknown, match="fabricated_summary_unknown"):
                await turn(h, "second")
            with pytest.raises(StoreError, match="requires_reopen"):
                await h.store.snapshot(h.session.id, actor.id)
    finally:
        assert await h.engine.aclose()
        assert await h.root.aclose()
    now[0] += timedelta(minutes=1)
    reused = kind in {"none", "compaction", "context_use"}
    script = [response()] if reused else [response("New summary", 7), response()]
    fresh = await open_state(tmp_path, now, limits, actor, h.session, script)
    try:
        result, entries = await turn(fresh, "fresh")
        assert result.stop_reason == "final"
        assert isinstance(fresh.model, ScriptedModel)
        assert [r.tags.purpose for r in fresh.model.requests] == (
            ["answer"] if reused else ["summary", "answer"]
        )
        assert [e.facts["ordinal"] for e in entries if e.kind == "context_preparation"] == [0, 1, 2]
        compact = [e for e in entries if e.kind == "compaction"]
        assert len(compact) == 1
        assert compact[0].facts["preparation_ordinal"] == (1 if reused else 2)
        assert (
            "Accepted summary" in repr(fresh.model.requests[-1].messages)
            if reused
            else "New summary" in repr(fresh.model.requests[-1].messages)
        )
    finally:
        assert await fresh.engine.aclose()
        assert await fresh.root.aclose()
