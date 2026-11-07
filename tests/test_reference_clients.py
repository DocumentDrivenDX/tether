"""Offline reference clients exercise public session and approval operations."""

from __future__ import annotations

from typing import cast

import pytest
from textual.widgets import Button, Input, RichLog
from tui_event_waits import (
    let_pilot_screen_wait_use_outer_owner,
    observe_session_shows,
    wait_for_state_or_operation,
)

from tether.cli.command import execute, parser
from tether.cli.demo import open_demo
from tether.client import Client
from tether.context.testing import FixedEstimator
from tether.tui import DemoApp
from tether.types.execution import Answer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


async def test_demo_uses_durable_client_and_requires_exact_approval(tmp_path):
    async with open_demo(tmp_path) as (app, actor, tool):
        client = app.client(actor)
        session = await client.create(ref="demo", settings={"approval": "manual"})
        result = await (await client.send(session.id, "Fabricated request")).result()
        assert result.stop_reason == "confirmation_required"
        assert len(result.pending) == 1
        assert tool.recorded == []

    async with open_demo(tmp_path, continuation=True) as (app, actor, tool):
        client = app.client(actor)
        pending = await client.pending(session.id)
        assert pending is not None
        handle = await client.confirm(session.id, pending.invocation_id, Answer(approved=True))
        assert handle is not None
        result = await handle.result()
        assert result.stop_reason == "final"
        assert tool.recorded == ["seedling"]
        assert (await client.get(session.id)).summary.status == "idle"
        assert (await client.history(session.id)).items


async def test_cli_lists_accepted_demo_session(tmp_path, capsys):
    async with open_demo(tmp_path) as (app, actor, _tool):
        created = await app.client(actor).create(ref="demo", title="Fabricated")
    await execute(parser().parse_args(["--root", str(tmp_path), "list"]))
    output = capsys.readouterr().out
    assert created.id in output
    assert "Fabricated" in output


async def test_terminal_ui_shows_approval_then_final(tmp_path):
    """@covers US-065-AC7: embedded counterpart of the HTTP-injected terminal journey."""
    app = DemoApp(tmp_path)
    shown = observe_session_shows(app)
    async with app.run_test() as pilot:
        assert app.session_id is not None
        session_id = app.session_id
        assert len(app.sessions) == 1
        field = app.query_one("#message", Input)
        field.value = "Fabricated request"
        await pilot.click("#send")
        client = app.client
        assert client is not None
        follower = app._follower
        assert follower is not None

        async def approval_ready():
            pending = await client.pending(session_id)
            return pending is not None and not app.query_one("#approve", Button).disabled

        await wait_for_state_or_operation(shown, approval_ready, follower)
        await pilot._wait_for_screen()
        assert not app.query_one("#approve", Button).disabled
        await pilot.click("#approve")
        await pilot._wait_for_screen()

        async def approval_completed():
            pending = await client.pending(session_id)
            view = await client.get(session_id)
            return (
                pending is None
                and app.query_one("#approve", Button).disabled
                and view.summary.status == "idle"
            )

        await wait_for_state_or_operation(shown, approval_completed, follower)
        await pilot._wait_for_screen()


async def test_explicit_volatile_demo_has_no_files_or_resume(tmp_path):
    root = tmp_path / "unused"
    async with open_demo(root, volatile=True) as (app, actor, tool):
        assert app.resume_mode == "off"
        assert app.store.capabilities.durability == "none"
        client = app.client(actor)
        session = await client.create(ref="demo", settings={"approval": "manual"})
        result = await (await client.send(session.id, "Fabricated request")).result()
        assert result.stop_reason == "confirmation_required"
        assert tool.recorded == []
    assert not root.exists()


async def test_terminal_ui_keeps_context_failure_visible(tmp_path):
    app = DemoApp(tmp_path, volatile=True)
    shown = observe_session_shows(app)
    async with app.run_test() as pilot:
        client = app.client
        assert client is not None
        cast(Client, client)._service.engine.context.estimator = FixedEstimator(1_000_000)
        field = app.query_one("#message", Input)
        field.value = "Fabricated request"
        await pilot.click("#send")

        async def context_failure_visible():
            lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]
            return any("[context_unavailable]" in line for line in lines)

        assert app._follower is not None
        await wait_for_state_or_operation(shown, context_failure_visible, app._follower)
        await pilot._wait_for_screen()
        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]
        assert any("[context_unavailable]" in line for line in lines)


async def test_new_session_button_and_shortcut_create_select_and_persist(tmp_path):
    from textual.widgets import ListView

    app = DemoApp(tmp_path)
    async with app.run_test() as pilot:
        client = app.client
        assert client is not None
        original = app.session_id
        field = app.query_one("#message", Input)
        field.value = "Unsent draft"
        await pilot.click("#new-session")
        await pilot._wait_for_screen()
        created = app.session_id
        assert created is not None
        assert created != original
        assert len(app.sessions) == 2
        assert field.value == ""
        assert app.focused is field
        assert (await client.history(created)).items == ()
        assert (await client.get(created)).settings["approval"] == "manual"
        listing = app.query_one("#sessions", ListView)
        assert listing.index is not None
        assert app.sessions[listing.index].id == created
        assert app.query_one("#approve", Button).disabled
        assert app.query_one("#deny", Button).disabled
        assert app.query_one("#resume", Button).disabled
        await pilot.press("ctrl+n")
        await pilot._wait_for_screen()
        assert app.session_id not in {original, created}
        assert len(app.sessions) == 3
    reopened = DemoApp(tmp_path)
    async with reopened.run_test():
        assert created in {item.id for item in reopened.sessions}
        assert len(reopened.sessions) == 3


async def test_text_typed_while_new_session_opens_is_kept(tmp_path):
    import asyncio

    app = DemoApp(tmp_path, volatile=True)
    async with app.run_test() as pilot:
        original = app.session_id
        field = app.query_one("#message", Input)
        field.value = "Old draft"
        selected = asyncio.Event()
        resume = asyncio.Event()
        refresh = app._refresh_sessions

        async def held_refresh() -> None:
            selected.set()
            await resume.wait()
            await refresh()

        app._refresh_sessions = held_refresh
        opening = asyncio.create_task(app.action_new_session())
        await selected.wait()
        assert app.session_id != original
        assert field.value == ""
        field.value = "Typed for the new session"
        app._refresh_sessions = refresh
        resume.set()
        await opening
        await pilot._wait_for_screen()
        assert field.value == "Typed for the new session"
        assert app.focused is field


async def test_new_session_keeps_original_pending_and_routes_input_to_new_session(tmp_path):
    from textual.widgets import ListView

    app = DemoApp(tmp_path, volatile=True)
    shown = observe_session_shows(app)
    async with app.run_test() as pilot:
        client, original = app.client, app.session_id
        assert client is not None
        assert original is not None
        follower = app._follower
        assert follower is not None
        app.query_one("#message", Input).value = "Original fabricated request"
        await pilot.click("#send")

        async def original_pending_visible():
            return (
                await client.pending(original) is not None
                and not app.query_one("#approve", Button).disabled
            )

        await wait_for_state_or_operation(shown, original_pending_visible, follower)
        await pilot._wait_for_screen()
        pending = await client.pending(original)
        assert pending is not None
        await pilot.press("ctrl+n")
        await pilot._wait_for_screen()
        created = app.session_id
        assert created is not None
        assert created != original
        assert await client.pending(created) is None
        assert (await client.pending(original)) == pending
        app.query_one("#message", Input).value = "New fabricated request"
        await pilot.click("#send")

        async def new_request_visible():
            page = await client.history(created)
            contents = [
                slot.value.record.message.content
                for slot in page.items
                if slot.value.record is not None and slot.value.record.message is not None
            ]
            return (
                "New fabricated request" in contents
                and "Original fabricated request" not in contents
            )

        await wait_for_state_or_operation(shown, new_request_visible, follower)
        await pilot._wait_for_screen()
        listing = app.query_one("#sessions", ListView)
        listing.index = next(i for i, item in enumerate(app.sessions) if item.id == original)
        listing.focus()
        await pilot.press("enter")

        async def original_session_selected():
            return app.session_id == original and not app.query_one("#approve", Button).disabled

        await wait_for_state_or_operation(shown, original_session_selected, follower)
        await pilot._wait_for_screen()
        assert app.session_id == original
        assert not app.query_one("#approve", Button).disabled
