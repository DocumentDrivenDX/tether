"""Deterministic tests for the opt-in shared-session qualification scenario."""

from __future__ import annotations

import asyncio
from threading import Event

import pytest
from tether_databricks_example.qualification_shared_session import (
    qualification_shared_session_settings,
)

from tether.client import Actor, Answer, ClientHost, ClientProtocol, PendingConfirmation
from tether.client.testing import fabricated_storage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_pending(client: ClientProtocol, session_id: str) -> PendingConfirmation:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        pending = await client.pending(session_id)
        if pending is not None:
            return pending
        await asyncio.sleep(0.02)
    pytest.fail("qualification_approval_not_persisted")


async def test_shared_session_effect_has_one_fixed_approval_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with (
        fabricated_storage() as storage,
        await ClientHost.open(qualification_shared_session_settings(storage)) as host,
    ):
        client = host.client(Actor(id="fabricated-owner"))
        session = await client.create(spec="qualification", title="Shared qualification")
        waiting = await client.send(
            session.id,
            "Run the Tether qualification scenario: shared-session.",
            request_id="qualification-shared-session",
            spec="qualification",
        )
        waiting_result = await asyncio.wait_for(waiting.result(), timeout=10)
        if waiting_result.stop_reason != "confirmation_required":
            pytest.fail("shared_session_approval_not_terminal")
        pending = await _wait_pending(client, session.id)
        if pending is None or pending.tool != "record_qualification":
            pytest.fail("shared_session_approval_missing")
        if pending.arguments != {"scenario": "shared-session"}:
            pytest.fail("shared_session_arguments_mismatch")
        result_handle = await client.confirm(
            session.id, pending.invocation_id, Answer(approved=True)
        )
        if result_handle is None:
            pytest.fail("shared_session_confirmation_not_accepted")
        result = await asyncio.wait_for(result_handle.result(), timeout=10)
        if result.stop_reason != "final":
            pytest.fail("shared_session_turn_not_final")
        if not any(
            message.content == "Fabricated Tether qualification effect completed."
            for message in result.messages
        ):
            pytest.fail("shared_session_effect_result_missing")
        marker = f"TETHER_QUALIFICATION_WORKER_STARTED {pending.invocation_id}"
        if capsys.readouterr().out.count(marker) != 1:
            pytest.fail("shared_session_worker_marker_missing")


async def test_unknown_effect_mode_blocks_only_after_approved_dispatch() -> None:
    started = Event()
    release = Event()
    async with (
        fabricated_storage() as storage,
        await ClientHost.open(
            qualification_shared_session_settings(storage, started=started, release_unknown=release)
        ) as host,
    ):
        client = host.client(Actor(id="fabricated-owner"))
        session = await client.create(spec="qualification", title="Unknown effect")
        accepted = await client.send(
            session.id,
            "Run the Tether qualification scenario: unknown-effect.",
            request_id="qualification-unknown-effect",
            spec="qualification",
        )
        accepted_result = await asyncio.wait_for(accepted.result(), timeout=10)
        if accepted_result.stop_reason != "confirmation_required":
            pytest.fail("unknown_effect_approval_not_terminal")
        pending = await _wait_pending(client, session.id)
        if pending is None or pending.arguments != {"scenario": "unknown-effect"}:
            pytest.fail("unknown_effect_approval_missing")
        result_handle = await client.confirm(
            session.id, pending.invocation_id, Answer(approved=True)
        )
        if result_handle is None:
            pytest.fail("unknown_effect_confirmation_not_accepted")
        try:
            started_waiter = asyncio.to_thread(started.wait, 10)
            if not await asyncio.wait_for(started_waiter, timeout=11):
                pytest.fail("unknown_effect_worker_not_started")
            effects = await client.effects(session.id)
            if len(effects.items) != 1 or effects.items[0].invocation_id != pending.invocation_id:
                pytest.fail("unknown_effect_intent_not_durable")
            if effects.items[0].tool != "record_qualification":
                pytest.fail("unknown_effect_intent_tool_mismatch")
            if await client.turn_result(session.id, result_handle.turn_id) is not None:
                pytest.fail("unknown_effect_completed_before_release")
        finally:
            release.set()
        finished = await asyncio.wait_for(result_handle.result(), timeout=10)
        if finished.stop_reason != "final":
            pytest.fail("released_unknown_effect_not_final")


async def test_physical_hold_has_margin_beyond_platform_stop_without_extending_shutdown():
    async with fabricated_storage() as storage:
        settings = qualification_shared_session_settings(storage)
        if settings.tools[0].timeout_seconds != 310:
            pytest.fail("qualification_physical_hold_margin_missing")
        if settings.client_limits.shutdown_timeout_seconds != 5:
            pytest.fail("qualification_shutdown_bound_changed")
        maximum = qualification_shared_session_settings(storage, unknown_hold_seconds=600)
        if maximum.tools[0].timeout_seconds != 610:
            pytest.fail("qualification_maximum_hold_not_bounded")


@pytest.mark.parametrize("hold", [14, 601, float("nan"), float("inf")])
async def test_unknown_effect_hold_remains_finite_and_bounded(hold):
    async with fabricated_storage() as storage:
        with pytest.raises(ValueError, match="qualification_unknown_hold_out_of_range"):
            qualification_shared_session_settings(storage, unknown_hold_seconds=hold)
