"""Controls for the benchmark's per-consumer streaming integrity checks."""

import asyncio
from datetime import UTC, datetime

import pytest
from benchmark_streaming import _consume_streaming_turn

from tether.client import EntriesAppended, Lagged, ReplyReset, ReplyText, ResyncRequired
from tether.types.execution import Entry


def _terminal(turn_id: str) -> Entry:
    return Entry(
        index=4,
        at=datetime.now(UTC),
        kind="turn_ended",
        turn_id=turn_id,
        facts={"stop_reason": "final"},
    )


async def _events(values: list[object], closed: asyncio.Event | None = None):
    try:
        for value in values:
            yield value
    finally:
        if closed is not None:
            closed.set()


def test_each_consumer_requires_exact_order_and_persisted_terminal_boundary() -> None:
    async def run() -> None:
        ready: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        closed = asyncio.Event()
        result = await _consume_streaming_turn(
            _events(
                [
                    ReplyText(session_id="s", turn_id="t", attempt_id="a", offset=0, text="x" * 73),
                    ReplyText(
                        session_id="s", turn_id="t", attempt_id="a", offset=73, text="x" * 127
                    ),
                    EntriesAppended(
                        seq=9,
                        session_id="s",
                        entries=(_terminal("t"),),
                        last_in_commit=True,
                    ),
                ],
                closed,
            ),
            ready,
        )
        assert result == ("t", "x" * 200)
        assert ready.qsize() == 1
        assert closed.is_set()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ([Lagged(missed=1)], "streaming_follower_missed_events"),
        ([ResyncRequired(reason="gap")], "streaming_follower_resync_required"),
        (
            [ReplyReset(session_id="s", turn_id="t", attempt_id="a", reason="outage")],
            "streaming_follower_reply_reset",
        ),
        (
            [ReplyText(session_id="s", turn_id="t", attempt_id="a", offset=1, text="x")],
            "streaming_follower_reply_offset_gap",
        ),
    ],
)
def test_consumer_rejects_lag_reset_and_out_of_order_reply(
    values: list[object], error: str
) -> None:
    async def run() -> None:
        ready: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        with pytest.raises(RuntimeError, match=error):
            await _consume_streaming_turn(_events(values), ready)

    asyncio.run(run())


def test_consumer_requires_terminal_entry_for_its_turn() -> None:
    async def run() -> None:
        ready: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        with pytest.raises(RuntimeError, match="streaming_follower_terminal_boundary_missing"):
            await _consume_streaming_turn(
                _events(
                    [
                        ReplyText(session_id="s", turn_id="t", attempt_id="a", offset=0, text="x"),
                        EntriesAppended(
                            seq=9,
                            session_id="s",
                            entries=(_terminal("other"),),
                            last_in_commit=True,
                        ),
                    ]
                ),
                ready,
            )

    asyncio.run(run())


def test_consumer_closes_owned_follow_stream_after_cancellation() -> None:
    async def run() -> None:
        ready: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        closed = asyncio.Event()
        never = asyncio.Event()

        async def held_events():
            try:
                yield ReplyText(session_id="s", turn_id="t", attempt_id="a", offset=0, text="x")
                await never.wait()
            finally:
                closed.set()

        consumer = asyncio.create_task(_consume_streaming_turn(held_events(), ready))
        await ready.get()
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert closed.is_set()
        assert consumer.done()

    asyncio.run(run())
