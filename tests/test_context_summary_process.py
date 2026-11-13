"""Real process exit preserves accepted summary and context-use provenance."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from process_lifetime_controls import (
    OwnedProcess,
    RetainedCleanup,
    summary_execution_controls,
)
from test_agent_summaries import Strategy, configuration, response, turn

from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types.execution import Actor, AgentSpec

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def killed_at(
    path: Path, phase: str, cleanup: RetainedCleanup
) -> tuple[dict[str, str], dict[str, str]]:
    child = await OwnedProcess.spawn(
        sys.executable,
        "-u",
        str(Path(__file__).with_name("context_summary_process.py")),
        str(path),
        phase,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    cleanup.add(child.close)
    created = await child.event()
    pending = await child.event()
    assert created["event"] == "created"
    assert pending["event"] == (
        "summary_committed" if phase == "after_summary_commit" else "model_requested"
    )
    if phase != "after_summary_commit":
        assert pending["purpose"] == ("summary" if phase == "before_summary" else "answer")
    await child.close()
    assert child.returncode is not None
    assert child.returncode < 0, child.stderr_text
    return created, pending


@asynccontextmanager
async def open_recovery_state(path, now, limits, actor, session, script):
    async with RetainedCleanup() as cleanup:
        root = await JsonlRoot.open(path)
        cleanup.add(root.aclose, root.wait_closed)
        store = (await JsonlSessionStore.open(root, clock=lambda: now[0])).for_actor(actor)
        # The actor-bound store does not own the root's physical drain.
        cleanup.add(store.aclose)
        provider = await JsonlHistory.open(root)
        current = Engine(
            **configuration(),
            context=Strategy(),
            model=ScriptedModel(script),
            history=MessageHistoryAdapter(provider),
            history_provider=provider,
            model_limits=limits,
        )
        cleanup.add(
            current.aclose,
            current.wait_closed,
            current.wait_observers_closed,
        )
        spec = AgentSpec(
            id="demo",
            version="1",
            title="Demo",
            instructions="Fabricated",
            model="demo",
        )
        yield SimpleNamespace(
            root=root,
            store=store,
            provider=provider,
            engine=current,
            model=current.model,
            actor=actor,
            session=session,
            spec=spec,
        )


@pytest.mark.parametrize("phase", ["before_summary", "after_summary_commit", "after_context_use"])
async def test_process_kill_reuses_only_accepted_summary(tmp_path, limits, phase):
    created: dict[str, str] = {}
    pending: dict[str, str] = {}
    async with RetainedCleanup() as children:
        created, pending = await killed_at(tmp_path, phase, children)

    actor = Actor(id="fabricated-owner")
    now = [datetime.now(UTC) + timedelta(minutes=1)]
    session = SimpleNamespace(id=created["session_id"])
    script = [response("New summary", 7), response()] if phase == "before_summary" else [response()]
    async with open_recovery_state(tmp_path, now, limits, actor, session, script) as fresh:
        before = (await fresh.store.entries(session.id, actor.id, start=0, limit=100)).items
        compaction = [entry for entry in before if entry.kind == "compaction"]
        assert len(compaction) == (0 if phase == "before_summary" else 1)
        if phase == "after_context_use":
            assert any(
                entry.kind == "context_use"
                and entry.facts.get("model_call_id") == pending["model_call_id"]
                for entry in before
            )
        if phase != "after_summary_commit":
            assert not any(
                entry.kind == "model_call"
                and entry.facts.get("model_call_id") == pending["model_call_id"]
                for entry in before
            )
        with summary_execution_controls(fresh.engine.context.config) as (
            model_timeouts,
            context_deadlines,
        ):
            result, after = await turn(fresh, "fresh")
            assert model_timeouts
            assert context_deadlines
            context_remaining = [deadline - 1000.0 for deadline in context_deadlines]
            assert context_remaining
            assert all(value == context_remaining[0] for value in context_remaining)
            assert all(
                value == fresh.engine.context.config.timeout_seconds for value in context_remaining
            )
            model_requests = fresh.model.requests
            assert model_requests
            assert all(request.timeout_seconds is None for request in model_requests)
            selected_model_timeouts = [
                min(
                    request.timeout_seconds or limits.request_timeout_seconds,
                    limits.request_timeout_seconds,
                )
                for request in model_requests
            ]
            assert all(value == selected_model_timeouts[0] for value in selected_model_timeouts)
            assert all(value == selected_model_timeouts[0] for value in model_timeouts)
        assert result.stop_reason == "final"
        assert isinstance(fresh.model, ScriptedModel)
        assert [request.tags.purpose for request in fresh.model.requests] == (
            ["summary", "answer"] if phase == "before_summary" else ["answer"]
        )
        assert ("New summary" if phase == "before_summary" else "Accepted summary") in repr(
            fresh.model.requests[-1].messages
        )
        completed = [entry for entry in after if entry.kind == "compaction"]
        assert len(completed) == 1
        if phase == "after_context_use":
            assert completed == compaction
