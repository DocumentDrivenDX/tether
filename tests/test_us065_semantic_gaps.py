"""Focused regressions for public client and embedded/remote UI semantics."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

import httpx
import pytest
from fastapi import Request
from public_port_fixtures import (
    FabricatedTool,
    RecordingAudit,
    RecordingContext,
    RecordingGuard,
    RecordingHistory,
    RecordingModel,
    RecordingPolicy,
    RecordingProvider,
    RecordingStore,
)
from tether_web_api import ApiSettings, create_api
from textual.widgets import Button, Input, RichLog

from tether.cli.demo import open_demo
from tether.client import (
    Actor,
    AgentSpec,
    ClientHost,
    ClientPorts,
    ClientProtocol,
    ClientSettings,
    ExecutionDenied,
    ModelResponse,
    ProvidedStorage,
    ToolCall,
    VolatileSettings,
)
from tether.client.remote import RemoteClient, RemoteClientSettings
from tether.client.testing import fabricated_model_context, fabricated_storage, scripted_model
from tether.tui import ClientApp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _settings(client_limits, limits, *, storage, model, policy=None, tools=()):
    context = fabricated_model_context()
    return ClientSettings(
        model=model,
        storage=storage,
        policy=policy or RecordingPolicy(),
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated",
                instructions="Fabricated",
                model="demo",
                tools=tuple(tool.name for tool in tools),
            ),
        ),
        tools=tuple(tools),
        resume="off",
        default_spec="demo",
    )


async def _link_history_success(client):
    session = await client.create(ref="history-link", spec="demo")
    first = await client.send(session.id, "Same fabricated message", request_id="first")
    assert (await first.result()).stop_reason == "final"
    second = await client.send(session.id, "Same fabricated message", request_id="second")
    assert (await second.result()).stop_reason == "final"
    entries = (await client.entries(session.id)).items
    messages = [entry for entry in entries if entry.kind == "message" and entry.history_ref]
    user_messages = [entry for entry in messages if entry.facts.get("role") == "user"]
    assert len(user_messages) == 2
    previous, verified = user_messages[0], user_messages[1]

    # @covers US-065-AC2: a successful consumer-visible link resolves to the equivalent record.
    linked = await client.link_history(
        session.id,
        previous.index,
        previous.history_ref,
        verified.history_ref,
        key="fabricated-equivalent-link",
    )
    assert linked.kind == "history_linked"
    page = await client.history(session.id)
    linked_slot = next(item for item in page.items if item.entry_index == previous.index)
    assert linked_slot.value.ref == verified.history_ref
    assert linked_slot.value.record is not None


@pytest.mark.parametrize("injected", [False, True], ids=["ordinary", "alternate-ports"])
async def test_successful_history_link_through_both_public_construction_paths(
    client_limits, limits, injected
):
    model = RecordingModel(
        scripted_model(
            [
                ModelResponse(content="Fabricated answer", finish_reason="stop"),
                ModelResponse(content="Fabricated answer", finish_reason="stop"),
            ]
        )
    )
    settings = _settings(
        client_limits,
        limits,
        storage=VolatileSettings(),
        model=model,
    )
    actor = Actor(id="fabricated-owner")
    if not injected:
        async with await ClientHost.open(settings) as host:
            await _link_history_success(host.client(actor))
        return

    async with fabricated_storage() as raw:
        store, history = RecordingStore(raw.store), RecordingHistory(raw.history)
        borrowed = replace(settings, storage=ProvidedStorage(store=store, history=history))
        ports = ClientPorts(
            providers=(RecordingProvider(),),
            guards=(RecordingGuard(),),
            audit=RecordingAudit(),
            context=RecordingContext(),
        )
        async with await ClientHost.from_ports(borrowed, ports=ports) as host:
            await _link_history_success(host.client(actor))
        assert history.calls["put"] >= 4


@pytest.mark.parametrize("injected", [False, True], ids=["ordinary", "alternate-ports"])
async def test_revoked_retained_session_refuses_send_before_model_or_tool_dispatch(
    client_limits, limits, injected
):
    """@covers US-065-AC6: revocation applies to a retained client/session."""
    tool = FabricatedTool()
    model = RecordingModel(
        scripted_model(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(id="fabricated-call", name=tool.name, arguments={"label": "fake"}),
                    ),
                    finish_reason="tool_calls",
                ),
                ModelResponse(content="Fabricated final", finish_reason="stop"),
            ]
        )
    )
    policy = RecordingPolicy()
    settings = _settings(
        client_limits,
        limits,
        storage=VolatileSettings(),
        model=model,
        policy=policy,
        tools=(tool,),
    )
    actor = Actor(id="fabricated-owner")

    async def check(client):
        session = await client.create(ref="retained-revocation", spec="demo")
        policy.revoked = True
        with pytest.raises(ExecutionDenied, match="policy_denied"):
            await client.send(session.id, "Should not dispatch", request_id="revoked")
        assert model.requests == []
        assert tool.runs == []

    if not injected:
        async with await ClientHost.open(settings) as host:
            retained_client = host.client(actor)
            await check(retained_client)
        return

    async with fabricated_storage() as raw:
        store, history = RecordingStore(raw.store), RecordingHistory(raw.history)
        settings = replace(settings, storage=ProvidedStorage(store=store, history=history))
        ports = ClientPorts(
            providers=(RecordingProvider(),),
            guards=(RecordingGuard(),),
            audit=RecordingAudit(),
            context=RecordingContext(),
        )
        async with await ClientHost.from_ports(settings, ports=ports) as host:
            retained_client = host.client(actor)
            await check(retained_client)


class _Authenticator:
    async def actor_for(self, request: Request) -> Actor:
        assert request.headers["authorization"] == " ".join(("Bearer", "fabricated-ui"))
        return Actor(id="demo")


class _CapturedHandle:
    def __init__(self, handle: Any, owner: _CapturedClient) -> None:
        self._handle, self._owner = handle, owner

    @property
    def turn_id(self) -> str:
        return self._handle.turn_id

    async def result(self):
        result = await self._handle.result()
        self._owner.results.append(result)
        return result


class _CapturedClient:
    def __init__(self, client: Any) -> None:
        self._client = client
        self.results: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def send(self, *args: Any, **kwargs: Any):
        return _CapturedHandle(await self._client.send(*args, **kwargs), self)

    async def confirm(self, *args: Any, **kwargs: Any):
        handle = await self._client.confirm(*args, **kwargs)
        return None if handle is None else _CapturedHandle(handle, self)


async def _run_tui_journey(client: _CapturedClient, session_id: str):
    app = ClientApp(cast(ClientProtocol, client), session_id=session_id)
    async with asyncio.timeout(20), app.run_test() as pilot:
        app.query_one("#message", Input).value = "Same fabricated UI prompt"
        await pilot.click("#send")
        await pilot.pause()
        assert not app.query_one("#approve", Button).disabled
        await pilot.click("#approve")
        await pilot.pause()
        assert app.query_one("#approve", Button).disabled
        rendered = tuple(str(line) for line in app.query_one("#transcript", RichLog).lines)
    assert len(client.results) == 2
    return client.results[-1], rendered


def _semantic_result(result):
    return (
        result.stop_reason,
        result.reason,
        tuple(
            (
                message.role,
                message.content,
                message.is_error,
                tuple((call.name, call.arguments) for call in message.tool_calls),
            )
            for message in result.messages
        ),
        tuple((item.name, item.arguments, item.reason) for item in result.pending),
    )


async def test_local_and_remote_tui_return_the_same_typed_final_result(tmp_path):
    """@covers US-065-AC7: one deterministic fixture, embedded and HTTP-backed clients."""
    async with open_demo(tmp_path) as (host, actor, tool):
        local_client = _CapturedClient(host.client(actor))
        local_session = await local_client.create(ref="local-ui", spec="demo")

        remote_transport = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_api(
                    clients=host.client,
                    authenticator=_Authenticator(),
                    settings=ApiSettings(),
                )
            ),
            base_url="http://fabricated.test",
        )
        try:
            remote = await RemoteClient.open(
                RemoteClientSettings(
                    base_url="http://fabricated.test",
                    credential_provider=_fabricated_credential,
                ),
                http_client=remote_transport,
            )
            async with remote:
                remote_client = _CapturedClient(remote)
                remote_session = await remote_client.create(ref="remote-ui", spec="demo")
                local_result, local_rendered = await _run_tui_journey(
                    local_client, local_session.id
                )
                remote_result, remote_rendered = await _run_tui_journey(
                    remote_client, remote_session.id
                )
                assert _semantic_result(local_result) == _semantic_result(remote_result)
                assert local_result.stop_reason == "final"
                assert local_result.messages[-1].content == "The fabricated record is complete."
                assert any("The fabricated record is complete." in line for line in local_rendered)
                assert any("The fabricated record is complete." in line for line in remote_rendered)
                assert tool.recorded == ["seedling", "seedling"]
        finally:
            await remote_transport.aclose()


class _FabricatedCredential:
    async def authorization(self) -> str:
        return " ".join(("Bearer", "fabricated-ui"))


async def _fabricated_credential() -> _FabricatedCredential:
    return _FabricatedCredential()
