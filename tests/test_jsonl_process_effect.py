"""Real-process JSONL recovery distinguishes unsafe barriers from safe replay."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from jsonl_effect_process import EffectTool
from process_lifetime_controls import (
    OwnedProcess,
    RetainedCleanup,
    exercise_process_owner_controls,
    model_execution_timeouts,
)
from process_lifetime_controls import (
    receive as receive,
)
from process_lifetime_controls import (
    stop as stop,
)
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


@asynccontextmanager
async def reopen_harness(root_path: Path):
    async with RetainedCleanup() as cleanup:
        root = await JsonlRoot.open(root_path)
        cleanup.add(root.aclose, root.wait_closed)
        store = await JsonlSessionStore.open(root)
        cleanup.add(store.aclose, root.wait_closed)
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
        yield root, h, cleanup


async def start_effect_child(
    root_path: Path, effect_path: Path, cleanup: RetainedCleanup, *, safe: bool = False
) -> OwnedProcess:
    command = [
        sys.executable,
        "-u",
        str(Path(__file__).with_name("jsonl_effect_process.py")),
        str(root_path),
        str(effect_path),
    ]
    if safe:
        command.append("safe")
    child = await OwnedProcess.spawn(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    cleanup.add(child.close)
    return child


async def test_owned_process_failure_and_repeated_cancellation_are_retained():
    await exercise_process_owner_controls()


@pytest.mark.anyio
async def test_real_process_effect_is_not_replayed_after_jsonl_reopen(tmp_path):
    root_path = tmp_path / "journal"
    effect_path = tmp_path / "effects.log"
    created: dict[str, Any] = {}
    effect: dict[str, Any] = {}
    async with RetainedCleanup() as children:
        child = await start_effect_child(root_path, effect_path, children)
        created = await child.event()
        effect = await child.event()
        assert created["event"] == "created"
        assert effect["event"] == "effect_committed"
        assert effect_path.read_bytes() == b"fabricated-effect\n"
        await child.close()
        assert child.returncode is not None
        assert child.returncode < 0

    async with reopen_harness(root_path) as (_root, h, cleanup):
        store, actor = h.store, h.actor
        spy = DemoTool()
        run = build(h, tool=spy, model=ScriptedModel([proposal(call_id="new"), final()]))
        cleanup.add(
            run.engine.aclose,
            run.engine.wait_closed,
            run.engine.wait_observers_closed,
        )
        with model_execution_timeouts() as timeout_values:
            recovered = await store.recover(now=datetime.now(UTC) + timedelta(minutes=1))
            assert [(item.session_id, item.turn_id) for item in recovered] == [
                (created["session_id"], "killed-turn")
            ]
            entries = (
                await store.entries(created["session_id"], actor.id, start=0, limit=100)
            ).items
            assert any(
                entry.facts.get("invocation_id") == effect["invocation_id"]
                for entry in entries
                if entry.key and entry.key.startswith("intent:")
            )
            async with store.turn(created["session_id"], actor.id, turn_id="resumed-turn") as log:
                result = await run.engine.resume(actor, log)
            assert timeout_values
            assert all(value == h.limits.request_timeout_seconds for value in timeout_values)
        assert result.stop_reason == "final"
        assert spy.calls == []
        view = await store.get(created["session_id"], actor.id)
        assert view is not None
        assert view.summary.unresolved_effect_count == 1
        assert effect_path.read_bytes() == b"fabricated-effect\n"
        entries = (await store.entries(created["session_id"], actor.id, start=0, limit=100)).items
        assert any(entry.facts.get("reason") == "effect_unresolved" for entry in entries)


@pytest.mark.anyio
async def test_real_process_safe_effect_reuses_invocation_after_jsonl_reopen(tmp_path):
    root_path = tmp_path / "journal"
    effect_path = tmp_path / "effects.log"
    created: dict[str, Any] = {}
    effect: dict[str, Any] = {}
    record = b""
    async with RetainedCleanup() as children:
        child = await start_effect_child(root_path, effect_path, children, safe=True)
        created = await child.event()
        effect = await child.event()
        assert created["event"] == "created"
        assert effect["event"] == "effect_committed"
        record = (effect["invocation_id"] + "\n").encode()
        assert effect_path.read_bytes() == record
        await child.close()
        assert child.returncode is not None
        assert child.returncode < 0

    async with reopen_harness(root_path) as (_root, h, cleanup):
        store, actor = h.store, h.actor
        with model_execution_timeouts() as timeout_values:
            replay_tool = EffectTool(
                effect_path,
                timeout_values,
                h.limits.request_timeout_seconds,
                safe=True,
                hold=False,
            )
            run = build(h, safe=True, tool=replay_tool, model=ScriptedModel([final()]))
            replay_tool.tool_timeout_seconds = run.engine.limits.tool_timeout_seconds
            cleanup.add(
                run.engine.aclose,
                run.engine.wait_closed,
                run.engine.wait_observers_closed,
            )
            recovered = await store.recover(now=datetime.now(UTC) + timedelta(minutes=1))
            assert [(item.session_id, item.turn_id) for item in recovered] == [
                (created["session_id"], "killed-turn")
            ]
            async with store.turn(created["session_id"], actor.id, turn_id="resumed-turn") as log:
                result = await run.engine.resume(actor, log)
            assert timeout_values
            assert all(
                value in {h.limits.request_timeout_seconds, run.engine.limits.tool_timeout_seconds}
                for value in timeout_values
            )
            assert replay_tool.timeout_snapshot[-1] == run.engine.limits.tool_timeout_seconds
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
