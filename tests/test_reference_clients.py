"""Offline reference clients exercise public session and approval operations."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Button, Input

from tether.cli.command import execute, parser
from tether.cli.demo import open_demo
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
