"""Public typed-port fixture and controlled model for follower measurements."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import cast

from benchmark_follower_memory_cleanup import close_all_retained, run_retained

from tether.client import (
    Actor,
    AgentSpec,
    Change,
    ClientHost,
    ClientLimits,
    ClientProtocol,
    ClientSettings,
    EntriesAppended,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ProvidedStorage,
    SessionUpdated,
    StreamItem,
    TextDelta,
    TokenEstimate,
    TurnHandle,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model
from tether.history.jsonl import JsonlHistory
from tether.ports import ModelPort
from tether.stores.jsonl.store import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.storage import PersistedChange, SessionStore, StorageLimits

_QUEUE_SIZE = 32
_TEXT_BYTES = 760
_COALESCED_TEXT_BYTES = 32 * 1024
_HELD_TURNS = 32
_DEADLINE_SECONDS = 30


class _ObservedStore:
    """A typed-port forwarding wrapper that acknowledges change-generator resumes."""

    def __init__(
        self,
        store: SessionStore,
        advanced: asyncio.Queue[int],
        fetched: asyncio.Queue[int],
        entered: asyncio.Event,
        registered: asyncio.Queue[None],
        allow_changes: asyncio.Event,
        release_change_tail: asyncio.Event,
    ) -> None:
        self._store = store
        self._advanced = advanced
        self._fetched = fetched
        self._entered = entered
        self._registered = registered
        self._allow_changes = allow_changes
        self._release_change_tail = release_change_tail

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)

    def for_actor(self, actor: Actor) -> SessionStore:
        return cast(
            SessionStore,
            _ObservedActorStore(
                self._store.for_actor(actor),
                self._advanced,
                self._fetched,
                self._entered,
                self._registered,
                self._allow_changes,
                self._release_change_tail,
            ),
        )


class _ObservedActorStore:
    def __init__(
        self,
        store: SessionStore,
        advanced: asyncio.Queue[int],
        fetched: asyncio.Queue[int],
        entered: asyncio.Event,
        registered: asyncio.Queue[None],
        allow_changes: asyncio.Event,
        release_change_tail: asyncio.Event,
    ) -> None:
        self._store = store
        self._advanced = advanced
        self._fetched = fetched
        self._entered = entered
        self._registered = registered
        self._allow_changes = allow_changes
        self._release_change_tail = release_change_tail

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)

    def changes(
        self, owner: str, *, session_id: str | None = None, since: int | None = None
    ) -> AsyncIterator[PersistedChange]:
        async def observed() -> AsyncIterator[PersistedChange]:
            self._entered.set()
            self._registered.put_nowait(None)
            await self._allow_changes.wait()
            first_change = True
            async for change in self._store.changes(owner, session_id=session_id, since=since):
                self._fetched.put_nowait(change.seq)
                yield change
                # The consumer requests the next value only after its queue.put finished.
                self._advanced.put_nowait(change.seq)
                if first_change:
                    first_change = False
                    await self._release_change_tail.wait()

        return observed()


class _ApproximateEstimator:
    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        del request
        return TokenEstimate(
            tokens=0, method="benchmark-approximate", revision="1", quality="approximate"
        )


class _GatedModel:
    provider = "scripted-follower-memory"

    def __init__(self) -> None:
        self.primary_session: str | None = None
        self.started: dict[tuple[str, int], asyncio.Event] = {}
        self.published: dict[tuple[str, int], asyncio.Event] = {}
        self.release: dict[tuple[str, int], asyncio.Event] = {}
        self.attempts: dict[str, int] = {}
        self.phases: dict[tuple[str, int], str] = {}
        self.identities: dict[str, tuple[str, int]] = {}
        self.delta_counts: dict[tuple[str, int], int] = {}
        self.emit_primary_first = asyncio.Event()
        self.emit_primary_second = asyncio.Event()
        self.emit_primary_overflow = asyncio.Event()
        self.emit_others = asyncio.Event()
        self.emit_coalesced = asyncio.Event()
        self.primary_second_published = asyncio.Event()
        self.primary_overflow_published = asyncio.Event()

    def configure(self, held_sessions: Sequence[str]) -> None:
        self.primary_session = held_sessions[0]
        self.attempts = {session: 0 for session in held_sessions}
        for session in held_sessions:
            phase = "primary_first" if session == self.primary_session else "other"
            self._register(session, phase)

    def configure_coalesced(self, session: str) -> None:
        self.primary_session = session
        self.attempts = {session: 0}
        self._register(session, "coalesced")

    def _register(self, session: str, phase: str) -> int:
        ordinal = self.attempts[session] + 1
        self.attempts[session] = ordinal
        key = (session, ordinal)
        self.started[key] = asyncio.Event()
        self.published[key] = asyncio.Event()
        self.release[key] = asyncio.Event()
        self.phases[key] = phase
        return ordinal

    def register_primary_retry(self, phase: str) -> int:
        if self.primary_session is None:
            raise RuntimeError("benchmark_model_not_configured")
        return self._register(self.primary_session, phase)

    async def wait_started(self, session: str, ordinal: int) -> None:
        await _wait_event(self.started[(session, ordinal)])

    async def wait_published(self, session: str, ordinal: int) -> None:
        await _wait_event(self.published[(session, ordinal)])

    def release_attempt(self, session: str, ordinal: int) -> None:
        self.release[(session, ordinal)].set()

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        session_id = request.tags.session_id
        if self.primary_session is None:
            raise RuntimeError("benchmark_model_not_configured")
        ordinal = self.attempts[session_id]
        key = (session_id, ordinal)
        phase = self.phases[key]
        gates = {
            "primary_first": self.emit_primary_first,
            "primary_second": self.emit_primary_second,
            "primary_overflow": self.emit_primary_overflow,
            "other": self.emit_others,
            "coalesced": self.emit_coalesced,
        }
        text = {
            "primary_first": "a",
            "primary_second": "b",
            "primary_overflow": "c",
            "other": "x",
            "coalesced": "q",
        }[phase] * _TEXT_BYTES
        if phase == "coalesced":
            text = "q" + ("z" * (_COALESCED_TEXT_BYTES - 1))
            tail = text[1:]
            deltas = (
                TextDelta(text=text[:1]),
                *(
                    TextDelta(text=tail[index : index + 1024])
                    for index in range(0, len(tail), 1024)
                ),
            )
        else:
            deltas = (TextDelta(text=text),)
        source = cast(
            AsyncGenerator[StreamItem, None],
            scripted_model([*deltas, ModelResponse(content=text, finish_reason="stop")]).stream(
                request
            ),
        )

        async def gated() -> AsyncIterator[StreamItem]:
            try:
                first = await anext(source)
                self.delta_counts[key] = int(isinstance(first, TextDelta))
                if isinstance(first, TextDelta) and first.identity is not None:
                    self.identities[first.identity.attempt_id] = key
                self.started[key].set()
                await gates[phase].wait()
                yield first
                self.published[key].set()
                await self.release[key].wait()
                while True:
                    try:
                        item = await anext(source)
                    except StopAsyncIteration:
                        return
                    if isinstance(item, TextDelta):
                        self.delta_counts[key] += 1
                    yield item
            finally:
                await source.aclose()

        return gated()


def _limits(*, max_followers: int = 1) -> ClientLimits:
    return ClientLimits(
        max_page_size=100,
        max_call_ids=8,
        max_read_bytes=1_048_576,
        max_lookup_entries=1000,
        read_timeout_seconds=20,
        heartbeat_interval_seconds=1,
        shutdown_timeout_seconds=10,
        follower_queue_size=_QUEUE_SIZE,
        max_followers=max_followers,
        max_active_turns=_HELD_TURNS,
        max_request_bytes=65_536,
    )


def _settings(
    store: SessionStore,
    history: JsonlHistory,
    model: ModelPort,
    *,
    max_followers: int = 1,
) -> ClientSettings:
    context = fabricated_model_context()
    spec = AgentSpec(
        id="benchmark",
        version="1",
        title="Follower memory fixture",
        instructions="Return the fixed fabricated response.",
        model="demo",
        max_steps=1,
    )
    return ClientSettings(
        model=model,
        storage=ProvidedStorage(store=store, history=history),
        policy=demo_policy(),
        client_limits=_limits(max_followers=max_followers),
        model_limits=ModelExecutionLimits(
            max_response_bytes=65_536,
            max_tool_argument_bytes=1024,
            max_attempts=1,
            request_timeout_seconds=10,
            max_retry_delay_seconds=0.01,
        ),
        context_config=context["context_config"],
        estimator=_ApproximateEstimator(),
        specs=(spec,),
        default_spec=spec.id,
        resume="off",
    )


async def _wait_event(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=_DEADLINE_SECONDS)


async def _next(stream: AsyncIterator[Change]) -> Change:
    return await anext(stream)


@dataclass
class _Fixture:
    files: MemoryFiles
    store: JsonlSessionStore
    history: JsonlHistory
    model: _GatedModel
    host: ClientHost
    actor: Actor
    client: ClientProtocol
    advanced: asyncio.Queue[int]
    fetched: asyncio.Queue[int]
    entered: asyncio.Event
    registered: asyncio.Queue[None]
    allow_changes: asyncio.Event
    release_change_tail: asyncio.Event


async def _fixture(
    *, max_followers: int = 1, pause_changes: bool = False, pause_after_first_change: bool = False
) -> _Fixture:
    advanced: asyncio.Queue[int] = asyncio.Queue()
    fetched: asyncio.Queue[int] = asyncio.Queue()
    entered = asyncio.Event()
    registered: asyncio.Queue[None] = asyncio.Queue()
    allow_changes = asyncio.Event()
    if not pause_changes:
        allow_changes.set()
    release_change_tail = asyncio.Event()
    if not pause_after_first_change:
        release_change_tail.set()
    resources: list[tuple[str, Callable[[], Awaitable[bool]]]] = []
    try:
        files = MemoryFiles(StorageLimits())
        resources.append(("files", files.aclose))
        store = await JsonlSessionStore.open(files)
        resources.clear()
        resources.append(("store", store.aclose))
        history = await JsonlHistory.open(files)
        observed = cast(
            SessionStore,
            _ObservedStore(
                store,
                advanced,
                fetched,
                entered,
                registered,
                allow_changes,
                release_change_tail,
            ),
        )
        model = _GatedModel()
        host = await ClientHost.open(
            _settings(observed, history, cast(ModelPort, model), max_followers=max_followers)
        )
        resources.append(("host", host.aclose))
        actor = Actor(id="fabricated-follower-memory")
        return _Fixture(
            files,
            store,
            history,
            model,
            host,
            actor,
            host.client(actor),
            advanced,
            fetched,
            entered,
            registered,
            allow_changes,
            release_change_tail,
        )
    except BaseException as original:
        try:
            await close_all_retained(resources)
        except BaseException as cleanup_error:
            raise cleanup_error from original
        raise


async def _close_fixture(fixture: _Fixture) -> None:
    await run_retained(_close_fixture_owned(fixture))


async def _close_fixture_owned(fixture: _Fixture) -> None:
    failures: list[BaseException] = []
    host_closed = False
    try:
        host_closed = await fixture.host.aclose()
    except BaseException as error:
        failures.append(error)
    if not host_closed:
        try:
            host_closed = await fixture.host.wait_closed()
        except BaseException as error:
            failures.append(error)
    if not host_closed:
        if failures:
            raise failures[0]
        raise RuntimeError("benchmark_follower_host_close_incomplete")
    try:
        store_closed = await fixture.store.aclose()
        if not store_closed:
            failures.append(RuntimeError("benchmark_follower_store_close_incomplete"))
    except BaseException as error:
        failures.append(error)
    if failures:
        raise failures[0]


async def _consume_commit(stream: AsyncIterator[Change]) -> int:
    while True:
        event = await asyncio.wait_for(anext(stream), timeout=_DEADLINE_SECONDS)
        if isinstance(event, (SessionUpdated, EntriesAppended)) and event.last_in_commit:
            return event.seq


async def _finished(handle: TurnHandle, expected_content: str) -> None:
    result = await handle.result()
    if (
        result.stop_reason != "final"
        or not result.messages
        or result.messages[-1].content != expected_content
    ):
        raise RuntimeError("benchmark_model_turn_not_final")


async def _read_next(stream: AsyncIterator[Change]) -> Change:
    return await anext(stream)


async def _close_stream(stream: AsyncIterator[Change]) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await run_retained(close())


def _positive_growth(current: int, baseline: int) -> int:
    growth = current - baseline
    if growth < 0:
        raise RuntimeError("benchmark_memory_measurement_negative_growth")
    return growth
