"""Real process exit preserves accepted summary and context-use provenance."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_agent_summaries import response, turn
from test_agent_summary_restart import open_state

from tether.models.testing import ScriptedModel
from tether.types.execution import Actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def receive(child: asyncio.subprocess.Process) -> dict[str, str]:
    assert child.stdout is not None
    raw = await asyncio.wait_for(child.stdout.readline(), 20)
    assert raw, "context child exited without a protocol event"
    return json.loads(raw)


async def killed_at(path: Path, phase: str) -> tuple[dict[str, str], dict[str, str]]:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        str(Path(__file__).with_name("context_summary_process.py")),
        str(path),
        phase,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        created = await receive(child)
        pending = await receive(child)
        assert created["event"] == "created"
        assert pending["event"] == (
            "summary_committed" if phase == "after_summary_commit" else "model_requested"
        )
        if phase != "after_summary_commit":
            assert pending["purpose"] == ("summary" if phase == "before_summary" else "answer")
    finally:
        if child.returncode is None:
            child.kill()
        _, errors = await asyncio.wait_for(child.communicate(), 10)
    assert child.returncode is not None, errors.decode()
    assert child.returncode < 0, errors.decode()
    return created, pending


@pytest.mark.parametrize("phase", ["before_summary", "after_summary_commit", "after_context_use"])
async def test_process_kill_reuses_only_accepted_summary(tmp_path, limits, phase):
    created, pending = await killed_at(tmp_path, phase)

    actor = Actor(id="fabricated-owner")
    now = [datetime.now(UTC) + timedelta(minutes=1)]
    session = SimpleNamespace(id=created["session_id"])
    script = [response("New summary", 7), response()] if phase == "before_summary" else [response()]
    fresh = await open_state(tmp_path, now, limits, actor, session, script)
    try:
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
        result, after = await turn(fresh, "fresh")
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
    finally:
        assert await fresh.engine.aclose()
        assert await fresh.root.aclose()
