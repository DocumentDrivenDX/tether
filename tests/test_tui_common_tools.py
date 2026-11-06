"""Exercise all common tools through actual Textual buttons and Client approval gates."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from test_reference_model_profile import write_profile
from textual.pilot import Pilot
from textual.widgets import Button, Input, RichLog
from tui_event_waits import (
    let_pilot_screen_wait_use_outer_owner,
    observe_session_shows,
    wait_for_state_or_operation,
)

from tether.cli import demo, web_tools
from tether.context.testing import fabricated_context
from tether.models.testing import ScriptedModel
from tether.tui import DemoApp
from tether.types import ModelResponse, ToolCall
from tether.types.execution import Actor, AgentSpec

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


@pytest.fixture
def common_profile(tmp_path, monkeypatch):
    calls = [
        ("write_file", {"path": "seedling.txt", "content": "fabricated seedling"}),
        ("read_file", {"path": "seedling.txt"}),
        ("bash", {"command": "printf 'fabricated shell output'", "timeout_seconds": 10}),
        ("web_search", {"query": "fabricated seedling"}),
        ("web_page_load", {"url": "https://example.org/seedling"}),
    ]
    seen_urls = []

    def fetch(url):
        seen_urls.append(url)
        html = (
            '<a class="result__a" href="https://example.org/seedling">Seedling</a>'
            '<a class="result__snippet">Fabricated search snippet</a>'
        )
        if url == "https://example.org/seedling":
            html = "<h1>Fabricated page</h1><script>hidden</script><p>Seedling content</p>"
        return url, "text/html", html, False

    class Model(ScriptedModel):
        async def aclose(self):
            pass

    async def configure(profile, limits, names):
        context = fabricated_context()
        frames = [
            ModelResponse(
                tool_calls=(ToolCall(id=f"tool-{index}", name=name, arguments=arguments),),
                finish_reason="tool_calls",
            )
            for index, (name, arguments) in enumerate(calls)
        ]
        frames.append(
            ModelResponse(content="All fabricated tools completed.", finish_reason="stop")
        )
        spec = AgentSpec(
            id="tools",
            version="1",
            title="Fabricated tools",
            instructions="Fabricated fixture",
            model="demo",
            tools=names,
        )
        return (
            Model(frames),
            context["context_config"],
            context["estimator"],
            Actor(id="demo"),
            spec,
        )

    monkeypatch.setattr(demo, "configured_model", configure)
    monkeypatch.setattr(web_tools, "fetch_text", fetch)
    config = write_profile(tmp_path, 1)
    settings = json.loads(config.read_text())
    settings["workspace"] = "."
    config.write_text(json.dumps(settings))
    return config, seen_urls, calls


async def _approve_common_tool(
    app: DemoApp,
    pilot: Pilot,
    shown: asyncio.Event,
    follower: asyncio.Task[None],
    name: str,
    arguments: dict[str, Any],
) -> None:
    async def pending_visible() -> bool:
        assert app.client is not None
        assert app.session_id is not None
        pending = await app.client.pending(app.session_id)
        lines = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
        return (
            pending is not None
            and pending.tool == name
            and pending.arguments == arguments
            and not app.query_one("#approve", Button).disabled
            and name in lines
        )

    await wait_for_state_or_operation(shown, pending_visible, follower)
    await pilot._wait_for_screen()
    assert app.client is not None
    assert app.session_id is not None
    pending = await app.client.pending(app.session_id)
    assert pending is not None
    assert pending.tool == name
    assert pending.arguments == arguments
    assert not app.query_one("#approve", Button).disabled
    assert name in "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
    await pilot.click("#approve")


async def _history_contains_message(client: Any, session_id: str, expected: str) -> bool:
    start = 0
    while True:
        page = await client.history(session_id, start=start)
        if any(
            slot.value.record
            and slot.value.record.message
            and slot.value.record.message.content == expected
            for slot in page.items
        ):
            return True
        if page.next_start is None:
            return False
        if page.next_start <= start:
            raise AssertionError("history_page_did_not_advance")
        start = page.next_start


async def _wait_for_final_common_tool_turn(
    app: DemoApp,
    pilot: Pilot,
    shown: asyncio.Event,
    follower: asyncio.Task[None],
    client: Any,
    session_id: str,
    seedling: Path,
) -> None:
    async def final_state_visible() -> bool:
        view = await client.get(session_id)
        transcript = "\n".join(str(line) for line in app.query_one("#transcript", RichLog).lines)
        final_message = "All fabricated tools completed."
        history_has_final = await _history_contains_message(client, session_id, final_message)
        file_exists = await asyncio.to_thread(seedling.is_file)
        return (
            await client.pending(session_id) is None
            and app.query_one("#approve", Button).disabled
            and file_exists
            and view.summary.status == "idle"
            and history_has_final
            and final_message in transcript
        )

    await wait_for_state_or_operation(shown, final_state_visible, follower)
    await pilot._wait_for_screen()
    assert await asyncio.to_thread(seedling.read_text) == "fabricated seedling"
    assert await client.pending(session_id) is None
    assert app.query_one("#approve", Button).disabled


async def test_all_common_tools_through_tui_approval(tmp_path, common_profile):
    config, seen_urls, calls = common_profile
    app = DemoApp(tmp_path / "journal", model_config=config)
    shown = observe_session_shows(app)
    async with app.run_test() as pilot:
        assert app.client is not None
        assert app.session_id is not None
        client = app.client
        session_id = app.session_id
        follower = app._follower
        assert follower is not None
        app.query_one("#message", Input).value = "Exercise the fabricated common tools"
        await pilot.click("#send")
        for index, (name, arguments) in enumerate(calls):
            if index == 0:
                assert not (tmp_path / "seedling.txt").exists()
            await _approve_common_tool(app, pilot, shown, follower, name, arguments)

        await _wait_for_final_common_tool_turn(
            app, pilot, shown, follower, client, session_id, tmp_path / "seedling.txt"
        )
        assert len(seen_urls) == 2
        assert "q=fabricated+seedling" in seen_urls[0]
        assert seen_urls[1] == "https://example.org/seedling"
        start = 0
        messages = []
        while True:
            history = await app.client.history(app.session_id, start=start)
            messages.extend(
                slot.value.record.message.content
                for slot in history.items
                if slot.value.record and slot.value.record.message
            )
            if history.next_start is None:
                break
            start = history.next_start
        contents = "\n".join(messages)
        assert "fabricated shell output" in contents
        assert "fabricated seedling" in contents
        assert "Fabricated search snippet" in contents
        assert "Seedling content" in contents
        assert "hidden" not in contents
        assert "All fabricated tools completed." in contents
