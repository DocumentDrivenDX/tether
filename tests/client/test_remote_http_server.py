from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI, Request
from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.typing import ASGIFramework
from selected_profiles_process import await_retained
from tether_web_api import ApiFailure, ApiSettings, create_api

from tether.client import (
    Actor,
    Change,
    ClientHost,
    ClientProvider,
    ClientSettings,
    EntriesAppended,
    ExecutionDenied,
    LocalJsonlSettings,
    SessionUpdated,
)
from tether.client.remote import RemoteClient, RemoteClientSettings, RemoteProblem
from tether.context.testing import fabricated_context
from tether.engine.governance import AllowAll
from tether.models.testing import ScriptedModel
from tether.types import ModelResponse
from tether.types.execution import AgentSpec

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class MutableToken:
    def __init__(self) -> None:
        self.value = "alice"

    async def authorization(self) -> str:
        return f"Bearer {self.value}"

    async def __call__(self) -> MutableToken:
        return self


class TokenAuth:
    def __init__(self) -> None:
        self.revoked = False
        self.revoke_after_feed_call: int | None = None
        self.feed_authorization_calls = 0
        self.feed_event_cursors: list[str | None] = []
        self.feed_started = asyncio.Event()
        self.calls = 0

    async def actor_for(self, request: Request) -> Actor:
        self.calls += 1
        is_feed = request.url.path.endswith("/events")
        if is_feed:
            self.feed_authorization_calls += 1
            self.feed_started.set()
            self.feed_event_cursors.append(request.headers.get("last-event-id"))
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if self.revoked or token not in {"alice", "bob"}:
            raise ApiFailure(401, "authentication_required")
        actor = Actor(id=token, principal=token)
        if is_feed and self.feed_authorization_calls == self.revoke_after_feed_call:
            self.revoked = True
        return actor


class SnapshotPolicy:
    def __init__(self) -> None:
        self.denied = False
        self.block_snapshot_number: int | None = None
        self.snapshot_calls = 0
        self.snapshot_entered = asyncio.Event()
        self.release_snapshot = asyncio.Event()
        self.queued_marker: str | None = None
        self.marker_queued = asyncio.Event()

    def client(self, actor: Actor, host: ClientHost):
        return SnapshotPolicyClient(host.client(actor), self)


class SnapshotPolicyClient:
    def __init__(self, client: Any, policy: SnapshotPolicy) -> None:
        self._client = client
        self._policy = policy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def snapshot(self, session_id: str):
        self._policy.snapshot_calls += 1
        if self._policy.snapshot_calls == self._policy.block_snapshot_number:
            self._policy.snapshot_entered.set()
            await self._policy.release_snapshot.wait()
        if self._policy.denied:
            raise ApiFailure(403, "denied")
        return await self._client.snapshot(session_id)

    async def follow(self, *, session_id: str | None = None, since: int | None = None):
        async for event in self._client.follow(session_id=session_id, since=since):
            if (
                self._policy.queued_marker is not None
                and isinstance(event, SessionUpdated)
                and event.summary.title == self._policy.queued_marker
            ):
                self._policy.marker_queued.set()
            yield event


class StalledEventHeaders:
    def __init__(self) -> None:
        self.first_attempt = asyncio.Event()
        self.retry_attempt = asyncio.Event()
        self.release = asyncio.Event()
        self.attempts = 0

    async def __call__(self, request: Request, call_next: Any) -> Any:
        if request.url.path.endswith("/events"):
            self.attempts += 1
            self.first_attempt.set()
            if self.attempts >= 2:
                self.retry_attempt.set()
            await self.release.wait()
        return await call_next(request)


def host_settings(path: Path, *, client_limits: Any, limits: Any) -> ClientSettings:
    context = fabricated_context()
    return ClientSettings(
        model=ScriptedModel([ModelResponse(content="reply", finish_reason="stop")]),
        storage=LocalJsonlSettings(path),
        policy=AllowAll(),
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(AgentSpec(id="demo", version="1", title="Demo", instructions="Demo", model="demo"),),
        default_spec="demo",
    )


@asynccontextmanager
async def serve_api(app: FastAPI) -> AsyncIterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    config = Config()
    config.bind = [f"127.0.0.1:{port}"]
    config.accesslog = None
    config.errorlog = None
    stopped = asyncio.Event()
    server = asyncio.create_task(
        serve(cast(ASGIFramework, app), config, shutdown_trigger=stopped.wait)
    )
    app.state.qualification_server_task = server
    base_url = f"http://127.0.0.1:{port}"
    try:
        await _wait_for_http_ready(base_url, server)
        yield base_url
    finally:
        stopped.set()

        async def join_server() -> None:
            await server

        await await_retained(asyncio.create_task(join_server()))


async def _wait_for_http_ready(base_url: str, server: asyncio.Task[None]) -> None:
    # Readiness is established by a successful response, not elapsed startup time.
    async with httpx.AsyncClient(timeout=None) as probe:  # noqa: S113
        while True:
            if server.done():
                await server
                raise RuntimeError("fabricated_http_server_stopped_before_ready")
            request = asyncio.create_task(probe.get(base_url + "/not-found"))
            try:
                done, _ = await asyncio.wait({request, server}, return_when=asyncio.FIRST_COMPLETED)
                if server in done and request not in done:
                    await server
                    raise RuntimeError("fabricated_http_server_stopped_before_ready")
                await request
            except httpx.ConnectError:
                await asyncio.sleep(0)
                continue
            finally:
                if not request.done():
                    request.cancel()

                    await settle_tasks(request)
            return


async def await_event_or_task(
    event: asyncio.Event,
    operation: asyncio.Future[Any],
    terminal: asyncio.Future[Any] | None = None,
) -> None:
    ready = asyncio.create_task(event.wait())
    try:
        watched = {ready, operation}
        if terminal is not None:
            watched.add(terminal)
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        if ready not in done:
            if operation in done:
                await operation
                raise AssertionError("operation terminated before event witness")
            if terminal is not None:
                await terminal
                raise AssertionError("server terminated before event witness")
        await ready
    finally:
        if not ready.done():
            ready.cancel()

        await settle_tasks(ready)


async def await_events_or_task(
    events: tuple[asyncio.Event, ...],
    operation: asyncio.Future[Any],
    terminal: asyncio.Future[Any] | None = None,
) -> None:
    ready = {asyncio.create_task(event.wait()) for event in events}
    pending = set(ready)
    try:
        while pending:
            watched = pending | {operation}
            if terminal is not None:
                watched.add(terminal)
            done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
            if operation in done and operation not in pending:
                await operation
                raise AssertionError("operation terminated before event witnesses")
            if terminal is not None and terminal in done:
                await terminal
                raise AssertionError("server terminated before event witnesses")
            pending -= done
    finally:
        for task in ready:
            if not task.done():
                task.cancel()
        await settle_tasks(*ready)


async def settle_tasks(*tasks: asyncio.Future[Any]) -> None:
    async def settle() -> None:
        await asyncio.gather(*tasks, return_exceptions=True)

    await await_retained(asyncio.create_task(settle()))


async def await_queued_http_request(
    pool: Any,
    read: asyncio.Task[Any],
    feed: asyncio.Task[Any],
    server: asyncio.Task[None],
) -> None:
    while True:
        if server.done():
            await server
            raise AssertionError("server terminated before queued-read witness")
        if feed.done():
            await feed
            raise AssertionError("feed terminated before queued-read witness")
        if read.done():
            await read
            raise AssertionError("single-connection read completed before feed close")
        requests = pool._requests
        if len(requests) >= 2 and requests[-1].is_queued():
            return
        await asyncio.sleep(0)


async def test_queued_readiness_failure_settles_created_read_and_feed_tasks() -> None:
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def feed_events() -> AsyncGenerator[object, None]:
        try:
            entered.set()
            await asyncio.Event().wait()
            yield object()
        finally:
            closed.set()

    async def request() -> None:
        await asyncio.Event().wait()

    async def failed_server() -> None:
        await entered.wait()
        raise RuntimeError("fabricated_server_terminal")

    class Pool:
        def __init__(self) -> None:
            self._requests: list[object] = []

    feed = feed_events()
    next_event = asyncio.create_task(anext(feed))
    read = asyncio.create_task(request())
    server = asyncio.create_task(failed_server())
    try:
        with pytest.raises(RuntimeError, match="fabricated_server_terminal"):
            await await_queued_http_request(Pool(), read, next_event, server)
    finally:
        read.cancel()
        next_event.cancel()

        await settle_tasks(read, next_event, server)
        await await_retained(asyncio.create_task(feed.aclose()))
    assert read.done()
    assert next_event.done()
    assert closed.is_set()


async def test_remote_feed_reauthorizes_on_each_committed_delivery_over_tcp(
    tmp_path: Path, client_limits: Any, limits: Any
) -> None:
    """@covers US-066-AC5"""
    host = await ClientHost.open(
        host_settings(tmp_path / "journal", client_limits=client_limits, limits=limits)
    )
    token = MutableToken()
    authenticator = TokenAuth()
    app = create_api(
        clients=host.client,
        authenticator=authenticator,
        settings=ApiSettings(event_batch_limit=2, heartbeat_seconds=10),
    )
    try:
        async with (
            serve_api(app) as base_url,
            await RemoteClient.open(RemoteClientSettings(base_url, token)) as remote,
        ):
            session = await remote.create(ref="tcp-feed", title="initial")
            feed = cast(
                AsyncGenerator[Change, None],
                remote.follow(session_id=session.id, since=session.last_seq),
            )
            authenticator.revoke_after_feed_call = 3
            event_task = asyncio.ensure_future(anext(feed))
            try:
                await remote.update(session.id, title="committed")
                first = await event_task
                second = await anext(feed)
                assert isinstance(first, SessionUpdated)
                assert first.summary.title == "committed"
                assert first.last_in_commit is False
                assert isinstance(second, EntriesAppended)
                assert second.last_in_commit is True

                with pytest.raises(RemoteProblem) as error:
                    await anext(feed)
                assert error.value.status == 401
                assert authenticator.feed_authorization_calls >= 3
                assert authenticator.feed_event_cursors[-1] == str(second.seq)
            finally:
                if not event_task.done():
                    event_task.cancel()
                await settle_tasks(event_task)
                await await_retained(asyncio.create_task(feed.aclose()))
    finally:
        assert await host.aclose()


async def test_idle_tcp_feed_reauthorizes_heartbeat_then_refuses_reconnect(
    tmp_path: Path, client_limits: Any, limits: Any
) -> None:
    host = await ClientHost.open(
        host_settings(tmp_path / "journal", client_limits=client_limits, limits=limits)
    )
    authenticator = TokenAuth()
    app = create_api(
        clients=host.client,
        authenticator=authenticator,
        settings=ApiSettings(heartbeat_seconds=0.01),
    )
    try:
        async with serve_api(app) as base_url, httpx.AsyncClient(base_url=base_url) as http:
            headers = {"Authorization": " ".join(("Bearer", "alice"))}
            authenticator.revoke_after_feed_call = 2
            async with http.stream("GET", "/api/v1/events", headers=headers) as response:
                assert response.status_code == 200
                frames = [chunk async for chunk in response.aiter_bytes()]
            assert b"".join(frames) == b": heartbeat\n\n"
            assert authenticator.feed_authorization_calls == 3
            refused = await http.get("/api/v1/events", headers=headers)
            assert refused.status_code == 401
            assert refused.json()["code"] == "authentication_required"
    finally:
        assert await host.aclose()


async def test_closing_feed_releases_single_http_connection_for_next_request(
    tmp_path: Path, client_limits: Any, limits: Any
) -> None:
    host = await ClientHost.open(
        host_settings(tmp_path / "journal", client_limits=client_limits, limits=limits)
    )
    authenticator = TokenAuth()
    app = create_api(
        clients=host.client,
        authenticator=authenticator,
        settings=ApiSettings(heartbeat_seconds=10),
    )
    try:
        async with (
            serve_api(app) as base_url,
            httpx.AsyncClient(
                base_url=base_url, limits=httpx.Limits(max_connections=1)
            ) as bounded_http,
        ):
            remote = await RemoteClient.open(
                RemoteClientSettings(base_url, MutableToken()), http_client=bounded_http
            )
            try:
                session = await remote.create(ref="one-connection")
                feed = cast(
                    AsyncGenerator[Change, None],
                    remote.follow(session_id=session.id, since=session.last_seq),
                )
                next_event = asyncio.create_task(anext(feed))
                read: asyncio.Task[Any] | None = None
                try:
                    server = app.state.qualification_server_task
                    await await_event_or_task(authenticator.feed_started, next_event, server)
                    read = asyncio.create_task(remote.get(session.id))
                    pool = cast(Any, bounded_http._transport)._pool
                    await await_queued_http_request(pool, read, next_event, server)
                    next_event.cancel()
                    await settle_tasks(next_event)
                    await await_retained(asyncio.create_task(feed.aclose()))
                    assert (await read).summary.id == session.id
                finally:
                    if read is not None and not read.done():
                        read.cancel()
                    if not next_event.done():
                        next_event.cancel()
                    await settle_tasks(next_event, *(tuple() if read is None else (read,)))
                    await await_retained(asyncio.create_task(feed.aclose()))
            finally:
                await await_retained(asyncio.create_task(remote.aclose()))
    finally:
        assert await host.aclose()


async def test_stream_response_headers_have_a_finite_request_deadline(
    tmp_path: Path, client_limits: Any, limits: Any
) -> None:
    host = await ClientHost.open(
        host_settings(tmp_path / "journal", client_limits=client_limits, limits=limits)
    )
    authenticator = TokenAuth()
    app = create_api(
        clients=host.client,
        authenticator=authenticator,
        settings=ApiSettings(heartbeat_seconds=10),
    )
    stalled_headers = StalledEventHeaders()
    app.middleware("http")(stalled_headers)

    try:
        async with serve_api(app) as base_url:
            remote = await RemoteClient.open(
                RemoteClientSettings(
                    base_url,
                    MutableToken(),
                    request_timeout_seconds=0.05,
                    event_idle_timeout_seconds=1.0,
                )
            )
            pending: asyncio.Task[Change] | None = None
            feed: AsyncGenerator[Change, None] | None = None
            try:
                session = await remote.create(ref="stalled-stream-headers")
                feed = cast(
                    AsyncGenerator[Change, None],
                    remote.follow(session_id=session.id, since=session.last_seq),
                )
                pending = asyncio.create_task(anext(feed))
                await await_events_or_task(
                    (stalled_headers.first_attempt, stalled_headers.retry_attempt),
                    pending,
                    app.state.qualification_server_task,
                )
                assert stalled_headers.attempts >= 2
                assert not pending.done()
            finally:
                stalled_headers.release.set()
                if pending is not None and not pending.done():
                    pending.cancel()
                    await settle_tasks(pending)
                if feed is not None:
                    await feed.aclose()
                await remote.aclose()
    finally:
        assert await host.aclose()


async def test_feed_withholds_next_commit_after_constant_identity_loses_snapshot_access(
    tmp_path: Path, client_limits: Any, limits: Any
) -> None:
    """@covers US-066-AC5"""
    host = await ClientHost.open(
        host_settings(tmp_path / "journal", client_limits=client_limits, limits=limits)
    )
    policy = SnapshotPolicy()
    authenticator = TokenAuth()
    app = create_api(
        clients=cast(ClientProvider, lambda actor: policy.client(actor, host)),
        authenticator=authenticator,
        settings=ApiSettings(event_batch_limit=1, heartbeat_seconds=10),
    )
    try:
        async with (
            serve_api(app) as base_url,
            await RemoteClient.open(RemoteClientSettings(base_url, MutableToken())) as remote,
        ):
            session = await remote.create(ref="withheld-feed", title="initial")
            policy.block_snapshot_number = 3
            feed = cast(
                AsyncGenerator[Change, None],
                remote.follow(session_id=session.id, since=session.last_seq),
            )
            first: asyncio.Task[Change] | None = None
            next_change: asyncio.Task[Change] | None = None
            try:
                first = asyncio.create_task(anext(feed))
                await remote.update(session.id, title="first authorized commit")
                change_a = await first
                assert isinstance(change_a, SessionUpdated)

                marker = f"fabricated-withheld-{id(policy)}"
                policy.queued_marker = marker
                next_change = asyncio.create_task(anext(feed))
                await await_event_or_task(
                    policy.snapshot_entered,
                    next_change,
                    app.state.qualification_server_task,
                )
                await remote.update(session.id, title=marker)
                await await_event_or_task(
                    policy.marker_queued,
                    next_change,
                    app.state.qualification_server_task,
                )
                policy.denied = True
                policy.release_snapshot.set()
                with pytest.raises(ExecutionDenied) as refused:
                    await next_change
                assert marker not in str(refused.value)
            finally:
                policy.release_snapshot.set()
                for operation in (first, next_change):
                    if operation is not None and not operation.done():
                        operation.cancel()
                await settle_tasks(
                    *(operation for operation in (first, next_change) if operation is not None),
                )
                await feed.aclose()
    finally:
        assert await host.aclose()


async def test_incompatible_api_major_fails_without_fallback_or_dispatch(
    tmp_path: Path, client_limits: Any, limits: Any
) -> None:
    """@covers US-069-AC6"""
    host = await ClientHost.open(
        host_settings(tmp_path / "journal", client_limits=client_limits, limits=limits)
    )
    authenticator = TokenAuth()
    app = create_api(
        clients=host.client,
        authenticator=authenticator,
        settings=ApiSettings(),
    )
    try:
        async with serve_api(app) as base_url:
            with pytest.raises(RemoteProblem) as error:
                await RemoteClient.open(
                    RemoteClientSettings(
                        base_url,
                        MutableToken(),
                        api_prefix="/api/v2",
                    )
                )
            assert error.value.status == 404
            assert authenticator.calls == 0
    finally:
        assert await host.aclose()
