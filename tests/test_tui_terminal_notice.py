"""Bounded public-client reconstruction of terminal UI notices."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal, cast

import pytest
from pydantic import JsonValue
from textual.widgets import Input, RichLog
from tui_event_waits import (
    let_pilot_screen_wait_use_outer_owner,
    observe_session_shows,
    wait_for_event_or_operation,
    wait_for_state_or_operation,
)

from tether.client import (
    Client,
    ClientProtocol,
    Entry,
    EntryPage,
    ReadLimit,
    SessionSnapshot,
    UnknownSession,
)
from tether.context.testing import FixedEstimator
from tether.tui import DemoApp
from tether.tui.terminal import terminal_notice

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


class EntryReader:
    def __init__(self, pages: list[EntryPage] | Exception) -> None:
        self.pages = pages
        self.calls: list[tuple[int, int, bool]] = []

    async def entries(
        self,
        _session_id: str,
        *,
        start: int = 0,
        limit: int = 100,
        reverse: bool = False,
    ) -> EntryPage:
        self.calls.append((start, limit, reverse))
        if isinstance(self.pages, Exception):
            raise self.pages
        return self.pages[len(self.calls) - 1]


def entry(index: int, kind: str, **facts: JsonValue) -> Entry:
    return Entry(
        index=index,
        at=datetime.now(UTC),
        kind=kind,
        facts=facts,
    )


def snapshot(through_index: int) -> SessionSnapshot:
    return cast(SessionSnapshot, SimpleNamespace(through_index=through_index))


async def test_notice_reads_latest_terminal_from_snapshot_bounded_entries():
    reader = EntryReader(
        [
            EntryPage(
                items=(entry(8, "turn_ended", stop_reason="context_unavailable"),),
                next_start=None,
            )
        ]
    )

    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(8))

    assert notice == "[context_unavailable]"
    assert reader.calls == [(8, 32, True)]


async def test_newer_turn_start_suppresses_previous_failure_notice():
    reader = EntryReader([EntryPage(items=(entry(9, "turn_started"),), next_start=None)])

    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(9))

    assert notice is None


@pytest.mark.parametrize(
    "reason",
    ["future_stop_reason", None, {"unknown": "value"}],
    ids=["future", "missing", "structured"],
)
async def test_unknown_stop_metadata_is_never_rendered(reason: object):
    reader = EntryReader(
        [
            EntryPage(
                items=(entry(4, "turn_ended", stop_reason=cast(JsonValue, reason)),),
                next_start=None,
            )
        ]
    )

    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(4))

    assert notice == "[terminal_status_unavailable]"


async def test_entry_budget_exhaustion_uses_fixed_unavailable_notice():
    class ExhaustingReader:
        def __init__(self) -> None:
            self.calls = 0

        async def entries(self, _session_id, *, start, limit, reverse):
            self.calls += 1
            items = tuple(entry(start - offset, "extension_event") for offset in range(limit))
            return EntryPage(items=items, next_start=start - limit)

    reader = ExhaustingReader()
    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(400))

    assert notice == "[terminal_status_unavailable]"
    assert reader.calls == 8


async def test_empty_continuation_page_is_rejected_immediately():
    class EmptyProgressReader:
        def __init__(self) -> None:
            self.calls = 0

        async def entries(self, _session_id, *, start, limit, reverse):
            self.calls += 1
            return EntryPage(items=(), next_start=start - 1)

    reader = EmptyProgressReader()
    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(20))

    assert notice == "[terminal_status_unavailable]"
    assert reader.calls == 1


async def test_one_entry_pages_hit_page_budget_before_entry_budget():
    class OneEntryReader:
        def __init__(self) -> None:
            self.calls = 0

        async def entries(self, _session_id, *, start, limit, reverse):
            self.calls += 1
            return EntryPage(items=(entry(start, "extension_event"),), next_start=start - 1)

    reader = OneEntryReader()
    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(100))

    assert notice == "[terminal_status_unavailable]"
    assert reader.calls == 8


async def test_cross_page_gap_refuses_before_reading_an_older_terminal():
    class GappedReader:
        def __init__(self) -> None:
            self.calls = 0

        async def entries(self, _session_id, *, start, limit, reverse):
            self.calls += 1
            if self.calls == 1:
                return EntryPage(items=(entry(start, "extension_event"),), next_start=start - 2)
            return EntryPage(
                items=(entry(start, "turn_ended", stop_reason="final"),), next_start=None
            )

    reader = GappedReader()
    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(10))

    assert notice == "[terminal_status_unavailable]"
    assert reader.calls == 1


async def test_read_limit_after_nonterminal_page_is_unavailable():
    class LimitedReader:
        def __init__(self) -> None:
            self.calls = 0

        async def entries(self, _session_id, *, start, limit, reverse):
            self.calls += 1
            if self.calls == 1:
                return EntryPage(items=(entry(start, "extension_event"),), next_start=start - 1)
            raise ReadLimit("read_limit")

    reader = LimitedReader()
    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(8))

    assert notice == "[terminal_status_unavailable]"
    assert reader.calls == 2


async def test_consumed_cancellation_does_not_start_another_entry_read():
    stopped = False
    entered = asyncio.Event()
    blocked = asyncio.Event()

    class ConsumingReader:
        def __init__(self) -> None:
            self.calls = 0

        async def entries(self, _session_id, *, start, limit, reverse):
            nonlocal stopped
            self.calls += 1
            entered.set()
            try:
                await blocked.wait()
            except asyncio.CancelledError:
                stopped = True
            return EntryPage(items=(entry(start, "extension_event"),), next_start=start - 1)

    reader = ConsumingReader()
    task = asyncio.create_task(
        terminal_notice(
            cast(ClientProtocol, reader),
            "session",
            snapshot(10),
            current=lambda: not stopped,
        )
    )
    try:
        await wait_for_event_or_operation(entered, task)
    finally:
        task.cancel()
    notice = await task

    assert notice is None
    assert reader.calls == 1


@pytest.mark.parametrize("index", [-1, 6, 4], ids=["negative", "beyond-snapshot", "gap-in-prefix"])
async def test_out_of_prefix_entry_index_is_unavailable(index: int):
    reader = EntryReader(
        [EntryPage(items=(entry(index, "turn_ended", stop_reason="final"),), next_start=None)]
    )

    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(5))

    assert notice == "[terminal_status_unavailable]"


async def test_empty_end_page_for_nonempty_snapshot_is_unavailable():
    reader = EntryReader([EntryPage(items=(), next_start=None)])

    notice = await terminal_notice(cast(ClientProtocol, reader), "session", snapshot(5))

    assert notice == "[terminal_status_unavailable]"


async def test_partial_or_denied_entry_read_never_becomes_a_cached_notice():
    partial = EntryReader([EntryPage(items=(), next_start=None, partial=True)])
    denied = EntryReader(UnknownSession("unknown_session"))
    limited = EntryReader(ReadLimit("read_limit"))

    partial_notice = await terminal_notice(cast(ClientProtocol, partial), "session", snapshot(5))
    limited_notice = await terminal_notice(cast(ClientProtocol, limited), "session", snapshot(5))
    with pytest.raises(UnknownSession):
        await terminal_notice(cast(ClientProtocol, denied), "session", snapshot(5))

    assert partial_notice == "[terminal_status_unavailable]"
    assert limited_notice == "[terminal_status_unavailable]"


async def _send_context_failure(app: DemoApp, pilot) -> None:
    client = cast(Client, app.client)
    shown = observe_session_shows(app)
    client._service.engine.context.estimator = FixedEstimator(1_000_000)
    app.query_one("#message", Input).value = "Fabricated request"
    await pilot.click("#send")

    async def notice_visible():
        return any(
            "[context_unavailable]" in str(line)
            for line in app.query_one("#transcript", RichLog).lines
        )

    assert app._follower is not None
    await wait_for_state_or_operation(shown, notice_visible, app._follower)
    await pilot._wait_for_screen()


async def test_terminal_notice_survives_a_late_session_refresh(tmp_path):
    app = DemoApp(tmp_path, volatile=True)
    async with app.run_test() as pilot:
        await _send_context_failure(app, pilot)
        await app._show_session()

        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]

        assert sum("[context_unavailable]" in line for line in lines) == 1


async def test_new_turn_committing_during_entry_read_suppresses_old_notice(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    app = DemoApp(tmp_path, volatile=True)
    async with app.run_test() as pilot:
        await _send_context_failure(app, pilot)
        client = cast(Client, app.client)
        original_snapshot = client.snapshot
        original_entries = client.entries
        calls = 0
        committed_during_entry_read = False

        async def changed_snapshot(session_id: str):
            nonlocal calls, committed_during_entry_read
            calls += 1
            result = await original_snapshot(session_id)
            if committed_during_entry_read:
                return result.model_copy(update={"through_index": result.through_index + 1})
            return result

        async def commit_during_entry_read(
            session_id: str,
            *,
            tier: Literal["live", "archive"] | None = None,
            start: int = 0,
            limit: int = 100,
            reverse: bool = False,
        ) -> EntryPage:
            nonlocal committed_during_entry_read
            page = await original_entries(
                session_id, tier=tier, start=start, limit=limit, reverse=reverse
            )
            committed_during_entry_read = True
            return page

        monkeypatch.setattr(client, "snapshot", changed_snapshot)
        monkeypatch.setattr(client, "entries", commit_during_entry_read)
        await app._show_session()

        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]

        assert calls == 2
        assert committed_during_entry_read
        assert not any("[context_unavailable]" in line for line in lines)


async def test_session_switch_and_revoked_view_clear_old_notice(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    app = DemoApp(tmp_path, volatile=True)
    async with app.run_test() as pilot:
        await _send_context_failure(app, pilot)
        client = cast(Client, app.client)
        other = await client.create(title="Other fabricated session")
        app.session_id = other.id
        await app._show_session()
        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]
        assert not any("[context_unavailable]" in line for line in lines)

        app.session_id = cast(str, app.sessions[0].id)
        await app._show_session()
        monkeypatch.setattr(client, "snapshot", denied_snapshot)
        with pytest.raises(UnknownSession):
            await app._show_session()
        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]
        assert not any("[context_unavailable]" in line for line in lines)


async def denied_snapshot(session_id: str):
    del session_id
    raise UnknownSession("unknown_session")
