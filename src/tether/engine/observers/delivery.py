"""Finite audit admission/delivery with a permit retained through actual sink exit."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextvars import Context
from threading import Lock

from tether.ports.telemetry import NoopAuditSink
from tether.types.canonical import canonical
from tether.types.telemetry import AuditRecord, AuditSink, DeliveryStats, TelemetryLimits

Key = tuple[str, str | None, str]


class AuditDelivery:
    def __init__(self, sink: AuditSink | None, limits: TelemetryLimits) -> None:
        self.sink = sink if sink is not None else NoopAuditSink()
        self.limits = limits.model_copy(deep=True)
        self._disabled = sink is None
        self._lock = Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._changed = asyncio.Event()
        self._sealed = self._aborted = False
        self._pending = 0
        self._queue: deque[tuple[Key, AuditRecord]] = deque()
        self._inflight: set[Key] = set()
        self._recent: OrderedDict[Key, None] = OrderedDict()
        self._active: asyncio.Task[None] | None = None
        self._runner: asyncio.Task[None] | None = None
        self._retainer: asyncio.TimerHandle | None = None
        self._counts = {
            k: 0
            for k in (
                "enqueued",
                "delivered",
                "failed",
                "timed_out",
                "dropped_full",
                "dropped_bytes",
                "dropped_closed",
                "duplicates",
            )
        }

    def bind(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise RuntimeError("wrong_event_loop")

    @property
    def stats(self) -> DeliveryStats:
        with self._lock:
            return DeliveryStats.model_validate(
                dict(
                    self._counts,
                    pending=self._pending,
                    active=self._active is not None,
                    sealed=self._sealed,
                    disabled=self._disabled,
                )
            )

    def _count(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def _packet(self, record: AuditRecord) -> AuditRecord | None:
        try:
            encoded = canonical(record.model_dump(mode="python"))
            if len(encoded) > self.limits.max_event_bytes:
                self._count("dropped_bytes")
                return None
            return AuditRecord.model_validate_json(encoded)
        except Exception:
            self._count("failed")
            return None

    def _reserve(self, key: Key) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            loop = self._loop
            if self._sealed or loop is None or loop.is_closed():
                self._counts["dropped_closed"] += 1
                return None
            if key in self._inflight or key in self._recent:
                self._counts["duplicates"] += 1
                return None
            if self._pending >= self.limits.max_pending_events:
                self._counts["dropped_full"] += 1
                return None
            self._pending += 1
            self._inflight.add(key)
            self._counts["enqueued"] += 1
            return loop

    def submit(self, record: AuditRecord) -> bool:
        if self._disabled:
            return False
        packet = self._packet(record)
        if packet is None:
            return False
        key = (packet.context.actor, packet.context.session_id, packet.context.event_id)
        loop = self._reserve(key)
        if loop is None:
            return False
        try:
            loop.call_soon_threadsafe(self._admit, key, packet, context=Context())
        except Exception:
            with self._lock:
                self._pending -= 1
                self._inflight.discard(key)
                self._counts["dropped_closed"] += 1
            return False
        return True

    def _remember(self, key: Key) -> None:
        self._inflight.discard(key)
        self._recent[key] = None
        self._recent.move_to_end(key)
        while len(self._recent) > self.limits.dedup_capacity:
            self._recent.popitem(last=False)

    def _admit(self, key: Key, record: AuditRecord) -> None:
        with self._lock:
            if self._aborted:
                self._pending -= 1
                self._counts["dropped_closed"] += 1
                self._remember(key)
            else:
                self._queue.append((key, record))
        self._changed.set()
        self._kick()
        self._retain_until_drained()

    def _kick(self) -> None:
        if self._runner is not None or self._active is not None or not self._queue:
            return
        coroutine = self._run()
        try:
            self._runner = asyncio.create_task(coroutine, context=Context())
            self._runner.add_done_callback(self._runner_done, context=Context())
        except Exception:
            coroutine.close()
            self._count("failed")
            self._abort()

    async def _emit(self, packet: AuditRecord) -> None:
        await self.sink.emit(context=packet.context, kind=packet.kind, detail=packet.detail)

    async def _run(self) -> None:
        await asyncio.sleep(0)
        while self._queue and self._active is None:
            with self._lock:
                key, packet = self._queue.popleft()
                self._pending -= 1
            coroutine = self._emit(packet)
            try:
                task = asyncio.create_task(coroutine, context=Context())
            except Exception:
                coroutine.close()
                with self._lock:
                    self._remember(key)
                raise
            with self._lock:
                self._active = task
            task.add_done_callback(lambda done, k=key: self._emitted(k, done), context=Context())
            _, pending = await asyncio.wait({task}, timeout=self.limits.delivery_timeout_seconds)
            if pending:
                self._count("timed_out")
                task.cancel()
                return

    def _emitted(self, key: Key, task: asyncio.Task[None]) -> None:
        failed = task.cancelled() or task.exception() is not None
        with self._lock:
            self._active = None
            self._remember(key)
            self._counts["failed" if failed else "delivered"] += 1
        self._changed.set()
        self._kick()
        self._retain_until_drained()

    def _runner_done(self, task: asyncio.Task[None]) -> None:
        failed = task.cancelled() or task.exception() is not None
        self._runner = None
        if failed:
            self._count("failed")
            self._abort()
        self._changed.set()
        self._kick()
        self._retain_until_drained()

    def _abort(self) -> None:
        with self._lock:
            self._aborted = self._sealed = True
            while self._queue:
                key, _ = self._queue.popleft()
                self._pending -= 1
                self._counts["dropped_closed"] += 1
                self._remember(key)
            active = self._active
        if active is not None and not active.done():
            active.cancel()
        self._changed.set()

    def _drained(self) -> bool:
        with self._lock:
            return self._pending == 0 and self._active is None and self._runner is None

    def _retain_until_drained(self) -> None:
        # asyncio tracks tasks weakly. One loop-owned timer keeps this owner alive
        # through cancellation-resistant emissions, including after bounded close.
        if self._drained():
            if self._retainer is not None:
                self._retainer.cancel()
                self._retainer = None
        elif self._retainer is None and self._loop is not None:
            self._retainer = self._loop.call_later(1, self._retention_tick, context=Context())

    def _retention_tick(self) -> None:
        self._retainer = None
        self._retain_until_drained()

    async def wait_closed(self) -> None:
        self.bind()
        if not self._sealed:
            raise RuntimeError("observers_not_closing")
        while True:
            self._changed.clear()
            if self._drained():
                return
            await self._changed.wait()

    async def aclose(self, *, deadline: float | None = None) -> bool:
        self.bind()
        with self._lock:
            self._sealed = True
        loop = asyncio.get_running_loop()
        end = loop.time() + self.limits.shutdown_timeout_seconds
        if deadline is not None:
            end = min(end, deadline)
        try:
            async with asyncio.timeout_at(end):
                await self.wait_closed()
        except TimeoutError:
            self._abort()
            return self._drained()
        except BaseException:
            self._abort()
            raise
        return True
