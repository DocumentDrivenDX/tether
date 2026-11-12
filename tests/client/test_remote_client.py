from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from pydantic import BaseModel
from remote_lifetime_controls import (
    SecondTurnHeldModel,
    TargetCancelThenHoldSuccessorModel,
    run_lost_target_cancel_response_retry,
    run_stale_remote_cancellation,
)
from tether_web_api import ApiFailure, ApiSettings, create_api

from tether.client import (
    Actor,
    Change,
    ClientHost,
    ClientSettings,
    LocalJsonlSettings,
    ResyncRequired,
    SessionRemoved,
    UnknownModelCall,
    UnknownSession,
    Unsupported,
)
from tether.client.remote import RemoteClient, RemoteClientSettings, RemoteTransportError
from tether.context.testing import fabricated_context
from tether.engine.governance import AllowAll
from tether.models.testing import ScriptedModel
from tether.types import ModelResponse
from tether.types.execution import AgentSpec, AuthzRequest, Decision, ExecutionDenied

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class LeakyProtocolFailure(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        authorization = request.headers.get("authorization", "")
        raise httpx.LocalProtocolError(f"invalid request header {authorization}")

    async def aclose(self) -> None:
        return None


class LoseFirstAcceptedResponse(httpx.AsyncBaseTransport):
    def __init__(self, app) -> None:
        self._inner = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._lost = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if not self._lost and request.method == "POST" and request.url.path.endswith("/turns"):
            self._lost = True
            await response.aread()
            raise httpx.ReadError("fabricated_response_lost", request=request)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class TrackingSSE(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b'event: resync_required\ndata: {"type":"resync_required","reason":"gap"}\n\n'
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class TrackingSSETransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.stream = TrackingSSE()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={"methods": {}, "tiers": {"live": "available", "archive": "unavailable"}},
            )
        return httpx.Response(200, stream=self.stream)

    async def aclose(self) -> None:
        await self.stream.aclose()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_remote_settings_reject_non_finite_timeout(value: float) -> None:
    with pytest.raises(ValueError, match="request_timeout_must_be_positive"):
        RemoteClientSettings(
            "https://tether.example.test",
            TokenProvider("alice"),
            request_timeout_seconds=value,
        )


class BearerCredential:
    def __init__(self, user: str) -> None:
        self.user = user

    async def authorization(self) -> str:
        return f"Bearer {self.user}"


class TokenProvider:
    def __init__(self, user: str) -> None:
        self.user = user
        self.calls = 0

    async def __call__(self) -> BearerCredential:
        self.calls += 1
        return BearerCredential(self.user)


class RequestAuthenticator:
    async def actor_for(self, request: Request) -> Actor:
        header = request.headers.get("authorization", "")
        prefix, _, user = header.partition(" ")
        if prefix.lower() != "bearer" or user not in {"alice", "bob"}:
            raise ApiFailure(401, "authentication_required")
        return Actor(id=user, principal=user)


class RevocableReadPolicy:
    def __init__(self) -> None:
        self.revoked = False

    async def authorize(self, request: AuthzRequest) -> Decision:
        allowed = not self.revoked or request.action != "read_session"
        return Decision(allowed=allowed, reason="fabricated_policy")


def make_settings(path: Path, *, model: ScriptedModel, client_limits: Any, limits: Any):
    context = fabricated_context()
    return ClientSettings(
        model=model,
        storage=LocalJsonlSettings(path),
        policy=AllowAll(),
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated",
                instructions="Respond with fabricated content.",
                model="demo",
            ),
        ),
        default_spec="demo",
    )


@pytest.mark.anyio
async def test_remote_client_executes_against_real_journal_host_and_decodes_counters(
    tmp_path, client_limits, limits
) -> None:
    """@covers US-066-AC1 @covers US-066-AC8"""
    host = await ClientHost.open(
        make_settings(
            tmp_path / "journal",
            model=ScriptedModel([ModelResponse(content="remote reply", finish_reason="stop")]),
            client_limits=client_limits,
            limits=limits,
        )
    )
    provider = TokenProvider("alice")
    api = create_api(
        clients=host.client,
        authenticator=RequestAuthenticator(),
        settings=ApiSettings(),
    )
    transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
        remote = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", provider), http_client=http
        )
        try:
            created = await remote.create(ref="fabricated/ref one", title="Remote")
            assert created.ref == "fabricated/ref one"
            assert isinstance(created.last_seq, int)

            view = await remote.get(created.id)
            assert view.summary.title == "Remote"
            assert view.settings == {}
            snapshot = await remote.snapshot(created.id)
            assert snapshot.through_index == -1
            assert snapshot.seq == created.last_seq
            assert (await remote.entries(created.id)).items == ()
            assert (await remote.history(created.id)).items == ()
            assert await remote.pending(created.id) is None
            assert (await remote.effects(created.id)).items == ()
            assert (await remote.list()).items[0].id == created.id
            assert await remote.find("fabricated/ref one") == created
            expected_methods = {
                "list",
                "get",
                "entries",
                "history",
                "pending",
                "snapshot",
                "effects",
                "call_detail",
                "follow",
                "find",
                "create",
                "send",
                "confirm",
                "resume",
                "resolve",
                "cancel",
                "cancel_turn",
                "update",
                "close",
                "link_history",
                "turn_result",
                "archive_list",
                "restore",
            }
            capabilities = remote.capabilities
            assert set(capabilities.methods) == expected_methods
            assert capabilities.methods == host.client(Actor(id="alice")).capabilities.methods
            assert set(capabilities.methods.values()) == {"supported", "unsupported"}
            assert set(capabilities.tiers) == {"live", "archive"}
            with pytest.raises(Unsupported):
                await remote.list(tier="archive")
            with pytest.raises(UnknownModelCall):
                await remote.call_detail(created.id, ["missing-call"])
            changed = await remote.update(created.id, title="Updated", settings={"large": 2**60})
            assert changed.title == "Updated"
            assert (await remote.get(created.id)).settings["large"] == 2**60
        finally:
            await remote.aclose()
    assert await host.aclose()


@pytest.mark.anyio
async def test_settings_bound_borrowed_http_client_requests() -> None:
    request_timeouts: list[object] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        request_timeouts.append(request.extensions.get("timeout"))
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={"methods": {}, "tiers": {"live": "available", "archive": "unavailable"}},
            )
        return httpx.Response(
            200,
            content=(
                b'event: resync_required\ndata: {"type":"resync_required","reason":"gap"}\n\n'
            ),
        )

    borrowed = httpx.AsyncClient(
        base_url="https://remote.test",
        timeout=0.01,
        transport=httpx.MockTransport(respond),
    )
    remote = await RemoteClient.open(
        RemoteClientSettings(
            "https://remote.test",
            TokenProvider("alice"),
            request_timeout_seconds=0.25,
        ),
        http_client=borrowed,
    )
    try:
        feed = cast(AsyncGenerator[Change, None], remote.follow())
        assert isinstance(await anext(feed), ResyncRequired)
        with pytest.raises(StopAsyncIteration):
            await anext(feed)
        assert len(request_timeouts) == 2
        for timeout in request_timeouts:
            assert isinstance(timeout, dict)
            assert set(timeout.values()) == {0.25}
    finally:
        await remote.aclose()
        assert not borrowed.is_closed
        await borrowed.aclose()


@pytest.mark.anyio
async def test_remote_client_returns_and_closes_an_accepted_turn(
    tmp_path, client_limits, limits
) -> None:
    """@covers US-066-AC2 @covers US-066-AC3"""
    model = ScriptedModel([ModelResponse(content="remote reply", finish_reason="stop")])
    host = await ClientHost.open(
        make_settings(
            tmp_path / "journal",
            model=model,
            client_limits=client_limits,
            limits=limits,
        )
    )
    provider = TokenProvider("alice")
    api = create_api(
        clients=host.client, authenticator=RequestAuthenticator(), settings=ApiSettings()
    )
    transport = LoseFirstAcceptedResponse(api)
    async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
        remote = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", provider), http_client=http
        )
        try:
            created = await remote.create(ref="remote-turn-1", title="Remote")
            with pytest.raises(RemoteTransportError, match="remote_transport_unavailable"):
                await remote.send(
                    created.id, "remote prompt", request_id="remote-send-1", spec="demo"
                )
            handle = await remote.send(
                created.id, "remote prompt", request_id="remote-send-1", spec="demo"
            )
            retry = await remote.send(
                created.id, "remote prompt", request_id="remote-send-1", spec="demo"
            )
            assert retry.turn_id == handle.turn_id
            result = await handle.result()
            assert await retry.result() == result
            assert len(model.requests) == 1
            assert result.stop_reason == "final"
            assert await remote.turn_result(created.id, handle.turn_id) == result
            assert await remote.cancel_turn(created.id, handle.turn_id) is False
            assert await remote.cancel(created.id) is False
            assert (await remote.close(created.id)).status == "closed"
        finally:
            await remote.aclose()
    assert await host.aclose()


@pytest.mark.anyio
async def test_remote_result_survives_api_and_host_restart(tmp_path, client_limits, limits) -> None:
    """@covers US-066-AC6"""
    path = tmp_path / "journal"
    model = ScriptedModel([ModelResponse(content="durable result", finish_reason="stop")])
    host = await ClientHost.open(
        make_settings(path, model=model, client_limits=client_limits, limits=limits)
    )
    api = create_api(
        clients=host.client, authenticator=RequestAuthenticator(), settings=ApiSettings()
    )
    transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
        remote = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", TokenProvider("alice")), http_client=http
        )
        try:
            session = await remote.create(ref="durable-result")
            turn = await remote.send(session.id, "persist this result", request_id="durable-send")
            result = await turn.result()
        finally:
            await remote.aclose()
    assert await host.aclose()

    reopened = await ClientHost.open(
        make_settings(path, model=ScriptedModel([]), client_limits=client_limits, limits=limits)
    )
    replacement_api = create_api(
        clients=reopened.client,
        authenticator=RequestAuthenticator(),
        settings=ApiSettings(),
    )
    replacement_transport = httpx.ASGITransport(app=replacement_api, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=replacement_transport, base_url="http://tether.test"
    ) as http:
        recovered = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", TokenProvider("alice")), http_client=http
        )
        try:
            assert await recovered.turn_result(session.id, turn.turn_id) == result
            assert len(model.requests) == 1
        finally:
            await recovered.aclose()
    assert await reopened.aclose()


@pytest.mark.anyio
async def test_retained_local_and_remote_result_reads_recheck_current_policy(
    tmp_path, client_limits, limits
) -> None:
    """@covers US-066-AC3"""
    model = ScriptedModel([ModelResponse(content="durable reply", finish_reason="stop")])
    policy = RevocableReadPolicy()
    settings = replace(
        make_settings(
            tmp_path / "journal", model=model, client_limits=client_limits, limits=limits
        ),
        policy=policy,
    )
    host = await ClientHost.open(settings)
    local = host.client(Actor(id="alice"))
    session = await local.create(ref="retained-result")
    turn = await local.send(session.id, "complete once", request_id="retained-turn")
    result = await turn.result()
    assert await local.turn_result(session.id, turn.turn_id) == result

    policy.revoked = True
    with pytest.raises(ExecutionDenied, match="policy_denied"):
        await local.turn_result(session.id, turn.turn_id)

    api = create_api(
        clients=host.client, authenticator=RequestAuthenticator(), settings=ApiSettings()
    )
    transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
        remote = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", TokenProvider("alice")), http_client=http
        )
        try:
            with pytest.raises(ExecutionDenied, match="denied"):
                await remote.turn_result(session.id, turn.turn_id)
            assert len(model.requests) == 1
        finally:
            await remote.aclose()
    assert await host.aclose()


@pytest.mark.anyio
async def test_stale_remote_cancellation_leaves_the_new_active_turn_running(
    tmp_path, client_limits, limits
) -> None:
    """@covers US-066-AC7"""
    model = SecondTurnHeldModel()
    settings = make_settings(
        tmp_path / "journal", model=model, client_limits=client_limits, limits=limits
    )
    await run_stale_remote_cancellation(
        settings, model, RequestAuthenticator(), TokenProvider("alice")
    )


@pytest.mark.anyio
async def test_lost_target_cancel_response_retry_cannot_cancel_successor(
    tmp_path, client_limits, limits
) -> None:
    """@covers US-066-AC7"""
    model = TargetCancelThenHoldSuccessorModel()
    settings = make_settings(
        tmp_path / "journal", model=model, client_limits=client_limits, limits=limits
    )
    await run_lost_target_cancel_response_retry(
        settings, model, RequestAuthenticator(), TokenProvider("alice")
    )


@pytest.mark.anyio
async def test_remote_identity_is_refreshed_per_request_and_session_access_is_isolated(
    tmp_path, client_limits, limits
) -> None:
    host = await ClientHost.open(
        make_settings(
            tmp_path / "journal",
            model=ScriptedModel([]),
            client_limits=client_limits,
            limits=limits,
        )
    )
    api = create_api(
        clients=host.client,
        authenticator=RequestAuthenticator(),
        settings=ApiSettings(),
    )
    transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
        alice_provider = TokenProvider("alice")
        alice = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", alice_provider), http_client=http
        )
        bob_provider = TokenProvider("bob")
        bob = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", bob_provider), http_client=http
        )
        try:
            private = await alice.create(ref="alice-only")
            with pytest.raises(UnknownSession):
                await bob.get(private.id)
            await alice.get(private.id)
            assert alice_provider.calls >= 3
            assert bob_provider.calls >= 2
        finally:
            await alice.aclose()
            await bob.aclose()
    assert await host.aclose()


@pytest.mark.anyio
async def test_feed_reconnects_with_only_the_last_durable_commit_cursor() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={
                    "methods": {"follow": "supported"},
                    "tiers": {"live": "available", "archive": "unavailable"},
                },
            )
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"""event: session_removed
id: 12
data: {"type":"session_removed","seq":"12","session_id":"s-1","last_in_commit":true}

""",
        )

    provider = TokenProvider("alice")
    http = httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://tether.test")
    remote = await RemoteClient.open(
        RemoteClientSettings("http://tether.test", provider), http_client=http
    )
    feed = cast(AsyncGenerator[SessionRemoved, None], remote.follow(session_id="s-1", since=4))
    try:
        first = await anext(feed)
        assert isinstance(first, SessionRemoved)
        second = await anext(feed)
        assert isinstance(second, SessionRemoved)
        assert len(requests) == 2
        assert requests[0].url.params["since"] == "4"
        assert requests[0].headers["last-event-id"] == "4"
        assert requests[1].url.params["since"] == "12"
        assert requests[1].headers["last-event-id"] == "12"
        assert provider.calls == 3
    finally:
        await feed.aclose()
        await remote.aclose()
        await http.aclose()


@pytest.mark.anyio
async def test_resync_required_ends_remote_feed_for_snapshot_recovery() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={
                    "methods": {"follow": "supported"},
                    "tiers": {"live": "available", "archive": "unavailable"},
                },
            )
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'event: resync_required\ndata: {"type":"resync_required","reason":"gap"}\n\n',
        )

    provider = TokenProvider("alice")
    http = httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="http://tether.test")
    remote = await RemoteClient.open(
        RemoteClientSettings("http://tether.test", provider), http_client=http
    )
    feed = cast(AsyncGenerator[Change, None], remote.follow(session_id="s-1", since=4))
    try:
        assert isinstance(await anext(feed), ResyncRequired)
        with pytest.raises(StopAsyncIteration):
            await anext(feed)
        assert len(requests) == 1
    finally:
        await feed.aclose()
        await remote.aclose()
        await http.aclose()


@pytest.mark.anyio
async def test_resync_ends_and_closes_nested_http_stream() -> None:
    transport = TrackingSSETransport()
    http = httpx.AsyncClient(transport=transport, base_url="http://tether.test")
    remote = await RemoteClient.open(
        RemoteClientSettings("http://tether.test", TokenProvider("alice")), http_client=http
    )
    feed = cast(AsyncGenerator[Change, None], remote.follow(session_id="s", since=0))
    try:
        assert isinstance(await anext(feed), ResyncRequired)
        with pytest.raises(StopAsyncIteration):
            await anext(feed)
        assert transport.stream.closed
    finally:
        await feed.aclose()
        await remote.aclose()
        await http.aclose()


@pytest.mark.anyio
async def test_remote_protocol_failure_redacts_runtime_credential_marker() -> None:
    marker = f"fabricated-{uuid4().hex}"
    provider = TokenProvider(marker)
    async with httpx.AsyncClient(
        transport=LeakyProtocolFailure(), base_url="https://tether.example.test"
    ) as http:
        with pytest.raises(RemoteTransportError, match="remote_transport_unavailable") as caught:
            await RemoteClient.open(
                RemoteClientSettings("https://tether.example.test", provider),
                http_client=http,
            )
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


@pytest.mark.anyio
async def test_remote_capability_decode_redacts_runtime_payload_marker() -> None:
    marker = f"fabricated-capability-{uuid4().hex}"

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "methods": {"get": marker},
                "tiers": {"live": "available", "archive": "unavailable"},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://tether.example.test"
    ) as http:
        with pytest.raises(RemoteTransportError, match="remote_response_invalid") as caught:
            await RemoteClient.open(
                RemoteClientSettings("https://tether.example.test", TokenProvider("alice")),
                http_client=http,
            )
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_remote_decode_redacts_invalid_payload_marker() -> None:
    from tether.client.remote.wire import decode

    class CountResponse(BaseModel):
        count: int

    marker = f"fabricated-private-response-{uuid4().hex}"
    with pytest.raises(RemoteTransportError, match="remote_response_invalid") as caught:
        decode(CountResponse, {"count": marker})
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
