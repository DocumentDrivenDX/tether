"""Dependency order survives incomplete closes, failures and waiter cancellation."""

import asyncio

import pytest
from tether_databricks import ResourceLifecycle, ShutdownReport

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_for_event_or_operation_end(event: asyncio.Event, operation: asyncio.Task) -> None:
    waiter = asyncio.create_task(event.wait(), name="tether-test-state-barrier")
    try:
        done, _ = await asyncio.wait({waiter, operation}, return_when=asyncio.FIRST_COMPLETED)
        if operation in done:
            if not operation.cancelled():
                operation.result()
            raise AssertionError("operation_ended_before_state_barrier")
        await waiter
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.parametrize("failure", ["incomplete", "exception"])
async def test_incomplete_owner_retains_dependencies_then_retries_only_remaining(
    failure: str,
) -> None:
    released = asyncio.Event()
    observed: list[str] = []

    async def close_owner() -> bool:
        observed.append("owner")
        if not released.is_set():
            if failure == "exception":
                raise RuntimeError("fabricated-close-failure")
            return False
        return True

    async def close_source() -> None:
        observed.append("source")

    lifecycle = ResourceLifecycle({"owner": close_owner, "source": close_source})
    first = await lifecycle.close(timeout_seconds=1800)
    assert first == ShutdownReport(complete=False, outstanding=("owner", "source"))
    assert observed == ["owner"]
    released.set()
    assert await lifecycle.close(timeout_seconds=1800) == ShutdownReport(
        complete=True, closed=("owner", "source")
    )
    assert observed == ["owner", "owner", "source"]
    assert (await lifecycle.close(timeout_seconds=1800)).complete
    assert observed == ["owner", "owner", "source"]


async def test_cancelled_close_waiter_does_not_release_dependency_or_cancel_physical_close() -> (
    None
):
    entered, released = asyncio.Event(), asyncio.Event()
    observed: list[str] = []

    async def close_owner() -> None:
        observed.append("owner")
        entered.set()
        await released.wait()

    async def close_source() -> None:
        observed.append("source")

    lifecycle = ResourceLifecycle({"owner": close_owner, "source": close_source})
    waiting = asyncio.create_task(lifecycle.close(timeout_seconds=1800))
    settled: ShutdownReport | None = None
    try:
        await _wait_for_event_or_operation_end(entered, waiting)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        report = await lifecycle.close(timeout_seconds=0.01)
        assert not report.complete
        assert report.outstanding == ("owner", "source")
        assert observed == ["owner"]
    finally:
        released.set()
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        settled = await lifecycle.close(timeout_seconds=1800)
    assert settled == ShutdownReport(complete=True, closed=("owner", "source"))
    assert observed == ["owner", "source"]
