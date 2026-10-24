"""Scalar lifecycle observations distinguish waiting, body exit and owned completion."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Literal

from tether.types import JsonValue
from tether.types.canonical import digest
from tether.types.telemetry import AuditContext, AuditRecord, TraceIds, TraceScalar

from .delivery import AuditDelivery
from .metadata import Scope
from .tracing import GuardedTracer

Outcome = Literal["returned", "raised", "cancelled"]
Phase = Literal["entered", "exited", "completed", "outstanding", "refused"]


@dataclass(frozen=True)
class Dispatch:
    scope: Scope
    source_turn_id: str | None
    source_position: int
    call_position: int
    invocation_id: str
    attempt_id: str
    intent_position: int | None
    ids: TraceIds | None


class WorkerObservation:
    def __init__(self, dispatch: Dispatch, delivery: AuditDelivery, tracer: GuardedTracer) -> None:
        self.dispatch, self.delivery, self.tracer = dispatch, delivery, tracer
        self._lock = Lock()
        self._ids = dispatch.ids
        self._entered = self._awaitable = self._abandoned = False

    def _attributes(self) -> dict[str, TraceScalar]:
        d, s = self.dispatch, self.dispatch.scope
        attributes: dict[str, TraceScalar] = {
            "tether.owner": s.owner,
            "tether.principal": s.principal,
            "tether.session_id": s.session_id,
            "tether.phase_turn_id": s.phase_turn_id,
            "tether.invocation_id": d.invocation_id,
            "tether.worker_attempt_id": d.attempt_id,
        }
        for field, value in (
            ("source_turn_id", d.source_turn_id),
            ("spec_id", s.spec_id),
            ("spec_version", s.spec_version),
        ):
            if value is not None:
                attributes["tether." + field] = value
        return attributes

    def emit(self, phase: Phase, outcome: Outcome | None = None) -> None:
        try:
            d, s = self.dispatch, self.dispatch.scope
            with self._lock:
                detail: dict[str, JsonValue] = {
                    "source_turn_id": d.source_turn_id,
                    "phase_turn_id": s.phase_turn_id,
                    "source_position": d.source_position,
                    "call_position": d.call_position,
                    "intent_position": d.intent_position,
                    "invocation_id": d.invocation_id,
                    "worker_attempt_id": d.attempt_id,
                    "body_entered": self._entered,
                    "returned_awaitable": self._awaitable,
                    "waiter_abandoned": self._abandoned,
                    "outcome": outcome,
                }
                ids = self._ids
            self.delivery.submit(
                AuditRecord(
                    context=AuditContext(
                        actor=s.owner,
                        principal=s.principal,
                        session_id=s.session_id,
                        spec_id=s.spec_id,
                        spec_version=s.spec_version,
                        event_id=digest(
                            [
                                "tether.worker.v1",
                                s.owner,
                                s.session_id,
                                d.source_position,
                                d.attempt_id,
                                phase,
                            ]
                        ),
                        trace_id=ids.trace_id if ids is not None else None,
                        span_id=ids.span_id if ids is not None else None,
                    ),
                    kind="x.executor." + phase,
                    detail=detail,
                )
            )
        except BaseException:
            self.tracer.failed()

    @contextmanager
    def waiting(self) -> Generator[None, None, None]:
        with self.tracer.start_as_current_span(
            "tether.tool_call", attributes=self._attributes()
        ) as span:
            try:
                yield
            except BaseException as error:
                span.error("cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
                raise

    @contextmanager
    def body(self, synchronous: bool) -> Generator[None, None, None]:
        name = "tether.executor_function" if synchronous else "tether.async_body"
        with self.tracer.start_as_current_span(name, attributes=self._attributes()) as span:
            with self._lock:
                self._entered = True
                self._ids = span.ids
            self.emit("entered")
            outcome: Outcome = "returned"
            try:
                yield
            except BaseException as error:
                outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "raised"
                span.error("cancelled" if outcome == "cancelled" else "failed")
                raise
            finally:
                self.emit("exited", outcome)

    def returned(self, awaitable: bool) -> None:
        with self._lock:
            self._awaitable = awaitable

    def abandoned(self, outstanding: bool) -> None:
        with self._lock:
            self._abandoned = True
        if outstanding:
            self.emit("outstanding")
