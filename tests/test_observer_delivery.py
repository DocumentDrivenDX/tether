"""Audit observers cannot acquire execution authority or unbounded delivery capacity."""

from __future__ import annotations

import asyncio
import gc
import weakref
from contextlib import suppress
from contextvars import ContextVar

import pytest

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
        assert delivery.submit(packet(0))
        await entered.wait()
        assert delivery.submit(packet(1))
        assert delivery.submit(packet(2))
        assert not await delivery.aclose()
        assert delivery.stats.active
        assert calls == ["0"]
        assert delivery.stats.timed_out == 1
        assert delivery.stats.dropped_closed == 2
        assert not delivery.submit(packet(3))
        release.set()
        await asyncio.wait_for(delivery.wait_closed(), 1)
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

        class Sink:
            async def emit(self, **kwargs):
                entered.set()
                while not release.is_set():
                    with suppress(asyncio.CancelledError):
                        await release.wait()

        delivery = AuditDelivery(Sink(), TelemetryLimits())
        delivery.bind()
        assert delivery.submit(packet())
        await entered.wait()
        closing = asyncio.create_task(delivery.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert delivery.stats.sealed
        assert delivery.stats.active
        release.set()
        await asyncio.wait_for(delivery.wait_closed(), 1)

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
        entered, surviving = asyncio.Event(), asyncio.Event()
        waiters = []
        released = False

        class Sink:
            async def emit(self, **kwargs):
                entered.set()
                while not released:
                    private = asyncio.get_running_loop().create_future()
                    waiters.append(weakref.ref(private))
                    try:
                        await private
                    except asyncio.CancelledError:
                        surviving.set()

        delivery = AuditDelivery(
            Sink(), TelemetryLimits(delivery_timeout_seconds=0.001, shutdown_timeout_seconds=0.01)
        )
        delivery.bind()
        assert delivery.submit(packet())
        await entered.wait()
        assert not await delivery.aclose()
        await surviving.wait()
        for _ in range(10):
            await asyncio.sleep(0)
        owner = weakref.ref(delivery)
        del delivery
        gc.collect()
        assert owner() is not None
        released = True
        waiters[-1]().set_result(None)
        for _ in range(10):
            await asyncio.sleep(0)
        gc.collect()
        assert owner() is None

    asyncio.run(scenario())
