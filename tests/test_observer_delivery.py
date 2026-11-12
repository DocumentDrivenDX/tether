"""Audit observers cannot acquire execution authority or unbounded delivery capacity."""

from __future__ import annotations

import asyncio
import gc
import weakref
from contextlib import suppress
from contextvars import ContextVar

import pytest
from selected_profiles_process import await_retained

from tether.engine.observers.delivery import AuditDelivery
from tether.testing.telemetry import ListAuditSink
from tether.types.telemetry import AuditContext, AuditRecord, TelemetryLimits


def packet(index=0, *, actor="owner", detail=None):
    return AuditRecord(
        context=AuditContext(
            actor=actor, principal=actor, session_id="session", event_id=str(index)
        ),
        kind="authorization",
        detail={} if detail is None else detail,
    )


async def wait_event_or_owner(event: asyncio.Event, owner: asyncio.Task) -> None:
    waiter = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait((waiter, owner), return_when=asyncio.FIRST_COMPLETED)
        if owner in done and waiter not in done:
            await owner
            pytest.fail("observation owner completed before its event")
        assert waiter.result()
    finally:
        await await_retained(asyncio.create_task(settle_waiter(waiter)))


async def settle_waiter(waiter: asyncio.Task[bool]) -> None:
    if not waiter.done():
        waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)


async def close_delivery(delivery: AuditDelivery) -> None:
    await delivery.aclose()
    await delivery.wait_closed()


async def close_delivery_retained(delivery: AuditDelivery) -> None:
    await await_retained(asyncio.create_task(close_delivery(delivery)))


async def submit_and_wait_admission(delivery: AuditDelivery, record: AuditRecord) -> None:
    admitted = asyncio.create_task(delivery._changed.wait())
    try:
        assert delivery.submit(record)
        await admitted
    finally:
        await await_retained(asyncio.create_task(settle_waiter(admitted)))


async def release_abandoned_delivery(
    delivery: AuditDelivery,
    waiters: list[weakref.ReferenceType[asyncio.Future[None]]],
) -> None:
    pending = waiters[-1]()
    assert pending is not None
    pending.set_result(None)
    await delivery.wait_closed()


async def wait_for_active_delivery(
    delivery: AuditDelivery, entered: asyncio.Event
) -> tuple[asyncio.Task[None], asyncio.Task[None]]:
    runner = delivery._runner
    assert runner is not None
    await wait_event_or_owner(entered, runner)
    active = delivery._active
    assert active is not None
    return runner, active


async def settle_closing_and_delivery(
    delivery: AuditDelivery, closing: asyncio.Task[bool] | None
) -> None:
    if closing is not None:
        if not closing.done():
            closing.cancel()
        await asyncio.gather(closing, return_exceptions=True)
    await close_delivery(delivery)


def test_default_disabled_delivery_starts_no_tasks_or_records():
    async def scenario():
        delivery = AuditDelivery(None, TelemetryLimits())
        delivery.bind()
        assert not delivery.submit(packet())
        assert delivery.stats.disabled
        assert delivery.stats.enqueued == 0
        assert await delivery.aclose()
        await delivery.wait_closed()
        assert not delivery.stats.active

    asyncio.run(scenario())


def test_copied_packets_dedup_and_falsey_port():
    class Sink(ListAuditSink):
        def __bool__(self):
            return False

    async def scenario():
        sink = Sink(capacity=4)
        delivery = AuditDelivery(sink, TelemetryLimits())
        delivery.bind()
        original = packet(detail={"count": 1})
        assert delivery.submit(original)
        original.detail["count"] = 9
        assert not delivery.submit(original)
        assert delivery.submit(packet(1, actor="other"))
        assert await delivery.aclose()
        assert sink.records[0].detail == {"count": 1}
        assert [r.context.actor for r in sink.records] == ["owner", "other"]
        assert delivery.stats.duplicates == 1
        assert delivery.stats.delivered == 2

    asyncio.run(scenario())


def test_pending_thread_callbacks_are_bounded_before_loop_runs_them(monkeypatch):
    async def scenario():
        sink = ListAuditSink(capacity=8)
        delivery = AuditDelivery(sink, TelemetryLimits(max_pending_events=2))
        delivery.bind()
        accepted = []

        def worker():
            accepted.extend(delivery.submit(packet(i)) for i in range(20))

        loop = asyncio.get_running_loop()
        schedule = loop.call_soon_threadsafe
        held = []

        def hold(callback, *args, context=None):
            if callback == delivery._admit:
                held.append((callback, args, context))
                return None
            return schedule(callback, *args, context=context)

        monkeypatch.setattr(loop, "call_soon_threadsafe", hold)
        await asyncio.to_thread(worker)
        assert sum(accepted) == 2
        assert len(held) == delivery.stats.pending == 2
        assert delivery.stats.dropped_full == 18
        for callback, args, context in held:
            context.run(callback, *args)
        assert await delivery.aclose()
        assert len(sink.records) == 2

    asyncio.run(scenario())


def test_timeout_keeps_one_physical_delivery_until_actual_exit():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Sink:
            async def emit(self, *, context, kind, detail):
                calls.append(context.event_id)
                entered.set()
                while not release.is_set():
                    with suppress(asyncio.CancelledError):
                        await release.wait()

        delivery = AuditDelivery(
            Sink(),
            TelemetryLimits(
                max_pending_events=2, delivery_timeout_seconds=0.01, shutdown_timeout_seconds=0.02
            ),
        )
        delivery.bind()
        try:
            await submit_and_wait_admission(delivery, packet(0))
            assert delivery._runner is not None
            await wait_event_or_owner(entered, delivery._runner)
            assert delivery.submit(packet(1))
            assert delivery.submit(packet(2))
            assert not await delivery.aclose()
            assert delivery.stats.active
            assert calls == ["0"]
            assert delivery.stats.timed_out == 1
            assert delivery.stats.dropped_closed == 2
            assert not delivery.submit(packet(3))
        finally:
            release.set()
            await close_delivery_retained(delivery)
        assert not delivery.stats.active
        assert delivery.stats.pending == 0
        assert calls == ["0"]

    asyncio.run(scenario())


def test_failure_is_observed_without_reexecution_or_packet_aliases():
    async def scenario():
        calls = []

        class Sink:
            async def emit(self, *, context, kind, detail):
                calls.append(context.event_id)
                detail["changed"] = True
                raise RuntimeError("fabricated content failure marker")

        delivery = AuditDelivery(Sink(), TelemetryLimits())
        delivery.bind()
        source = packet()
        assert delivery.submit(source)
        assert await delivery.aclose()
        assert source.detail == {}
        assert calls == ["0"]
        assert delivery.stats.failed == 1
        assert "fabricated" not in delivery.stats.model_dump_json()

    asyncio.run(scenario())


def test_emission_has_no_first_actor_ambient_context():
    ambient = ContextVar("fabricated_ambient", default="none")

    async def scenario():
        observed = []

        class Sink:
            async def emit(self, *, context, kind, detail):
                observed.append((context.actor, ambient.get()))

        delivery = AuditDelivery(Sink(), TelemetryLimits())
        delivery.bind()
        ambient.set("private-owner-one")
        assert delivery.submit(packet(1, actor="one"))
        ambient.set("private-owner-two")
        assert delivery.submit(packet(2, actor="two"))
        assert await delivery.aclose()
        assert observed == [("one", "none"), ("two", "none")]

    asyncio.run(scenario())


def test_packet_size_and_invalid_envelopes_do_not_throw():
    async def scenario():
        delivery = AuditDelivery(ListAuditSink(capacity=2), TelemetryLimits(max_event_bytes=512))
        delivery.bind()
        assert not delivery.submit(packet(detail={"oversized": "x" * 1024}))
        invalid = packet().model_copy(update={"detail": {"nonfinite": float("nan")}})
        assert not delivery.submit(invalid)
        assert delivery.stats.dropped_bytes == 1
        assert delivery.stats.failed == 1
        assert await delivery.aclose()

    asyncio.run(scenario())


def test_caller_cancellation_seals_and_retains_actual_emission():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        closing_entered = asyncio.Event()
        caught_cancel: list[bool] = []

        class Sink:
            async def emit(self, **kwargs):
                entered.set()
                while not release.is_set():
                    with suppress(asyncio.CancelledError):
                        await release.wait()

        delivery = AuditDelivery(Sink(), TelemetryLimits())
        delivery.bind()
        original_wait_closed = delivery.wait_closed

        async def observe_wait_closed():
            closing_entered.set()
            try:
                await original_wait_closed()
            except asyncio.CancelledError:
                caught_cancel.append(True)
                raise

        delivery.wait_closed = observe_wait_closed
        closing: asyncio.Task[bool] | None = None
        try:
            await submit_and_wait_admission(delivery, packet())
            assert delivery._runner is not None
            await wait_event_or_owner(entered, delivery._runner)
            closing = asyncio.create_task(delivery.aclose())
            await wait_event_or_owner(closing_entered, closing)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert caught_cancel == [True]
            assert delivery.stats.sealed
            assert delivery.stats.active
        finally:
            release.set()
            await await_retained(
                asyncio.create_task(settle_closing_and_delivery(delivery, closing))
            )

    asyncio.run(scenario())


def test_wrong_loop_and_wait_before_seal_refuse():
    delivery = AuditDelivery(None, TelemetryLimits())

    async def first():
        delivery.bind()
        with pytest.raises(RuntimeError, match="observers_not_closing"):
            await delivery.wait_closed()

    asyncio.run(first())
    with pytest.raises(RuntimeError, match="wrong_event_loop"):
        asyncio.run(delivery.aclose())


def test_loop_retains_abandoned_delivery_until_actual_exit():
    async def scenario():
        entered = asyncio.Event()
        first_reentered, second_reentered = asyncio.Event(), asyncio.Event()
        waiters = []
        released = False
        cancellations = 0

        class Sink:
            async def emit(self, **kwargs):
                nonlocal cancellations
                entered.set()
                while not released:
                    private = asyncio.get_running_loop().create_future()
                    waiters.append(weakref.ref(private))
                    if cancellations == 1:
                        first_reentered.set()
                    elif cancellations == 2:
                        second_reentered.set()
                    try:
                        await private
                    except asyncio.CancelledError:
                        cancellations += 1

        delivery = AuditDelivery(
            Sink(), TelemetryLimits(delivery_timeout_seconds=0.001, shutdown_timeout_seconds=0.01)
        )
        delivery.bind()
        owner = weakref.ref(delivery)
        try:
            await submit_and_wait_admission(delivery, packet())
            runner, active = await wait_for_active_delivery(delivery, entered)
            await wait_event_or_owner(first_reentered, active)
            assert not await delivery.aclose()
            await wait_event_or_owner(second_reentered, active)
            del runner, active
            del delivery
            gc.collect()
            assert owner() is not None
            released = True
            retained_delivery = owner()
            assert retained_delivery is not None
            await release_abandoned_delivery(retained_delivery, waiters)
            del retained_delivery
            gc.collect()
            assert owner() is None
        finally:
            released = True
            for waiter_ref in waiters:
                waiter = waiter_ref()
                if waiter is not None and not waiter.done():
                    waiter.set_result(None)
            retained_delivery = owner()
            if retained_delivery is not None:
                await close_delivery_retained(retained_delivery)

    asyncio.run(scenario())
