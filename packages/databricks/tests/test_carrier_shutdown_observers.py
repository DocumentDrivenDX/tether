"""The socket observer settles its request when setup reaches terminal state."""

from __future__ import annotations

import asyncio

import pytest
from packages.databricks.tests.test_carrier_shutdown import _connect_when_listening

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _wait_for_event_or_owner_end(event: asyncio.Event, owner: asyncio.Task) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({waiter, owner}, return_when=asyncio.FIRST_COMPLETED)
        if owner in done:
            if not owner.cancelled():
                owner.result()
            raise AssertionError("connect_owner_ended_before_request_started")
        await waiter
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.parametrize("terminal_state", ["main_returned", "reader_error"])
async def test_connect_readiness_cancels_pending_request_on_observer_terminal(
    terminal_state: str,
) -> None:
    started, request_cancelled = asyncio.Event(), asyncio.Event()

    class Client:
        def build_request(self, *_args, **_kwargs):
            return object()

        async def send(self, *_args, **_kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                request_cancelled.set()

    async def wait_forever() -> None:
        await asyncio.Future()

    async def wait_for_process() -> int:
        await asyncio.Future()
        return 0

    async def fail_reader(queue: asyncio.Queue) -> None:
        await queue.put(("reader_error", "OSError"))

    queue = asyncio.Queue()
    process_terminal = asyncio.create_task(wait_for_process())
    output_reader = (
        asyncio.create_task(fail_reader(queue))
        if terminal_state == "reader_error"
        else asyncio.create_task(wait_forever())
    )
    owner = asyncio.create_task(
        _connect_when_listening(
            Client(), "http://fabricated.invalid/", queue, process_terminal, output_reader, []
        )
    )
    try:
        await _wait_for_event_or_owner_end(started, owner)
        if terminal_state == "main_returned":
            queue.put_nowait(("line", b"FABRICATED_LAUNCHER_MAIN_RETURNED\\n"))
        with pytest.raises(AssertionError):
            await owner
        assert request_cancelled.is_set()
    finally:
        if not owner.done():
            owner.cancel()
        for task in (process_terminal, output_reader):
            if not task.done():
                task.cancel()
        await asyncio.gather(owner, process_terminal, output_reader, return_exceptions=True)
