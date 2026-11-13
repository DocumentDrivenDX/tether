"""Textual reference UI for the offline, single-owner local demo profile."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, ClassVar
from uuid import uuid4

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from tether.client import (
    Answer,
    ClientProtocol,
    ReplyReset,
    ReplyText,
    SessionSnapshot,
    SessionSummary,
)
from tether.client import (
    SessionUpdated as SessionUpdated,
)

from .feed import FeedPresentation, follow_client
from .terminal import terminal_notice


class SessionApp(App[None]):
    """Textual presentation that borrows an embedded or remote client."""

    TITLE = "Tether · client"
    BINDINGS: ClassVar = [("ctrl+n", "new_session", "New session")]
    CSS = """
    #session-panel { width: 28; }
    #new-session { width: 1fr; }
    #sessions { width: 1fr; height: 1fr; border: solid $primary; }
    #conversation { width: 1fr; border: solid $primary; }
    #transcript { height: 1fr; }
    #reply { height: auto; max-height: 50%; padding: 0 1;
             border-top: solid $primary; overflow-y: auto; display: none; }
    #message { width: 1fr; }
    #controls { height: 3; }
    #body { height: 1fr; }
    """

    def __init__(self, client: ClientProtocol | None, *, session_id: str | None = None) -> None:
        super().__init__()
        self.client: ClientProtocol | None = client
        self._initial_session_id = session_id
        self.session_id: str | None = None
        self.sessions: tuple[SessionSummary, ...] = ()
        self._follower: asyncio.Task[None] | None = None
        self._follower_stopping: asyncio.Event | None = None
        self._reply_turn: str | None = None
        self._reply_attempt: str | None = None
        self._reply_text = ""
        self._render_generation = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Live updates enabled", id="connection", markup=False)
        with Horizontal(id="body"):
            with Vertical(id="session-panel"):
                yield Button("New Session", id="new-session")
                yield ListView(id="sessions")
            with Vertical(id="conversation"):
                yield RichLog(id="transcript", wrap=True, markup=False)
                yield Static(id="reply", markup=False)
        with Vertical(id="controls"), Horizontal():
            yield Input(placeholder="Send a fabricated message", id="message")
            yield Button("Send", id="send")
            yield Button("Approve", id="approve")
            yield Button("Deny", id="deny")
            yield Button("Resume", id="resume")
        yield Footer()

    async def _connect_client(self, session_id: str | None) -> None:
        client = self.client
        if client is None:
            raise RuntimeError("client_not_connected")
        if session_id is not None:
            await client.get(session_id)
            self.session_id = session_id
        else:
            page = await client.list()
            self.session_id = page.items[0].id if page.items else None
            if self.session_id is None:
                created = await client.create(title="Session")
                self.session_id = created.id
        await self._connected()

    async def _connected(self, *, notice: str | None = None) -> None:
        snapshot = (
            await self.client.snapshot(self.session_id)
            if self.client is not None and self.session_id is not None
            else None
        )
        await self._refresh_sessions()
        await self._show_session()
        if notice is not None:
            self.query_one("#transcript", RichLog).write(notice)
        self._start_follower(snapshot.seq if snapshot is not None else None)
        self.query_one("#message", Input).focus()

    def _start_follower(self, initial_cursor: int | None) -> None:
        stopping = asyncio.Event()
        self._follower_stopping = stopping
        self._follower = asyncio.create_task(self._follow(initial_cursor, stopping))

    async def action_new_session(self) -> None:
        if self.client is None:
            return
        # Clear the old draft before switching: text typed once the new session
        # is selected belongs to it and must survive the refresh below.
        field = self.query_one("#message", Input)
        field.value = ""
        session = await self.client.create(
            title="Session · " + uuid4().hex[:8], settings={"approval": "manual"}
        )
        self.session_id = session.id
        await self._refresh_sessions()
        await self._show_session()
        field.focus()

    async def _stop_follower(self) -> None:
        if self._follower_stopping is not None:
            self._follower_stopping.set()
        if self._follower is not None:
            self._follower.cancel()
            with suppress(asyncio.CancelledError):
                await self._follower
            self._follower = None
        self._follower_stopping = None

    async def action_quit(self) -> None:
        await self._stop_follower()
        await super().action_quit()

    async def on_unmount(self) -> None:
        await self._stop_follower()

    async def _refresh_sessions(self) -> None:
        if self.client is None or not self.is_running:
            return
        page = await self.client.list()
        if not self.is_running:
            return
        self.sessions = page.items
        listing = next((node for node in self.query(ListView) if node.id == "sessions"), None)
        if listing is None:
            return
        await listing.clear()
        for item in page.items:
            if not self.is_running:
                return
            await listing.append(ListItem(Label(f"{item.title or item.id} · {item.status}")))
        if not self.is_running:
            return
        listing.index = next(
            (index for index, item in enumerate(self.sessions) if item.id == self.session_id), None
        )

    async def _show_session(self) -> None:
        client, selected = self.client, self.session_id
        if client is None or selected is None or not self.is_running:
            return
        self._render_generation += 1
        generation = self._render_generation
        self._reply_turn = None
        self._reset_reply()
        log = self.query_one("#transcript", RichLog)
        log.clear()
        snapshot = await client.snapshot(selected)
        if not self._view_is_current(selected, generation):
            return
        await self._render_history(client, selected, snapshot.through_index, generation)
        if not self._view_is_current(selected, generation):
            return
        await self._update_approval(selected, generation)
        if not self._view_is_current(selected, generation):
            return
        await self._render_terminal_notice(client, selected, snapshot, generation)

    def _view_is_current(self, session_id: str, generation: int) -> bool:
        return (
            self.is_running
            and (self._follower_stopping is None or not self._follower_stopping.is_set())
            and self.session_id == session_id
            and self._render_generation == generation
        )

    async def _render_history(
        self, client: ClientProtocol, session_id: str, through_index: int, generation: int
    ) -> None:
        start = 0
        while True:
            page = await client.history(session_id, start=start)
            if not self._view_is_current(session_id, generation):
                return
            for slot in page.items:
                if slot.entry_index > through_index:
                    continue
                record = slot.value
                if record.status == "ready" and record.record is not None:
                    message = record.record.message
                    if message is not None and message.content:
                        self.query_one("#transcript", RichLog).write(
                            f"{message.role}: {message.content}"
                        )
                elif record.status != "ready":
                    self.query_one("#transcript", RichLog).write(
                        f"[history {record.status} at {slot.entry_index}]"
                    )
            if page.next_start is None:
                return
            start = page.next_start

    async def _render_terminal_notice(
        self,
        client: ClientProtocol,
        session_id: str,
        snapshot: SessionSnapshot,
        generation: int,
    ) -> None:
        notice = await terminal_notice(
            client,
            session_id,
            snapshot,
            current=lambda: self._view_is_current(session_id, generation),
        )
        if not self._view_is_current(session_id, generation):
            return
        latest = await client.snapshot(session_id)
        if (
            self._view_is_current(session_id, generation)
            and latest.through_index == snapshot.through_index
            and notice is not None
        ):
            self.query_one("#transcript", RichLog).write(notice)

    async def _update_approval(self, session_id: str, generation: int) -> None:
        client = self.client
        if client is None:
            return
        pending = await client.pending(session_id)
        if not self._view_is_current(session_id, generation):
            return
        view = await client.get(session_id)
        if not self._view_is_current(session_id, generation):
            return
        self.query_one("#approve", Button).disabled = pending is None or pending.arguments is None
        self.query_one("#deny", Button).disabled = pending is None
        self.query_one("#resume", Button).disabled = not view.summary.resumable
        if pending is not None:
            self.query_one("#transcript", RichLog).write(
                f"Approval requested: {pending.tool} — {pending.description}"
            )
            self.query_one("#transcript", RichLog).write(
                json.dumps(pending.arguments, ensure_ascii=True)
                if pending.arguments is not None
                else "[arguments unavailable]"
            )

    def _reset_reply(self) -> None:
        self._reply_attempt = None
        self._reply_text = ""
        if not self.is_running:
            return
        reply = self.query_one("#reply", Static)
        reply.update("")
        reply.display = False

    def _update_reply(self, change: ReplyText) -> None:
        if change.session_id != self.session_id or change.turn_id != self._reply_turn:
            return
        if change.attempt_id != self._reply_attempt:
            self._reset_reply()
            if change.offset != 0:
                return
            self._reply_attempt = change.attempt_id
        if len(self._reply_text) != change.offset:
            self._reset_reply()
            return
        self._reply_text += change.text
        reply = self.query_one("#reply", Static)
        reply.border_title = "Assistant · responding"
        reply.update(self._reply_text + " ▍")
        reply.display = True
        reply.call_after_refresh(reply.scroll_end, animate=False)

    async def _follow(
        self,
        initial_cursor: int | None = None,
        stopping: asyncio.Event | None = None,
    ) -> None:
        if self.client is None:
            return
        if stopping is None:
            stopping = asyncio.Event()
            self._follower_stopping = stopping

        def reset_attempt(change: ReplyReset) -> None:
            if (
                change.session_id == self.session_id
                and change.turn_id == self._reply_turn
                and change.attempt_id == self._reply_attempt
            ):
                self._reset_reply()

        def failed() -> None:
            if self.is_running:
                self.query_one("#connection", Static).update(
                    "Live updates unavailable. Reopen the client to reconnect."
                )

        await follow_client(
            self.client,
            FeedPresentation(
                selected=lambda: self.session_id,
                refresh=self._refresh_sessions,
                show=self._show_session,
                reset=self._reset_reply,
                reply=self._update_reply,
                reset_attempt=reset_attempt,
                failed=failed,
                busy=lambda: self._reply_turn is not None,
                stopping=stopping.is_set,
            ),
            initial_cursor=initial_cursor,
        )

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
        selected = self.session_id
        self._reply_turn = handle.turn_id
        try:
            await handle.result()
        finally:
            self._reply_turn = None
            self._reset_reply()
        await self._refresh_sessions()
        if self.is_running and self.session_id == selected:
            await self._show_session()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "message":
            await self._send()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.client is None or self.session_id is None:
            return
        if event.button.id == "new-session":
            await self.action_new_session()
        elif event.button.id == "send":
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


class ClientApp(SessionApp):
    """Textual client UI that borrows an embedded or remote client."""

    def __init__(self, client: ClientProtocol, *, session_id: str | None = None) -> None:
        super().__init__(client, session_id=session_id)

    async def on_mount(self) -> None:
        await self._connect_client(self._initial_session_id)
