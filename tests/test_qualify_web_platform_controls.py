"""Fabricated readiness, terminal-state, and retained-thread controls."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import BinaryIO, cast

import pytest
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_web_platform_logs import (
    OwnedLogFollower,
    _validate_shutdown_report,
    restart_app,
    wait_app_state,
)
from qualify_web_platform_observation import (
    assert_active_turn,
    run_owned_thread,
    start_event_feed,
    stop_event_feed,
)

from tether.client import ClientProtocol, ReplyText, SessionSummary, SessionUpdated, TurnHandle


async def _wait_for_entry_or_operation_terminal(
    entered: asyncio.Event, operation: asyncio.Task[object]
) -> None:
    entered_task = asyncio.create_task(entered.wait())
    try:
        done, _ = await asyncio.wait({entered_task, operation}, return_when=asyncio.FIRST_COMPLETED)
        if operation in done and entered_task not in done:
            await operation
            raise AssertionError("worker terminated before entry witness")
        await entered_task
    finally:
        if not entered_task.done():
            entered_task.cancel()
        await asyncio.gather(entered_task, return_exceptions=True)


def test_worker_entry_wait_surfaces_operation_terminal_before_entry() -> None:
    async def run() -> None:
        entered = asyncio.Event()

        async def operation() -> str:
            return "finished before entry"

        task = asyncio.create_task(operation())
        with pytest.raises(AssertionError, match="terminated before entry"):
            await _wait_for_entry_or_operation_terminal(entered, task)
        assert task.result() == "finished before entry"

    asyncio.run(run())


def test_app_readiness_uses_observed_state_without_a_speed_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_web_platform_logs as logs

    states = iter(("STARTING", "RUNNING"))
    calls: list[tuple[str, ...]] = []

    def cli(_profile: str, *arguments: str, **_options: object) -> str:
        calls.append(arguments)
        state = next(states)
        compute = "ACTIVE" if state == "RUNNING" else "PENDING"
        return (
            '{"app_status":{"state":"' + state + '"},"compute_status":{"state":"' + compute + '"}}'
        )

    sleeps: list[int] = []
    monkeypatch.setattr(logs.time, "sleep", sleeps.append)
    wait_app_state("profile", "app", {("RUNNING", "ACTIVE")}, deadline=None, cli=cli)
    assert len(calls) == 2
    assert sleeps == [3]


def test_app_state_watchdog_exhaustion_is_closed_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_web_platform_logs as logs

    now = iter((10.0, 12.0))
    monkeypatch.setattr(logs.time, "monotonic", lambda: next(now))

    with pytest.raises(LiveQualificationIncomplete) as caught:
        wait_app_state(
            "profile",
            "app",
            {("RUNNING", "ACTIVE")},
            deadline=11.0,
            cli=lambda *_args, **_kwargs: '{"app_status":{},"compute_status":{}}',
        )
    assert caught.value.reason == "qualification_app_state_not_observed"


def test_owned_thread_observation_keeps_worker_until_terminal() -> None:
    release = threading.Event()

    async def run() -> None:
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def work() -> str:
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            return "done"

        task = asyncio.create_task(
            run_owned_thread(
                work,
                watchdog_seconds=0,
                reason="qualification_app_state_not_observed",
            )
        )
        try:
            await _wait_for_entry_or_operation_terminal(entered, task)
            assert not task.done()
            release.set()
            with pytest.raises(LiveQualificationIncomplete) as caught:
                await task
            assert caught.value.reason == "qualification_app_state_not_observed"
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_owned_thread_survives_repeated_cancellation() -> None:
    release = threading.Event()

    async def run() -> None:
        entered = asyncio.Event()
        finished = asyncio.Event()
        loop = asyncio.get_running_loop()

        def work() -> None:
            loop.call_soon_threadsafe(entered.set)
            release.wait()
            loop.call_soon_threadsafe(finished.set)

        task = asyncio.create_task(
            run_owned_thread(
                work,
                watchdog_seconds=None,
                reason="qualification_app_state_not_observed",
            )
        )
        try:
            await _wait_for_entry_or_operation_terminal(entered, task)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_log_follower_drains_and_retains_only_complete_report_lines() -> None:
    marker = 'TETHER_SHUTDOWN_REPORT {"complete":true}'
    process = subprocess.Popen(  # noqa: S603 - fixed local Python command
        [sys.executable, "-c", "print('private log line'); print(" + repr(marker) + ")"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    follower = OwnedLogFollower(process)
    process.wait()
    assert follower.finish(None, time.monotonic) == (marker,)


def test_restart_refuses_before_stop_without_log_observer_ack() -> None:
    actions: list[str] = []

    def cli(_profile: str, *arguments: str, **_options: object) -> str:
        actions.extend(arguments)
        return "{}"

    with pytest.raises(LiveQualificationIncomplete) as caught:
        restart_app("profile", "selected-app", None, cli)
    assert caught.value.reason == "qualification_log_observer_readiness_unavailable"
    assert actions == []


def test_log_observer_readiness_incomplete_has_cli_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import qualify_web_platform as qualifier

    async def incomplete(*_args: object, **_kwargs: object) -> None:
        raise LiveQualificationIncomplete("qualification_log_observer_readiness_unavailable")

    monkeypatch.setattr(qualifier, "profile_from_environment", lambda _values: object())
    monkeypatch.setattr(qualifier, "qualify", incomplete)
    monkeypatch.setattr(sys, "argv", ["qualify_web_platform", "--profile", "profile", "--restart"])
    assert qualifier.main() == 2
    assert capsys.readouterr().err.strip() == (
        "Live profile check incomplete: qualification_log_observer_readiness_unavailable"
    )


def test_shutdown_capture_error_overrides_valid_marker() -> None:
    error = LiveQualificationIncomplete("qualification_shutdown_log_not_observed")
    with pytest.raises(LiveQualificationIncomplete) as caught:
        _validate_shutdown_report(
            (
                'TETHER_SHUTDOWN_REPORT {"complete":true,"outstanding":[],'
                '"asgi_http_versions":["2"]}',
            ),
            error,
        )
    assert caught.value is error


def test_failed_log_capture_remains_failed_on_repeated_finish() -> None:
    class BrokenStream:
        def readline(self, _limit: int) -> bytes:
            raise OSError("fabricated stream read failure")

    class Process:
        stdout = cast(BinaryIO, BrokenStream())

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    follower = OwnedLogFollower(cast(subprocess.Popen[bytes], Process()))
    for _ in range(2):
        with pytest.raises(LiveQualificationIncomplete) as caught:
            follower.finish(None, time.monotonic)
        assert caught.value.reason == "qualification_shutdown_log_not_observed"


class _FeedClient:
    def __init__(self, event: object) -> None:
        self.event = event
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def follow(self, *, session_id: str, since: int | None) -> AsyncIterator[object]:
        assert session_id == "session"
        assert since == 0
        self.entered.set()
        now = datetime(2026, 11, 2, tzinfo=UTC)
        yield SessionUpdated(
            seq=1,
            summary=SessionSummary(
                id=session_id,
                owner="owner",
                title="feed-ready",
                created_at=now,
                updated_at=now,
                last_seq=1,
            ),
            last_in_commit=True,
        )
        await self.release.wait()
        yield self.event


def test_event_feed_start_and_stop_are_state_driven() -> None:
    client = _FeedClient(object())

    async def run() -> None:
        queue, task = await start_event_feed(
            cast(ClientProtocol, client),
            "session",
            since=0,
            expected_seq=1,
            expected_title="feed-ready",
            watchdog_seconds=None,
        )
        assert client.entered.is_set()
        assert queue.empty()
        client.release.set()
        assert await queue.get() is client.event
        await stop_event_feed(task)

    asyncio.run(run())


def test_event_feed_terminal_before_fresh_session_witness_is_incomplete() -> None:
    class IncompleteFeed:
        async def follow(self, **_kwargs: object) -> AsyncIterator[object]:
            yield SessionUpdated(
                seq=1,
                summary=SessionSummary(
                    id="another-session",
                    owner="owner",
                    created_at=datetime(2026, 11, 2, tzinfo=UTC),
                    updated_at=datetime(2026, 11, 2, tzinfo=UTC),
                ),
                last_in_commit=True,
            )

    async def run() -> None:
        with pytest.raises(LiveQualificationIncomplete) as caught:
            await start_event_feed(
                cast(ClientProtocol, IncompleteFeed()),
                "session",
                since=0,
                expected_seq=1,
                expected_title="feed-ready",
                watchdog_seconds=None,
            )
        assert caught.value.reason == "qualification_event_feed_not_started"

    asyncio.run(run())


def test_active_turn_requires_provisional_event_before_operation_terminal() -> None:
    event = ReplyText(
        session_id="session", turn_id="turn", attempt_id="attempt", offset=0, text="partial"
    )
    client = _FeedClient(event)
    operation_cancelled = asyncio.Event()

    class Handle:
        turn_id = "turn"

        async def result(self) -> object:
            try:
                await asyncio.Event().wait()
            finally:
                operation_cancelled.set()

    class Reads:
        async def turn_result(self, _session_id: str, _turn_id: str) -> None:
            return None

    async def run() -> None:
        queue, feed = await start_event_feed(
            cast(ClientProtocol, client),
            "session",
            since=0,
            expected_seq=1,
            expected_title="feed-ready",
            watchdog_seconds=None,
        )
        result_check = asyncio.create_task(
            assert_active_turn(
                cast(ClientProtocol, Reads()),
                "session",
                "turn",
                queue,
                feed,
                cast(TurnHandle, Handle()),
                None,
            )
        )
        await client.entered.wait()
        client.release.set()
        await result_check
        assert operation_cancelled.is_set()
        await stop_event_feed(feed)

    asyncio.run(run())


def test_terminal_turn_without_provisional_event_remains_behavior_failure() -> None:
    client = _FeedClient(object())
    completed = SimpleNamespace(stop_reason="final")

    class Handle:
        async def result(self) -> object:
            return completed

    class Reads:
        async def turn_result(self, _session_id: str, _turn_id: str) -> object:
            return completed

    async def run() -> None:
        queue, feed = await start_event_feed(
            cast(ClientProtocol, client),
            "session",
            since=0,
            expected_seq=1,
            expected_title="feed-ready",
            watchdog_seconds=None,
        )
        with pytest.raises(RuntimeError, match="qualification_turn_completed_before_restart"):
            await assert_active_turn(
                cast(ClientProtocol, Reads()),
                "session",
                "turn",
                queue,
                feed,
                cast(TurnHandle, Handle()),
                None,
            )
        await stop_event_feed(feed)

    asyncio.run(run())
