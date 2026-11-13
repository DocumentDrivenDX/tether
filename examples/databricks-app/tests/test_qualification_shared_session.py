"""Deterministic tests for the opt-in shared-session qualification scenario."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import Event
from typing import Any, cast

import pytest
from context_lifetime_controls import wait_event_or_task
from functional_watchdog import MAX_FUNCTIONAL_WATCHDOG_MS
from selected_profiles_process import await_retained
from tether_databricks_example.qualification_shared_session import (
    qualification_shared_session_settings,
)

from tether.client import Actor, Answer, ClientHost
from tether.client.testing import fabricated_storage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _ThreadWorkerEntry(Event):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.loop = loop
        self.async_entry = asyncio.Event()

    def set(self) -> None:
        super().set()
        self.loop.call_soon_threadsafe(self.async_entry.set)


class _ExplicitRelease(Event):
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self.loop = loop
        self.wait_entered = asyncio.Event()
        self.requested_timeout: float | None = None

    def wait(self, timeout: float | None = None) -> bool:
        self.requested_timeout = timeout
        self.loop.call_soon_threadsafe(self.wait_entered.set)
        return super().wait()


@contextmanager
def _unarmed_engine_tool_deadlines() -> Iterator[list[float | None]]:
    from tether.engine import tools as tools_module

    actual_asyncio = asyncio
    captured: list[float | None] = []

    class Facade:
        def __getattr__(self, name: str) -> Any:
            return getattr(actual_asyncio, name)

        def timeout(self, seconds: float | None = None) -> asyncio.Timeout:
            captured.append(seconds)
            return actual_asyncio.timeout(None)

    original = tools_module.asyncio
    cast(Any, tools_module).asyncio = Facade()
    try:
        yield captured
    finally:
        cast(Any, tools_module).asyncio = original


def _ordinary_fake_model_settings(storage: Any, **options: Any) -> Any:
    settings = qualification_shared_session_settings(storage, **options)
    model_limits = settings.model_limits.model_copy(
        update={"request_timeout_seconds": MAX_FUNCTIONAL_WATCHDOG_MS / 1000.0}
    )
    return replace(settings, model_limits=model_limits)


async def _finish_unknown_worker(
    release: Event | None,
    result_task: asyncio.Task[Any] | None,
    host: ClientHost,
    storage: Any,
) -> None:
    errors: list[BaseException] = []
    if release is not None:
        release.set()
    if result_task is not None:
        try:
            await await_retained(result_task)
        except BaseException as error:
            errors.append(error)
    await _close_unknown_host(host, errors)
    await _close_unknown_storage(storage, errors)
    if errors:
        raise BaseExceptionGroup("qualification_worker_cleanup_failed", errors)


async def _close_unknown_host(host: ClientHost, errors: list[BaseException]) -> None:
    try:
        await host.aclose()
    except BaseException as error:
        errors.append(error)
    try:
        if not await host.wait_closed():
            errors.append(AssertionError("qualification_host_not_physically_closed"))
    except BaseException as error:
        errors.append(error)


async def _close_unknown_storage(storage: Any, errors: list[BaseException]) -> None:
    workers = tuple(storage.store.workers.active)
    try:
        await storage.store.aclose()
    except BaseException as error:
        errors.append(error)
    if workers:
        outcomes = await asyncio.gather(*workers, return_exceptions=True)
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    try:
        if not await storage.store.aclose():
            errors.append(AssertionError("qualification_storage_workers_not_settled"))
    except BaseException as error:
        errors.append(error)


async def _retain_unknown_worker_cleanup(
    release: Event | None,
    result_task: asyncio.Task[Any] | None,
    host: ClientHost,
    storage: Any,
    primary: BaseException | None,
) -> None:
    cleanup = asyncio.create_task(_finish_unknown_worker(release, result_task, host, storage))
    try:
        await await_retained(cleanup)
    except BaseException as error:
        if primary is None:
            raise
        raise BaseExceptionGroup(
            "qualification_test_and_cleanup_failed", [primary, error]
        ) from primary


async def _approve_unknown_effect(client: Any) -> tuple[Any, Any, Any]:
    session = await client.create(spec="qualification", title="Unknown effect")
    accepted = await client.send(
        session.id,
        "Run the Tether qualification scenario: unknown-effect.",
        request_id="qualification-unknown-effect",
        spec="qualification",
    )
    if (await accepted.result()).stop_reason != "confirmation_required":
        pytest.fail("unknown_effect_approval_not_terminal")
    pending = await client.pending(session.id)
    if (
        pending is None
        or pending.tool != "record_qualification"
        or pending.arguments != {"scenario": "unknown-effect"}
        or not pending.invocation_id
    ):
        pytest.fail("unknown_effect_approval_missing")
    result_handle = await client.confirm(session.id, pending.invocation_id, Answer(approved=True))
    if result_handle is None:
        pytest.fail("unknown_effect_confirmation_not_accepted")
    return session, pending, result_handle


async def _verify_unknown_effect_hold(
    client: Any,
    session: Any,
    pending: Any,
    result_handle: Any,
    result_task: asyncio.Task[Any],
    started: _ThreadWorkerEntry,
    release: _ExplicitRelease,
    selected_tool_deadlines: list[float | None],
) -> None:
    await wait_event_or_task(started.async_entry, result_task)
    await wait_event_or_task(release.wait_entered, result_task)
    if 310 not in selected_tool_deadlines:
        pytest.fail("qualification_tool_deadline_not_observed")
    if release.requested_timeout != 300:
        pytest.fail("qualification_hold_duration_not_observed")
    effects = await client.effects(session.id)
    if len(effects.items) != 1 or effects.items[0].invocation_id != pending.invocation_id:
        pytest.fail("unknown_effect_intent_not_durable")
    if effects.items[0].tool != "record_qualification":
        pytest.fail("unknown_effect_intent_tool_mismatch")
    if await client.turn_result(session.id, result_handle.turn_id) is not None:
        pytest.fail("unknown_effect_completed_before_release")
    release.set()
    if (await result_task).stop_reason != "final":
        pytest.fail("released_unknown_effect_not_final")


async def test_shared_session_effect_has_one_fixed_approval_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with fabricated_storage() as storage:
        host = await ClientHost.open(_ordinary_fake_model_settings(storage))
        try:
            client = host.client(Actor(id="fabricated-owner"))
            session = await client.create(spec="qualification", title="Shared qualification")
            waiting = await client.send(
                session.id,
                "Run the Tether qualification scenario: shared-session.",
                request_id="qualification-shared-session",
                spec="qualification",
            )
            waiting_result = await waiting.result()
            if waiting_result.stop_reason != "confirmation_required":
                pytest.fail("shared_session_approval_not_terminal")
            pending = await client.pending(session.id)
            if (
                pending is None
                or pending.tool != "record_qualification"
                or not pending.invocation_id
            ):
                pytest.fail("shared_session_approval_missing")
            if pending.arguments != {"scenario": "shared-session"}:
                pytest.fail("shared_session_arguments_mismatch")
            result_handle = await client.confirm(
                session.id, pending.invocation_id, Answer(approved=True)
            )
            if result_handle is None:
                pytest.fail("shared_session_confirmation_not_accepted")
            result = await result_handle.result()
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
        finally:
            await _retain_unknown_worker_cleanup(None, None, host, storage, sys.exception())


async def test_unknown_effect_mode_blocks_only_after_approved_dispatch() -> None:
    loop = asyncio.get_running_loop()
    started = _ThreadWorkerEntry(loop)
    release = _ExplicitRelease(loop)
    async with fabricated_storage() as storage:
        settings = _ordinary_fake_model_settings(
            storage,
            unknown_hold_seconds=300,
            started=started,
            release_unknown=release,
        )
        if settings.tools[0].timeout_seconds != 310:
            pytest.fail("qualification_unknown_effect_tool_deadline_changed")
        host = await ClientHost.open(settings)
        result_task: asyncio.Task[Any] | None = None
        try:
            client = host.client(Actor(id="fabricated-owner"))
            with _unarmed_engine_tool_deadlines() as selected_tool_deadlines:
                session, pending, result_handle = await _approve_unknown_effect(client)
                result_task = asyncio.create_task(result_handle.result())
                await _verify_unknown_effect_hold(
                    client,
                    session,
                    pending,
                    result_handle,
                    result_task,
                    started,
                    release,
                    selected_tool_deadlines,
                )
        finally:
            await _retain_unknown_worker_cleanup(
                release, result_task, host, storage, sys.exception()
            )


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
