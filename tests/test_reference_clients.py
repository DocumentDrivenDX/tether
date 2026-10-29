"""Offline reference clients exercise public session and approval operations."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Button, Input, RichLog

from tether.cli.command import execute, parser
from tether.cli.demo import open_demo
from tether.context.testing import FixedEstimator
from tether.tui import DemoApp
from tether.types.execution import Answer

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    app = DemoApp(tmp_path)
    async with asyncio.timeout(20), app.run_test() as pilot:
        assert app.session_id is not None
        assert len(app.sessions) == 1
        field = app.query_one("#message", Input)
        field.value = "Fabricated request"
        await pilot.click("#send")
        await pilot.pause()
        assert not app.query_one("#approve", Button).disabled
        await pilot.click("#approve")
        await pilot.pause()
        assert app.query_one("#approve", Button).disabled
        client = app.client
        assert client is not None
        assert (await client.get(app.session_id)).summary.status == "idle"


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
    async with asyncio.timeout(20), app.run_test() as pilot:
        client = app.client
        assert client is not None
        client._service.engine.context.estimator = FixedEstimator(1_000_000)
        field = app.query_one("#message", Input)
        field.value = "Fabricated request"
        await pilot.click("#send")
        await pilot.pause()
        lines = [str(line) for line in app.query_one("#transcript", RichLog).lines]
        assert any("[context_unavailable]" in line for line in lines)


async def test_new_session_button_and_shortcut_create_select_and_persist(tmp_path):
    from textual.widgets import ListView

    app = DemoApp(tmp_path)
    async with asyncio.timeout(20), app.run_test() as pilot:
        client = app.client
        assert client is not None
        original = app.session_id
        field = app.query_one("#message", Input)
        field.value = "Unsent draft"
        await pilot.click("#new-session")
        await pilot.pause()
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
        await pilot.pause()
        assert app.session_id not in {original, created}
        assert len(app.sessions) == 3
    reopened = DemoApp(tmp_path)
    async with asyncio.timeout(20), reopened.run_test():
        assert created in {item.id for item in reopened.sessions}
        assert len(reopened.sessions) == 3


async def test_new_session_keeps_original_pending_and_routes_input_to_new_session(tmp_path):
    from textual.widgets import ListView

    app = DemoApp(tmp_path, volatile=True)
    async with asyncio.timeout(20), app.run_test() as pilot:
        client, original = app.client, app.session_id
        assert client is not None
        assert original is not None
        app.query_one("#message", Input).value = "Original fabricated request"
        await pilot.click("#send")
        await pilot.pause()
        pending = await client.pending(original)
        assert pending is not None
        await pilot.press("ctrl+n")
        await pilot.pause()
        created = app.session_id
        assert created is not None
        assert created != original
        assert await client.pending(created) is None
        assert (await client.pending(original)) == pending
        app.query_one("#message", Input).value = "New fabricated request"
        await pilot.click("#send")
        await pilot.pause()
        page = await client.history(created)
        contents = [
            slot.value.record.message.content
            for slot in page.items
            if slot.value.record is not None and slot.value.record.message is not None
        ]
        assert "New fabricated request" in contents
        assert "Original fabricated request" not in contents
        listing = app.query_one("#sessions", ListView)
        listing.index = next(i for i, item in enumerate(app.sessions) if item.id == original)
        listing.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.session_id == original
        assert not app.query_one("#approve", Button).disabled
