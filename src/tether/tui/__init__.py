"""Textual reference UI for the offline, single-owner local demo profile."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, RichLog

from tether.cli.demo import load_profile, open_demo
from tether.client import Client
from tether.types.client import ReplyReset, ReplyText, SessionUpdated
from tether.types.execution import Actor, Answer, TurnResult
from tether.types.storage import SessionSummary


class DemoApp(App[None]):
    TITLE = "Tether · offline demo"
    CSS = """
    #sessions { width: 28; border: solid $primary; }
    #transcript { width: 1fr; border: solid $primary; }
    #message { width: 1fr; }
    #controls { height: 3; }
    #body { height: 1fr; }
    """

    def __init__(
        self, root: Path, *, volatile: bool = False, model_config: Path | None = None
    ) -> None:
        super().__init__()
        self.root = root
        self.volatile = volatile
        self.model_config = model_config
        self._demo_context: Any = None
        self.client: Client | None = None
        self.actor: Actor | None = None
        self.session_id: str | None = None
        self.sessions: tuple[SessionSummary, ...] = ()
        self._follower: asyncio.Task[None] | None = None
        self._provisional: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield ListView(id="sessions")
            yield RichLog(id="transcript", wrap=True, markup=False)
        with Vertical(id="controls"), Horizontal():
            yield Input(placeholder="Send a fabricated message", id="message")
            yield Button("Send", id="send")
            yield Button("Approve", id="approve")
            yield Button("Deny", id="deny")
            yield Button("Resume", id="resume")
        yield Footer()

    async def on_mount(self) -> None:
        profile = await load_profile(self.model_config) if self.model_config is not None else None
        if profile is not None:
            self.title = "Tether · " + profile.id
            self.query_one("#message", Input).placeholder = "Send a message"
        self._demo_context = open_demo(self.root, volatile=self.volatile, profile=profile)
        app, actor, _ = await self._demo_context.__aenter__()
        client = app.client(actor)
        self.client, self.actor = client, actor
        session_ref = profile.session_ref if profile is not None else "demo"
        demo = await client.find(session_ref)
        if demo is None:
            demo = await client.create(
                ref=session_ref,
                title="Model · " + profile.id if profile is not None else "Offline demo",
                settings={"approval": "manual"},
            )
        self.session_id = demo.id
        await self._refresh_sessions()
        await self._show_session()
        self._follower = asyncio.create_task(self._follow())
        self.query_one("#message", Input).focus()

    async def _stop_follower(self) -> None:
        if self._follower is not None:
            self._follower.cancel()
            with suppress(asyncio.CancelledError):
                await self._follower
            self._follower = None

    async def action_quit(self) -> None:
        await self._stop_follower()
        await super().action_quit()

    async def on_unmount(self) -> None:
        await self._stop_follower()
        if self._demo_context is not None:
            await self._demo_context.__aexit__(None, None, None)

    async def _refresh_sessions(self) -> None:
        if self.client is None:
            return
        page = await self.client.list()
        self.sessions = page.items
        listing = self.query_one("#sessions", ListView)
        await listing.clear()
        for item in page.items:
            await listing.append(ListItem(Label(f"{item.title or item.id} · {item.status}")))

    async def _show_session(self) -> None:
        if self.client is None or self.session_id is None:
            return
        log = self.query_one("#transcript", RichLog)
        log.clear()
        start = 0
        while True:
            page = await self.client.history(self.session_id, start=start)
            for slot in page.items:
                record = slot.value
                if record.status == "ready" and record.record is not None:
                    message = record.record.message
                    if message is not None and message.content:
                        log.write(f"{message.role}: {message.content}")
                elif record.status != "ready":
                    log.write(f"[history {record.status} at {slot.entry_index}]")
            if page.next_start is None:
                break
            start = page.next_start
        await self._update_approval()

    async def _update_approval(self) -> None:
        if self.client is None or self.session_id is None:
            return
        pending = await self.client.pending(self.session_id)
        view = await self.client.get(self.session_id)
        self.query_one("#approve", Button).disabled = pending is None
        self.query_one("#deny", Button).disabled = pending is None
        self.query_one("#resume", Button).disabled = not view.summary.resumable
        if pending is not None:
            self.query_one("#transcript", RichLog).write(
                f"Approval requested: {pending.description or pending.tool}"
            )

    async def _follow(self) -> None:
        if self.client is None:
            return
        async for change in self.client.follow():
            if isinstance(change, ReplyText):
                if change.session_id != self.session_id:
                    continue
                previous = self._provisional.get(change.attempt_id, "")
                if len(previous) == change.offset:
                    self._provisional[change.attempt_id] = previous + change.text
                    self.query_one("#transcript", RichLog).write(f"live: {change.text}")
            elif isinstance(change, ReplyReset):
                if change.session_id != self.session_id:
                    continue
                self._provisional.pop(change.attempt_id, None)
                self.query_one("#transcript", RichLog).write("[live reply reset]")
            elif isinstance(change, SessionUpdated):
                await self._refresh_sessions()

    async def _send(self) -> None:
        if self.client is None or self.session_id is None:
            return
        field = self.query_one("#message", Input)
        value = field.value.strip()
        if not value:
            return
        field.value = ""
        self.query_one("#transcript", RichLog).write(f"user: {value}")
        await self._finish(await self.client.send(self.session_id, value))

    async def _finish(self, handle: Any) -> None:
        result: TurnResult = await handle.result()
        await self._refresh_sessions()
        await self._show_session()
        self.query_one("#transcript", RichLog).write(f"[{result.stop_reason}]")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "message":
            await self._send()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.client is None or self.session_id is None:
            return
        if event.button.id == "send":
            await self._send()
        elif event.button.id in {"approve", "deny"}:
            pending = await self.client.pending(self.session_id)
            if pending is not None:
                handle = await self.client.confirm(
                    self.session_id,
                    pending.invocation_id,
                    Answer(approved=event.button.id == "approve"),
                )
                if handle is not None:
                    await self._finish(handle)
        elif event.button.id == "resume":
            await self._finish(await self.client.resume(self.session_id))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is not None and index < len(self.sessions):
            self.session_id = self.sessions[index].id
            await self._show_session()


def run(root: Path, *, volatile: bool = False, model_config: Path | None = None) -> None:
    DemoApp(root, volatile=volatile, model_config=model_config).run()
