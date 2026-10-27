"""Real-process JSONL recovery distinguishes unsafe barriers from safe replay."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonl_effect_process import EffectTool
from test_gated_execution import DemoTool, final, proposal
from test_resume_engine import build

from tether.history.jsonl import JsonlHistory
from tether.models.testing import ScriptedModel
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.types import ModelExecutionLimits
from tether.types.execution import Actor

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def receive(child: asyncio.subprocess.Process) -> dict:
    assert child.stdout is not None
    raw = await asyncio.wait_for(child.stdout.readline(), 20)
    assert raw, "effect child exited without a protocol event"
    return json.loads(raw)


async def stop(child: asyncio.subprocess.Process) -> None:
    if child.returncode is None:
        child.kill()
    await asyncio.wait_for(child.communicate(), 10)


async def reopen_harness(root_path: Path):
    root = await JsonlRoot.open(root_path)
    store = await JsonlSessionStore.open(root)
    provider = await JsonlHistory.open(root)
    actor = Actor(id="fabricated-owner")
    limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=2,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.001,
    )
    h = SimpleNamespace(store=store, provider=provider, actor=actor, engines=[], limits=limits)
    return root, h


async def start_effect_child(root_path: Path, effect_path: Path, *, safe: bool = False):
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("jsonl_effect_process.py")),
        str(root_path),
        str(effect_path),
    ]
    if safe:
        command.append("safe")
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def test_real_process_effect_is_not_replayed_after_jsonl_reopen(tmp_path):
    root_path = tmp_path / "journal"
    effect_path = tmp_path / "effects.log"
    child = await start_effect_child(root_path, effect_path)
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

    root, h = await reopen_harness(root_path)
    store, actor = h.store, h.actor
    spy = DemoTool()
    run = build(h, tool=spy, model=ScriptedModel([proposal(call_id="new"), final()]))
    try:
        recovered = await store.recover(now=datetime.now(UTC) + timedelta(minutes=1))
        assert [(item.session_id, item.turn_id) for item in recovered] == [
            (created["session_id"], "killed-turn")
        ]
        entries = (await store.entries(created["session_id"], actor.id, start=0, limit=100)).items
        assert any(
            entry.facts.get("invocation_id") == effect["invocation_id"]
            for entry in entries
            if entry.key and entry.key.startswith("intent:")
        )
        async with store.turn(created["session_id"], actor.id, turn_id="resumed-turn") as log:
            result = await run.engine.resume(actor, log)
        assert result.stop_reason == "final"
        assert spy.calls == []
        view = await store.get(created["session_id"], actor.id)
        assert view is not None
        assert view.summary.unresolved_effect_count == 1
        assert effect_path.read_bytes() == b"fabricated-effect\n"
        entries = (await store.entries(created["session_id"], actor.id, start=0, limit=100)).items
        assert any(entry.facts.get("reason") == "effect_unresolved" for entry in entries)
    finally:
        assert await run.engine.aclose()
        assert await store.aclose()
        assert await root.aclose()


async def test_real_process_safe_effect_reuses_invocation_after_jsonl_reopen(tmp_path):
    root_path = tmp_path / "journal"
    effect_path = tmp_path / "effects.log"
    child = await start_effect_child(root_path, effect_path, safe=True)
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

    root, h = await reopen_harness(root_path)
    store, actor = h.store, h.actor
    replay_tool = EffectTool(effect_path, safe=True, hold=False)
    run = build(h, safe=True, tool=replay_tool, model=ScriptedModel([final()]))
    try:
        recovered = await store.recover(now=datetime.now(UTC) + timedelta(minutes=1))
        assert [(item.session_id, item.turn_id) for item in recovered] == [
            (created["session_id"], "killed-turn")
        ]
        async with store.turn(created["session_id"], actor.id, turn_id="resumed-turn") as log:
            result = await run.engine.resume(actor, log)
        assert result.stop_reason == "final"
        assert [call[0] for call in replay_tool.calls] == [effect["invocation_id"]]
        assert effect_path.read_bytes() == record
        view = await store.get(created["session_id"], actor.id)
        assert view is not None
        assert view.summary.unresolved_effect_count == 0
        entries = (await store.entries(created["session_id"], actor.id, start=0, limit=100)).items
        invocation_id = effect["invocation_id"]
        assert len([entry for entry in entries if entry.key == "intent:" + invocation_id]) == 1
        assert len([entry for entry in entries if entry.key == "result:" + invocation_id]) == 1
    finally:
        assert await run.engine.aclose()
        assert await store.aclose()
        assert await root.aclose()
