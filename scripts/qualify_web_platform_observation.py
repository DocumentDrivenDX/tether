"""State-driven readiness and retained thread ownership for web qualification."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_observation import (
    LiveObservationPolicy,
    observe_until,
    use_live_observation_policy,
)
from selected_profiles_process import await_retained

from tether.client import ClientProtocol, ReplyText, SessionUpdated, TurnHandle

R = TypeVar("R")
T = TypeVar("T")


async def run_owned_thread(
    operation: Callable[[], R],
    *,
    watchdog_seconds: float | None,
    reason: str,
) -> R:
    LiveObservationPolicy(watchdog_seconds)
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        if watchdog_seconds is not None:
            done, _ = await asyncio.wait({task}, timeout=watchdog_seconds)
            if task not in done:
                await await_retained(task)
                raise LiveQualificationIncomplete(reason)
        return await await_retained(task)
    except asyncio.CancelledError:

        async def settle_worker() -> None:
            await asyncio.gather(task, return_exceptions=True)

        await await_retained(asyncio.create_task(settle_worker()))
        raise


async def observe_operation(
    operation: Awaitable[R], *, watchdog_seconds: float | None, reason: str
) -> R:
    with use_live_observation_policy(LiveObservationPolicy(watchdog_seconds)):
        async with observe_until(reason):
            return await operation


async def start_event_feed(
    client: ClientProtocol,
    session_id: str,
    *,
    since: int,
    expected_seq: int,
    expected_title: str,
    watchdog_seconds: float | None,
) -> tuple[asyncio.Queue[object], asyncio.Task[None]]:
    if expected_seq <= since:
        raise LiveQualificationIncomplete("qualification_event_feed_not_started")
    queue: asyncio.Queue[object] = asyncio.Queue()

    async def collect() -> None:
        async for event in client.follow(session_id=session_id, since=since):
            await queue.put(event)

    task = asyncio.create_task(collect())
    ready = asyncio.create_task(queue.get())
    try:
        with use_live_observation_policy(LiveObservationPolicy(watchdog_seconds)):
            async with observe_until("qualification_event_feed_not_started"):
                while True:
                    done, _ = await asyncio.wait({ready, task}, return_when=asyncio.FIRST_COMPLETED)
                    if task in done:
                        raise LiveQualificationIncomplete("qualification_event_feed_not_started")
                    event = ready.result()
                    if (
                        isinstance(event, SessionUpdated)
                        and event.summary.id == session_id
                        and event.seq == expected_seq
                        and event.summary.title == expected_title
                        and event.last_in_commit
                    ):
                        if task.done():
                            raise LiveQualificationIncomplete(
                                "qualification_event_feed_not_started"
                            )
                        return queue, task
                    ready = asyncio.create_task(queue.get())
    except BaseException:
        await stop_event_feed(task)
        raise
    finally:
        if not ready.done():
            ready.cancel()
        await _settle(ready)


async def assert_active_turn(
    client: ClientProtocol,
    session_id: str,
    turn_id: str,
    queue: asyncio.Queue[object],
    feed_task: asyncio.Task[None],
    handle: TurnHandle,
    watchdog_seconds: float | None,
) -> None:
    operation = asyncio.create_task(handle.result())
    try:
        with use_live_observation_policy(LiveObservationPolicy(watchdog_seconds)):
            async with observe_until("qualification_provisional_reply_not_observed"):
                while True:
                    event = await _next_event_or_terminal(queue, feed_task, operation)
                    if isinstance(event, ReplyText) and event.turn_id == turn_id and event.text:
                        break
        if operation.done():
            operation.result()
            raise RuntimeError("qualification_turn_completed_before_restart")
        if await client.turn_result(session_id, turn_id) is not None:
            raise RuntimeError("qualification_turn_completed_before_restart")
        if operation.done():
            operation.result()
            raise RuntimeError("qualification_turn_completed_before_restart")
    finally:
        if not operation.done():
            await _cancel_and_join(operation)


async def _next_event_or_terminal(
    queue: asyncio.Queue[object],
    feed_task: asyncio.Task[None],
    operation: asyncio.Task[object],
) -> object:
    event = asyncio.create_task(queue.get())
    try:
        done, _ = await asyncio.wait(
            {event, feed_task, operation}, return_when=asyncio.FIRST_COMPLETED
        )
        if event in done:
            return event.result()
        if operation in done:
            operation.result()
            raise RuntimeError("qualification_turn_completed_before_restart")
        if feed_task.cancelled():
            raise LiveQualificationIncomplete("qualification_event_feed_not_started")
        error = feed_task.exception()
        if error is not None:
            raise error
        raise LiveQualificationIncomplete("qualification_event_feed_not_started")
    finally:
        if not event.done():
            await _cancel_and_join(event)


async def stop_event_feed(task: asyncio.Task[None]) -> None:
    if not task.done():
        task.cancel()
    await _settle(task)


async def _cancel_and_join(task: asyncio.Task[T]) -> None:
    if not task.done():
        task.cancel()
    await _settle(task)


async def _settle(task: asyncio.Task[T]) -> None:
    async def join() -> None:
        await asyncio.gather(task, return_exceptions=True)

    await await_retained(asyncio.create_task(join()))


__all__ = [
    "assert_active_turn",
    "observe_operation",
    "run_owned_thread",
    "start_event_feed",
    "stop_event_feed",
]
