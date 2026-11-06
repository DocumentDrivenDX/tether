"""Real-process PostgreSQL recovery distinguishes unsafe barriers from safe replay."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonl_effect_process import EffectTool
from postgres_support import database as database
from postgres_support import postgres_server as postgres_server
from test_gated_execution import DemoTool, final, proposal
from test_jsonl_process_effect import receive, stop
from test_postgres_processes import expired
from test_resume_engine import build

from tether.models.testing import ScriptedModel
from tether.stores.postgres import PostgresHistory
from tether.stores.postgres.store import PostgresSessionStore
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor, Resolution, ResolutionConflict

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def start_child(h, server, effect_path: Path, *, safe: bool = False):
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        str(Path(__file__).with_name("postgres_engine_effect_process.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert child.stdin is not None
    child.stdin.write(
        (
            json.dumps(
                {
                    "connection": server.get_uri(),
                    "schema": h.schema,
                    "owner": h.actor.id,
                    "effect_path": str(effect_path),
                    "safe": safe,
                }
            )
            + "\n"
        ).encode()
    )
    await child.stdin.drain()
    return child


def recovered_engine(h, root_store, *, safe=False, effect_path: Path | None = None):
    scoped = root_store.for_actor(h.actor)
    limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.001,
    )
    harness = SimpleNamespace(
        store=scoped,
        provider=PostgresHistory(h.root),
        actor=h.actor,
        engines=[],
        limits=limits,
    )
    if safe:
        assert effect_path is not None
        spy = EffectTool(effect_path, safe=True, hold=False)
    else:
        spy = DemoTool()
    responses = [final()] if safe else [proposal(call_id="new"), final()]
    run = build(harness, safe=safe, tool=spy, model=ScriptedModel(responses))
    return scoped, spy, run


async def resolve_sql_barrier(scoped, run, actor, session_id, invocation_id, effect_path):
    value = Resolution(invocation_id=invocation_id, outcome="completed", reason="operator_checked")
    async with scoped.turn(session_id, actor.id, turn_id="resolve") as log:
        await run.engine.resolve(actor, log, value)
    view = await scoped.get(session_id, actor.id)
    assert view is not None
    assert view.summary.unresolved_effect_count == 0
    resolutions = await scoped.lookup(
        session_id, actor.id, kind="key", value="resolution:" + invocation_id, limit=2
    )
    assert len(resolutions) == 1
    async with scoped.turn(session_id, actor.id, turn_id="resolve-retry") as log:
        await run.engine.resolve(actor, log, value)
    resolutions = await scoped.lookup(
        session_id, actor.id, kind="key", value="resolution:" + invocation_id, limit=2
    )
    assert len(resolutions) == 1
    conflict = value.model_copy(update={"outcome": "not_completed"})
    with pytest.raises(ResolutionConflict):
        async with scoped.turn(session_id, actor.id, turn_id="resolve-conflict") as log:
            await run.engine.resolve(actor, log, conflict)
    assert effect_path.read_bytes() == b"fabricated-effect\n"


async def test_real_engine_sql_effect_keeps_unknown_barrier_after_process_kill(
    database, postgres_server, tmp_path
):
    h = database
    effect_path = tmp_path / "fabricated-effects.log"
    child = await start_child(h, postgres_server, effect_path)
    try:
        created = await receive(child)
        effect = await receive(child)
        assert created["event"] == "created"
        assert effect["event"] == "effect_committed"
        assert effect_path.read_bytes() == b"fabricated-effect\n"
    finally:
        await stop(child)
    assert child.returncode is not None
    assert child.returncode < 0

    session_id = created["session_id"]
    session = await PostgresSessionStore(h.root).for_actor(h.actor).get(session_id, h.actor.id)
    assert session is not None
    await expired(h, session.summary)
    root_store = PostgresSessionStore(h.root, maintenance=Actor(id="maintenance"))
    recovered = await root_store.recover(now=datetime.now(UTC))
    assert [(item.session_id, item.turn_id) for item in recovered] == [(session_id, "killed-turn")]
    scoped, spy, run = recovered_engine(h, root_store)
    try:
        entries = (await scoped.entries(session_id, h.actor.id, start=0, limit=100)).items
        assert any(
            entry.facts.get("invocation_id") == effect["invocation_id"]
            for entry in entries
            if entry.key and entry.key.startswith("intent:")
        )
        async with scoped.turn(session_id, h.actor.id, turn_id="resumed-turn") as log:
            result = await run.engine.resume(h.actor, log)
        assert result.stop_reason == "final"
        assert spy.calls == []
        view = await scoped.get(session_id, h.actor.id)
        assert view is not None
        assert view.summary.unresolved_effect_count == 1
        assert effect_path.read_bytes() == b"fabricated-effect\n"
        entries = (await scoped.entries(session_id, h.actor.id, start=0, limit=100)).items
        assert any(entry.facts.get("reason") == "effect_unresolved" for entry in entries)
        await resolve_sql_barrier(
            scoped, run, h.actor, session_id, effect["invocation_id"], effect_path
        )
        assert spy.calls == []
    finally:
        assert await run.engine.aclose()


async def test_real_engine_sql_safe_effect_reuses_invocation_after_process_kill(
    database, postgres_server, tmp_path
):
    h = database
    effect_path = tmp_path / "fabricated-effects.log"
    child = await start_child(h, postgres_server, effect_path, safe=True)
    try:
        created = await receive(child)
        effect = await receive(child)
        assert created["event"] == "created"
        assert effect["event"] == "effect_committed"
        record = (effect["invocation_id"] + "\n").encode()
        assert effect_path.read_bytes() == record
    finally:
        await stop(child)
    assert child.returncode is not None
    assert child.returncode < 0

    session_id = created["session_id"]
    view = await PostgresSessionStore(h.root).for_actor(h.actor).get(session_id, h.actor.id)
    assert view is not None
    await expired(h, view.summary)
    root_store = PostgresSessionStore(h.root, maintenance=Actor(id="maintenance"))
    recovered = await root_store.recover(now=datetime.now(UTC))
    assert [(item.session_id, item.turn_id) for item in recovered] == [(session_id, "killed-turn")]
    scoped, tool, run = recovered_engine(h, root_store, safe=True, effect_path=effect_path)
    try:
        async with scoped.turn(session_id, h.actor.id, turn_id="resumed-turn") as log:
            result = await run.engine.resume(h.actor, log)
        assert result.stop_reason == "final"
        assert [call[0] for call in tool.calls] == [effect["invocation_id"]]
        assert effect_path.read_bytes() == record
        view = await scoped.get(session_id, h.actor.id)
        assert view is not None
        assert view.summary.unresolved_effect_count == 0
        entries = (await scoped.entries(session_id, h.actor.id, start=0, limit=100)).items
        invocation_id = effect["invocation_id"]
        assert len([entry for entry in entries if entry.key == "intent:" + invocation_id]) == 1
        assert len([entry for entry in entries if entry.key == "result:" + invocation_id]) == 1
    finally:
        assert await run.engine.aclose()
