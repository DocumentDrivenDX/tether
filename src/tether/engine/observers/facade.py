"""One optional observational owner shared by an Engine and its session clients."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from threading import Lock
from weakref import ReferenceType, ref

from tether.types import ModelRequest, TokenUsage
from tether.types.execution import Actor, Entry, NewEntry, SessionState
from tether.types.telemetry import AuditSink, TelemetryLimits, TraceIds, Tracer
from tether.types.usage import UsageLedger

from .delivery import AuditDelivery
from .metadata import Scope, record, text
from .tracing import GuardedTracer


class Observers:
    def __init__(
        self,
        audit: AuditSink | None = None,
        tracer: Tracer | None = None,
        limits: TelemetryLimits | None = None,
    ) -> None:
        self.limits = limits if limits is not None else TelemetryLimits()
        self.delivery = AuditDelivery(audit, self.limits)
        self.tracer = GuardedTracer(tracer)
        self.enabled = audit is not None
        self._gates: OrderedDict[int, tuple[ReferenceType[NewEntry], TraceIds | None]] = (
            OrderedDict()
        )
        self._calls: OrderedDict[tuple[str, str, str], TraceIds | None] = OrderedDict()
        self._lock = Lock()
        self._failures = 0

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def failed(self) -> None:
        with self._lock:
            self._failures += 1

    def remember(self, entry: NewEntry) -> None:
        if not self.enabled:
            return
        try:
            self._gates[id(entry)] = (ref(entry), self.tracer.current_ids())
            while len(self._gates) > self.limits.max_pending_events:
                self._gates.popitem(last=False)
        except BaseException:
            self.failed()

    def remember_call(self, owner: str, session: str, call: str) -> None:
        if not self.enabled:
            return
        try:
            self._calls[(owner, session, call)] = self.tracer.current_ids()
            while len(self._calls) > self.limits.max_pending_events:
                self._calls.popitem(last=False)
        except BaseException:
            self.failed()

    @contextmanager
    def model(
        self,
        request: ModelRequest,
        *,
        owner: str,
        usage: Callable[[str], tuple[TokenUsage | None, bool]],
    ) -> Generator[str | None, None, None]:
        with self.tracer.start_as_current_span(
            "tether.model_call",
            attributes={
                "tether.model_call_id": request.tags.model_call_id,
                "tether.purpose": request.tags.purpose,
                "tether.session_id": request.tags.session_id,
                "tether.principal": request.tags.principal,
            },
        ) as span:
            self.remember_call(owner, request.tags.session_id, request.tags.model_call_id)
            try:
                yield span.traceparent
            except BaseException as error:
                span.error("cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
                raise
            finally:
                try:
                    value, complete = usage(request.tags.model_call_id)
                    span.set_attribute("tether.usage_complete", complete)
                    if value is not None:
                        for field, count in value.model_dump().items():
                            if count is not None:
                                span.set_attribute("tether." + field, count)
                except BaseException:
                    self.failed()

    def accepted(
        self,
        actor: Actor,
        session: SessionState,
        phase: str,
        expected: Sequence[NewEntry],
        accepted: Sequence[Entry],
        ledger: UsageLedger,
        *,
        spec_id: str | None = None,
        spec_version: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        for source, entry in zip(expected, accepted, strict=True):
            try:
                ids = self.tracer.current_ids()
                cached = self._gates.pop(id(source), None)
                if cached is not None and cached[0]() is source:
                    ids = cached[1]
                if entry.kind == "model_call":
                    call = text(entry.facts.get("model_call_id"))
                    if call is not None:
                        ids = self._calls.pop((session.owner, session.id, call), ids)
                scope = Scope(
                    owner=session.owner,
                    session_id=session.id,
                    principal=actor.principal or actor.id,
                    phase_turn_id=phase,
                    spec_id=spec_id or text(entry.facts.get("spec_id")),
                    spec_version=spec_version or text(entry.facts.get("spec_version")),
                )
                packet = record(entry, scope, ledger, ids)
                if packet is not None:
                    self.delivery.submit(packet)
            except BaseException:
                # Admission cannot change a known accepted result or caller cancellation.
                self.failed()
