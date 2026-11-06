"""A borrowed presentation recovers state without repeating accepted work."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest
from textual.pilot import Pilot
from tui_event_waits import let_pilot_screen_wait_use_outer_owner, wait_for_event_or_operation

from tether.client import (
    Change,
    ClientProtocol,
    Lagged,
    ReplyReset,
    ReplyText,
    ResyncRequired,
    SessionSnapshot,
    SessionSummary,
    SessionUpdated,
    SessionView,
)
from tether.tui.feed import FeedPresentation, follow_client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


async def test_event_barrier_surfaces_operation_end_without_the_requested_state():
    event = asyncio.Event()

    async def finished():
        return None

    operation = asyncio.create_task(finished())
    with pytest.raises(AssertionError, match="operation_finished_before_state"):
        await wait_for_event_or_operation(event, operation)
    assert operation.result() is None


async def test_event_barrier_does_not_cancel_the_operation_when_state_wins():
    event = asyncio.Event()
    release = asyncio.Event()
    operation = asyncio.create_task(release.wait())
    event.set()
    await wait_for_event_or_operation(event, operation)
    assert not operation.done()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation


async def test_event_barrier_retains_its_observer_through_repeated_cancellation():
    class CancellationBarrier:
        def __init__(self):
            self.entered = asyncio.Event()
            self.cancellation_seen = asyncio.Event()
            self.release_cancellation = asyncio.Event()

        async def wait(self):
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancellation_seen.set()
                await self.release_cancellation.wait()
                raise

    state = CancellationBarrier()
    operation = asyncio.create_task(asyncio.Event().wait())
    waiting = asyncio.create_task(
        wait_for_event_or_operation(cast(asyncio.Event, state), operation)
    )
    try:
        await wait_for_event_or_operation(state.entered, waiting)
        waiting.cancel()
        await wait_for_event_or_operation(state.cancellation_seen, waiting)
        waiting.cancel()
        state.release_cancellation.set()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not operation.done()
    finally:
        state.release_cancellation.set()
        for task in (waiting, operation):
            if not task.done():
                task.cancel()
        await asyncio.gather(waiting, operation, return_exceptions=True)


async def test_textual_default_screen_wait_uses_outer_owner_and_preserves_explicit_values(
    monkeypatch,
):
    observed = []

    async def controlled(_pilot, **options):
        observed.append(options.get("timeout", 30.0))
        return True

    monkeypatch.setattr(Pilot, "_wait_for_screen", controlled)
    let_pilot_screen_wait_use_outer_owner(monkeypatch)
    pilot = cast(Pilot, object())
    await Pilot._wait_for_screen(pilot)
    await Pilot._wait_for_screen(pilot, timeout=30.0)
    await Pilot._wait_for_screen(pilot, timeout=9.0)
    assert observed == [None, 30.0, 9.0]


def summary():
    now = datetime.now(UTC)
    return SessionSummary(id="selected", owner="owner", created_at=now, updated_at=now)


@dataclass
class Presentation:
    trace: list[object] = field(default_factory=list)

    async def refresh(self):
        self.trace.append("refresh")

    async def show(self):
        self.trace.append("show")

    def callbacks(self):
        return FeedPresentation(
            selected=lambda: "selected",
            refresh=self.refresh,
            show=self.show,
            reset=lambda: self.trace.append("reset"),
            reply=lambda event: self.trace.append(event),
            reset_attempt=lambda event: self.trace.append(event),
            failed=lambda: self.trace.append("failed"),
        )


class GapClient:
    def __init__(self, change: Change, trace: list[object]):
        self.change = change
        self.trace = trace
        self.connected = asyncio.Event()
        self.release = asyncio.Event()
        self.cursors: list[int | None] = []
        self.snapshots: list[str] = []

    async def snapshot(self, session_id: str):
        assert self.trace[-1] == ("closed", self.cursors[-1])
        self.snapshots.append(session_id)
        self.trace.append("snapshot")
        return SessionSnapshot(
            seq=9007199254740993,
            through_index=1,
            view=SessionView(summary=summary()),
        )

    async def follow(self, *, session_id=None, since=None) -> AsyncIterator[Change]:
        self.cursors.append(since)
        try:
            if len(self.cursors) == 1:
                yield self.change
            else:
                self.connected.set()
                await self.release.wait()
        finally:
            self.trace.append(("closed", since))


@pytest.mark.parametrize(
    "change",
    [ResyncRequired(reason="gap"), ResyncRequired(reason="too_old"), Lagged(missed=2)],
    ids=["gap", "too-old", "lagged"],
)
async def test_gap_reloads_snapshot_and_history_then_reopens_at_exact_watermark(change):
    view = Presentation()
    client = GapClient(change, view.trace)
    task = asyncio.create_task(follow_client(cast(ClientProtocol, client), view.callbacks()))
    try:
        await wait_for_event_or_operation(client.connected, task)
        assert client.cursors == [None, 9007199254740993]
        assert client.snapshots == ["selected"]
        assert view.trace[:5] == ["reset", ("closed", None), "snapshot", "refresh", "show"]
        assert "failed" not in view.trace
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert view.trace[-1] == ("closed", 9007199254740993)


class BrokenClient:
    async def follow(self, **kwargs) -> AsyncIterator[Change]:
        raise RuntimeError("fabricated private response marker")
        yield


async def test_feed_failure_is_observed_and_only_a_fixed_failure_callback_runs():
    view = Presentation()
    await follow_client(cast(ClientProtocol, BrokenClient()), view.callbacks())
    assert view.trace == ["reset", "failed"]


class UpdatesClient:
    async def follow(self, **kwargs) -> AsyncIterator[Change]:
        yield ReplyText(
            session_id="selected", turn_id="turn", attempt_id="attempt", offset=0, text="partial"
        )
        yield ReplyReset(
            session_id="selected", turn_id="turn", attempt_id="attempt", reason="outage"
        )
        yield SessionUpdated(
            seq=1, summary=summary().model_copy(update={"status": "running"}), last_in_commit=True
        )
        yield SessionUpdated(seq=2, summary=summary(), last_in_commit=True)


async def test_external_terminal_update_reloads_committed_history_without_repeating_a_command():
    view = Presentation()
    await follow_client(cast(ClientProtocol, UpdatesClient()), view.callbacks())
    assert isinstance(view.trace[0], ReplyText)
    assert isinstance(view.trace[1], ReplyReset)
    assert view.trace[2:] == ["refresh", "refresh", "show", "reset", "failed"]


class RepeatedGapsClient(GapClient):
    async def follow(self, *, session_id=None, since=None) -> AsyncIterator[Change]:
        self.cursors.append(since)
        try:
            yield self.change
        finally:
            self.trace.append(("closed", since))


async def test_persistent_resync_refusal_is_bounded_and_closes_every_subscription():
    view = Presentation()
    client = RepeatedGapsClient(ResyncRequired(reason="too_old"), view.trace)
    await follow_client(cast(ClientProtocol, client), view.callbacks())
    assert len(client.cursors) == 6
    assert len([item for item in view.trace if isinstance(item, tuple)]) == 6
    assert view.trace[-1] == "failed"


@pytest.mark.parametrize("fail_close", [False, True], ids=["close", "close-error"])
async def test_shutdown_after_swallowed_read_cancellation_closes_without_next_receive(fail_close):
    class OneUpdateClient:
        def __init__(self):
            self.receives = 0
            self.closes = 0
            self.borrower_closes = 0

        async def follow(self, **kwargs):
            try:
                self.receives += 1
                yield SessionUpdated(
                    seq=1,
                    summary=summary(),
                    last_in_commit=True,
                )
                self.receives += 1
                await asyncio.Event().wait()
            finally:
                self.closes += 1
                if fail_close:
                    raise RuntimeError("fabricated close failure")

        async def aclose(self):
            self.borrower_closes += 1

    class SwallowingPresentation(Presentation):
        def __init__(self):
            super().__init__()
            self.read_entered = asyncio.Event()

        async def refresh(self):
            self.read_entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.trace.append("read-completed-during-stop")

    client = OneUpdateClient()
    view = SwallowingPresentation()
    stopping = asyncio.Event()
    task = asyncio.create_task(
        follow_client(
            cast(ClientProtocol, client),
            FeedPresentation(
                selected=lambda: "selected",
                refresh=view.refresh,
                show=view.show,
                reset=lambda: view.trace.append("reset"),
                reply=lambda event: view.trace.append(event),
                reset_attempt=lambda event: view.trace.append(event),
                failed=lambda: view.trace.append("failed"),
                stopping=stopping.is_set,
            ),
        )
    )
    await wait_for_event_or_operation(view.read_entered, task)
    stopping.set()
    task.cancel()
    await task
    assert client.receives == 1
    assert client.closes == 1
    assert client.borrower_closes == 0
    assert view.trace == ["read-completed-during-stop"]
