"""Injected child startup exits and cancellation retain carrier cleanup ownership."""

from __future__ import annotations

import asyncio

import pytest
from tether_databricks import CarrierASGI, DatabricksResources, ResourceLifecycle

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_for_event_or_operation_end(event: asyncio.Event, operation: asyncio.Task) -> None:
    waiter = asyncio.create_task(event.wait(), name="tether-test-startup-state")
    try:
        done, _ = await asyncio.wait({waiter, operation}, return_when=asyncio.FIRST_COMPLETED)
        if operation in done:
            if not operation.cancelled():
                operation.result()
            raise AssertionError("startup_ended_before_failure_state")
        await waiter
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def _child(
    name, scope, receive, send, *, failed_child, exit_kind, entered, release_failed_child, trace
):
    assert scope["type"] == "lifespan"
    assert (await receive())["type"] == "lifespan.startup"
    trace.append(name + "_started")
    if name == failed_child:
        entered.set()
        if exit_kind == "return":
            return
        if exit_kind == "raise":
            raise RuntimeError("fabricated_private_failure")
        if exit_kind == "refuse":
            await send({"type": "lifespan.startup.failed", "message": "fabricated_private_failure"})
        elif exit_kind == "cancel":
            await release_failed_child.wait()
            return
    else:
        await send({"type": "lifespan.startup.complete"})
    assert (await receive())["type"] == "lifespan.shutdown"
    trace.append(name + "_closed")
    await send({"type": "lifespan.shutdown.complete"})


@pytest.mark.parametrize("failed_child", ["api", "ui"])
@pytest.mark.parametrize("exit_kind", ["return", "raise", "refuse", "cancel"])
async def test_startup_failure_closes_owned_dependencies(failed_child, exit_kind):
    trace = []
    entered = asyncio.Event()
    release_failed_child = asyncio.Event()

    async def close_host():
        trace.append("host_closed")
        return True

    async def close_resources():
        trace.append("resources_closed")
        return True

    async def api(scope, receive, send):
        await _child(
            "api",
            scope,
            receive,
            send,
            failed_child=failed_child,
            exit_kind=exit_kind,
            entered=entered,
            release_failed_child=release_failed_child,
            trace=trace,
        )

    async def ui(scope, receive, send):
        await _child(
            "ui",
            scope,
            receive,
            send,
            failed_child=failed_child,
            exit_kind=exit_kind,
            entered=entered,
            release_failed_child=release_failed_child,
            trace=trace,
        )

    async def unused_resource(_ref):
        raise AssertionError("unexpected_resource_acquisition")

    app = CarrierASGI(
        api=api,
        ui=ui,
        api_prefix="/api/v1",
        shutdown_timeout_seconds=1800,
        lifecycle=ResourceLifecycle({"host": close_host, "resources": close_resources}),
        resources=DatabricksResources.from_bindings((), acquire=unused_resource),
    )
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "lifespan.startup"})
    outgoing = []

    async def send(message):
        outgoing.append(message)

    task = asyncio.create_task(app({"type": "lifespan"}, incoming.get, send))
    try:
        if exit_kind == "cancel":
            await _wait_for_event_or_operation_end(entered, task)
            task.cancel()
            release_failed_child.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
            assert outgoing == [
                {"type": "lifespan.startup.failed", "message": "Carrier startup failed"}
            ]
        _assert_startup_cleanup(app, trace, failed_child)
    finally:
        release_failed_child.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _dispose_children(app)


async def _dispose_children(app):
    # The refusing/canceled child may retain shutdown work; dispose only this
    # test's owned task after verifying resource cleanup and the fixed response.
    for lifespan in app._lifespans:
        if lifespan.task is not None and not lifespan.task.done():
            lifespan.task.cancel()
            await asyncio.gather(lifespan.task, return_exceptions=True)


def _assert_startup_cleanup(app, trace, failed_child):
    assert app.shutdown_report is not None
    assert trace[-2:] == ["host_closed", "resources_closed"]
    if failed_child == "ui":
        assert "api_closed" in trace


async def test_child_cancellation_during_shutdown_retains_resource_cleanup():
    closed = []

    async def close_resource():
        closed.append("resource")
        return True

    async def child(scope, receive, send):
        assert (await receive())["type"] == "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        raise asyncio.CancelledError

    async def unused_resource(_ref):
        raise AssertionError("unexpected_resource_acquisition")

    app = CarrierASGI(
        api=child,
        ui=child,
        api_prefix="/api/v1",
        shutdown_timeout_seconds=1800,
        lifecycle=ResourceLifecycle({"resource": close_resource}),
        resources=DatabricksResources.from_bindings((), acquire=unused_resource),
    )
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "lifespan.startup"})
    incoming.put_nowait({"type": "lifespan.shutdown"})
    outgoing = []

    async def send(message):
        outgoing.append(message["type"])

    await app({"type": "lifespan"}, incoming.get, send)
    assert outgoing == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert closed == ["resource"]
    assert app.shutdown_report is not None
    assert not app.shutdown_report.complete
    assert app.shutdown_report.outstanding == ("api", "ui")
