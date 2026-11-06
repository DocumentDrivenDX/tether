"""The rendered borrower recovers authoritative history and shows safe failures."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
from textual.widgets import RichLog, Static
from tui_event_waits import (
    let_pilot_screen_wait_use_outer_owner,
    observe_session_shows,
    wait_for_event_or_operation,
    wait_for_state_or_operation,
)

from tether.client import (
    Change,
    ClientProtocol,
    EntryPage,
    HistoryPage,
    Page,
    ResyncRequired,
    SessionSnapshot,
    SessionSummary,
    SessionUpdated,
    SessionView,
)
from tether.tui import ClientApp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


class PresentationClient:
    def __init__(self, *, fail: bool = False):
        now = datetime.now(UTC)
        self.summary = SessionSummary(
            id="selected", owner="owner", title="Fabricated", created_at=now, updated_at=now
        )
        self.release = asyncio.Event()
        self.connected = asyncio.Event()
        self.fail = fail
        self.changed = False
        self.allow_feed_reads = False
        self.cursors = []
        self.snapshots = []
        self.stream_closed = []
        self.close_calls = 0

    async def list(self):
        return Page[SessionSummary](items=(self.summary,), next=None)

    async def get(self, session_id):
        assert session_id == self.summary.id
        return SessionView(summary=self.summary)

    async def pending(self, session_id):
        assert session_id == self.summary.id

    async def history(self, session_id, *, start=0):
        assert session_id == self.summary.id
        text = "Reconstructed committed answer" if self.changed else "Original committed answer"
        return HistoryPage.model_validate(
            {
                "items": [
                    {
                        "entry_index": 0,
                        "value": {
                            "ref": {"provider": "fabricated", "key": "message", "sha256": "a" * 64},
                            "status": "ready",
                            "record": {
                                "kind": "message",
                                "message": {"role": "assistant", "content": text},
                            },
                        },
                    }
                ],
                "next_start": None,
            }
        )

    async def entries(self, session_id, *, tier=None, start=0, limit=100, reverse=False):
        assert session_id == self.summary.id
        return EntryPage(items=(), next_start=None)

    async def snapshot(self, session_id):
        if self.cursors and not self.allow_feed_reads:
            assert self.stream_closed[-1] == self.cursors[-1]
        self.snapshots.append(session_id)
        return SessionSnapshot(
            view=SessionView(summary=self.summary), through_index=0, seq=9007199254740993
        )

    async def follow(self, *, session_id=None, since=None) -> AsyncIterator[Change]:
        self.cursors.append(since)
        try:
            if len(self.cursors) == 1:
                await self.release.wait()
                if self.fail:
                    raise RuntimeError("fabricated hidden response marker")
                self.changed = True
                yield ResyncRequired(reason="gap")
            else:
                self.connected.set()
                await asyncio.Event().wait()
        finally:
            self.stream_closed.append(since)

    async def aclose(self):
        self.close_calls += 1


async def test_visible_tui_gap_reconstructs_committed_history_and_borrows_transport():
    client = PresentationClient()
    app = ClientApp(cast(ClientProtocol, client), session_id="selected")
    shown = observe_session_shows(app)
    async with app.run_test() as pilot:
        log = app.query_one("#transcript", RichLog)
        assert any("Original committed answer" in str(line) for line in log.lines)
        client.release.set()
        assert app._follower is not None
        await wait_for_event_or_operation(client.connected, app._follower)

        async def recovered_history_visible():
            return any(
                "Reconstructed committed answer" in str(line)
                for line in app.query_one("#transcript", RichLog).lines
            )

        await wait_for_state_or_operation(shown, recovered_history_visible, app._follower)
        await pilot._wait_for_screen()
        assert client.cursors == [9007199254740993, 9007199254740993]
        assert client.snapshots == ["selected"] * 6
        assert any("Reconstructed committed answer" in str(line) for line in log.lines)
        assert not any("Original committed answer" in str(line) for line in log.lines)
        assert client.stream_closed == [9007199254740993]
    assert client.stream_closed == [9007199254740993, 9007199254740993]
    assert client.close_calls == 0


class CommitDuringInitialPresentationClient(PresentationClient):
    def __init__(self):
        super().__init__()
        self.allow_feed_reads = True
        self.snapshot_calls = 0
        self.snapshots_before_change = 0

    async def snapshot(self, session_id):
        self.snapshot_calls += 1
        if not self.changed:
            self.snapshots_before_change += 1
        snapshot = await super().snapshot(session_id)
        if self.changed:
            return snapshot.model_copy(update={"through_index": 1, "seq": 9007199254740994})
        return snapshot

    async def history(self, session_id, *, start=0):
        page = await super().history(session_id, start=start)
        if not self.changed:
            # A commit lands after the initial snapshot while first history is
            # being rendered. The explicit cursor must make it replayable.
            self.changed = True
        return page

    async def follow(self, *, session_id=None, since=None) -> AsyncIterator[Change]:
        self.cursors.append(since)
        try:
            assert since == 9007199254740993
            yield SessionUpdated(
                seq=9007199254740994,
                summary=self.summary,
                last_in_commit=True,
            )
            self.connected.set()
            await asyncio.Event().wait()
        finally:
            self.stream_closed.append(since)


async def test_initial_snapshot_cursor_replays_commit_during_initial_render():
    client = CommitDuringInitialPresentationClient()
    app = ClientApp(cast(ClientProtocol, client), session_id="selected")
    shown = observe_session_shows(app)
    async with app.run_test() as pilot:
        log = app.query_one("#transcript", RichLog)
        assert app._follower is not None
        await wait_for_event_or_operation(client.connected, app._follower)

        async def committed_history_visible():
            return client.snapshot_calls == 5 and any(
                "Reconstructed committed answer" in str(line)
                for line in app.query_one("#transcript", RichLog).lines
            )

        await wait_for_state_or_operation(shown, committed_history_visible, app._follower)
        await pilot._wait_for_screen()
        assert client.snapshots_before_change == 2
        assert client.snapshot_calls == 5
        assert client.cursors[0] == 9007199254740993
        assert any("Reconstructed committed answer" in str(line) for line in log.lines)
        assert not any("Original committed answer" in str(line) for line in log.lines)
    assert client.close_calls == 0


async def test_visible_tui_failure_contains_no_upstream_exception_text():
    client = PresentationClient(fail=True)
    app = ClientApp(cast(ClientProtocol, client), session_id="selected")
    async with app.run_test() as pilot:
        client.release.set()
        assert app._follower is not None
        await app._follower
        await pilot._wait_for_screen()
        assert app.query_one("#connection", Static).content == (
            "Live updates unavailable. Reopen the client to reconnect."
        )
        assert all(
            "fabricated hidden response marker" not in str(line)
            for line in app.query_one("#transcript", RichLog).lines
        )
    assert client.close_calls == 0
