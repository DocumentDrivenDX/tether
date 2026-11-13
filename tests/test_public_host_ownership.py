"""Public host retains accepted work and physical resource ownership."""

import asyncio
import sys
from dataclasses import replace
from threading import Event

import pytest
from public_port_fixtures import FabricatedTool
from resource_lifetime_controls import (
    cancel_and_expect_cancellation,
    close_after_release,
    wait_closed_entry,
)
from tui_event_waits import let_pilot_screen_wait_use_outer_owner, wait_for_event_or_operation

from tether.client import (
    Actor,
    AgentSpec,
    ClientHost,
    ClientPorts,
    ClientSettings,
    ExecutionLimits,
    LocalJsonlSettings,
    ModelResponse,
    RootBusy,
    ToolCall,
    VolatileSettings,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model
from tether.tui import ClientApp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(model, client_limits, model_limits):
    context = fabricated_model_context()
    return ClientSettings(
        model=model,
        storage=VolatileSettings(),
        policy=demo_policy(),
        client_limits=client_limits,
        model_limits=model_limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo", version="1", title="Fabricated", instructions="Fabricated", model="demo"
            ),
        ),
        resume="off",
        default_spec="demo",
    )


class HeldModel:
    provider = "scripted"

    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.delegate = scripted_model(
            [ModelResponse(content="Fabricated final", finish_reason="stop")]
        )

    async def stream(self, request):
        self.entered.set()
        await self.release.wait()
        async for item in self.delegate.stream(request):
            yield item


class ShutdownRead:
    def __init__(self, original, app):
        self.original, self.app = original, app
        self.entered = asyncio.Event()
        self.returned_during_shutdown = False

    async def __call__(self, *args, **kwargs):
        page = await self.original(*args, **kwargs)
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # A completed read may win its cancellation race. Return it after
            # Textual removed the DOM, exactly where the production race occurred.
            self.returned_during_shutdown = not self.app.is_running
            return page


async def test_borrowed_view_close_preserves_other_client_and_accepted_work(
    client_limits, limits, monkeypatch
):
    """@covers US-065-AC3 @covers US-065-AC4"""
    model = HeldModel()
    host = await ClientHost.open(settings(model, client_limits, limits))
    result_task = None
    try:
        with pytest.raises(RuntimeError, match="tether_not_closing"):
            await host.wait_closed()

        async def forbidden_factory(*_args, **_kwargs):
            raise AssertionError("borrower_created_another_runtime")

        monkeypatch.setattr(ClientHost, "open", forbidden_factory)
        monkeypatch.setattr(ClientHost, "from_ports", forbidden_factory)
        first, second = host.client(Actor(id="owner")), host.client(Actor(id="owner"))
        session = await first.create(spec="demo")
        app = ClientApp(first, session_id=session.id)
        let_pilot_screen_wait_use_outer_owner(monkeypatch)
        async with app.run_test():
            read = ShutdownRead(first.list, app)
            monkeypatch.setattr(first, "list", read)
            accepted = await first.send(session.id, "Fabricated request", spec="demo")
            result_task = asyncio.create_task(accepted.result())
            await wait_for_event_or_operation(model.entered, result_task)
            follower = app._follower
            assert follower is not None
            await wait_for_event_or_operation(read.entered, follower)
            assert await second.turn_result(session.id, accepted.turn_id) is None
        assert read.returned_during_shutdown
        # Closing the actual view preserves the shared runtime and its work.
        assert (await second.get(session.id)).summary.status == "running"
        model.release.set()
        assert (await result_task).stop_reason == "final"
        assert (await second.get(session.id)).summary.status == "idle"
    finally:
        await close_after_release(host, model.release.set, result_task, primary=sys.exception())


class HeldTool(FabricatedTool):
    always_ask = False

    def __init__(self, entered, release, loop):
        super().__init__()
        self.entered, self.release, self.loop = entered, release, loop

    def asks_first(self, ctx, args):
        return False

    def run(self, ctx, args):
        self.loop.call_soon_threadsafe(self.entered.set)
        self.release.wait()
        return "Fabricated physical completion"


async def test_public_host_incomplete_close_retains_root_until_physical_worker_exit(
    tmp_path, client_limits, limits
):
    """@covers US-065-AC5"""
    entered, release = asyncio.Event(), Event()
    tool = HeldTool(entered, release, asyncio.get_running_loop())
    model = scripted_model(
        [
            ModelResponse(
                tool_calls=(ToolCall(id="held", name=tool.name, arguments={"label": "fake"}),),
                finish_reason="tool_calls",
            ),
            ModelResponse(content="Fabricated final", finish_reason="stop"),
        ]
    )
    config = replace(
        settings(model, client_limits, limits),
        storage=LocalJsonlSettings(tmp_path),
        client_limits=client_limits.model_copy(update={"shutdown_timeout_seconds": 0.01}),
        tools=(tool,),
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated",
                instructions="Fabricated",
                model="demo",
                tools=(tool.name,),
            ),
        ),
    )
    host = await ClientHost.from_ports(
        config, ports=ClientPorts(execution_limits=ExecutionLimits(shutdown_timeout_seconds=0.01))
    )
    handle = None
    result_task = None
    waiter = None
    try:
        client = host.client(Actor(id="owner"))
        session = await client.create(spec="demo")
        handle = await client.send(session.id, "Fabricated request", spec="demo")
        result_task = asyncio.create_task(handle.result())
        await wait_for_event_or_operation(entered, result_task)
        assert host.store.workers.busy(session.id)
        assert await host.aclose() is False
        assert not host.drained

        waiter = await wait_closed_entry(host)
        await cancel_and_expect_cancellation(waiter)
        assert not host.drained
        with pytest.raises(RootBusy, match="local_root_already_open"):
            await ClientHost.open(config)
    finally:
        await close_after_release(host, release.set, result_task, waiter, primary=sys.exception())
    assert host.drained
    # Reopening through the same public factory proves the physical root was freed.
    async with await ClientHost.open(config) as reopened:
        assert (await reopened.client(Actor(id="owner")).get(session.id)).summary.id == session.id
    assert handle.turn_id
