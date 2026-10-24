"""Fixed metadata for mediated retrieval and registered provider hook waits."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import cast
from uuid import uuid4

from tether.types import JsonValue
from tether.types.canonical import digest
from tether.types.execution import Provider
from tether.types.telemetry import AuditContext, AuditRecord, TraceIds, TraceScalar

from .delivery import AuditDelivery
from .metadata import Scope
from .tracing import GuardedTracer

METHODS = frozenset(
    {
        "capabilities",
        "prompt_section",
        "hidden_tools",
        "before_request",
        "before_call",
        "after_call",
    }
)
NAMESPACE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class StepObservers:
    def __init__(
        self,
        delivery: AuditDelivery,
        tracer: GuardedTracer,
        audit: bool,
        enabled: bool,
        failed: Callable[[], None],
    ) -> None:
        self.delivery, self.tracer = delivery, tracer
        self.audit, self.enabled, self.failed = audit, enabled, failed
        self._providers: dict[int, str] = {}

    def register(self, providers: Sequence[Provider]) -> None:
        if not self.enabled:
            return
        for provider in providers:
            try:
                name = provider.id
                if type(name) is not str or NAMESPACE.fullmatch(name) is None:
                    self.failed()
                    continue
                self._providers[id(provider)] = name
            except BaseException:
                self.failed()

    def retrieval(
        self,
        scope: Scope,
        *,
        invocation_id: str,
        source_position: int,
        source_turn_id: str | None,
        authorization_position: int,
        resource: str,
    ) -> AbstractContextManager[None]:
        if not self.enabled:
            return nullcontext()
        try:
            attempt = str(uuid4())
            detail: dict[str, JsonValue] = {
                "origin": "waiter",
                "retrieval_attempt_id": attempt,
                "invocation_id": invocation_id,
                "source_position": source_position,
                "source_turn_id": source_turn_id,
                "phase_turn_id": scope.phase_turn_id,
                "authorization_position": authorization_position,
                "resource_digest": digest(resource),
            }
            return self._observe(scope, "tether.retrieval", "retrieval", attempt, detail)
        except BaseException:
            self.failed()
            return nullcontext()

    def provider(
        self,
        scope: Scope,
        provider: Provider,
        method: str,
    ) -> AbstractContextManager[None]:
        if not self.enabled:
            return nullcontext()
        try:
            name = self._providers.get(id(provider))
            current = provider.id
            if name is None or type(current) is not str or current != name or method not in METHODS:
                self.failed()
                return nullcontext()
            attempt = str(uuid4())
            detail: dict[str, JsonValue] = {
                "origin": "waiter",
                "hook_attempt_id": attempt,
                "provider_id": name,
                "method": method,
                "phase_turn_id": scope.phase_turn_id,
            }
            return self._observe(
                scope, "tether.provider_hook", "x." + name + ".hook", attempt, detail
            )
        except BaseException:
            self.failed()
            return nullcontext()

    @contextmanager
    def _observe(
        self,
        scope: Scope,
        span_name: str,
        kind: str,
        attempt: str,
        detail: dict[str, JsonValue],
    ) -> Generator[None, None, None]:
        attributes: dict[str, TraceScalar] = {
            "tether.owner": scope.owner,
            "tether.principal": scope.principal,
            "tether.session_id": scope.session_id,
            "tether.phase_turn_id": scope.phase_turn_id,
        }
        for field, value in (("spec_id", scope.spec_id), ("spec_version", scope.spec_version)):
            if value is not None:
                attributes["tether." + field] = value
        for field, value in detail.items():
            if type(value) in {str, int}:
                attributes["tether." + field] = cast(TraceScalar, value)
        with self.tracer.start_as_current_span(span_name, attributes=attributes) as span:
            outcome = "returned"
            try:
                yield
            except BaseException as error:
                outcome = "cancelled" if isinstance(error, asyncio.CancelledError) else "raised"
                span.error("cancelled" if outcome == "cancelled" else "failed")
                raise
            finally:
                if self.audit:
                    self._emit(scope, kind, attempt, detail | {"outcome": outcome}, span.ids)

    def _emit(
        self,
        scope: Scope,
        kind: str,
        attempt: str,
        detail: dict[str, JsonValue],
        ids: TraceIds | None,
    ) -> None:
        try:
            self.delivery.submit(
                AuditRecord(
                    context=AuditContext(
                        actor=scope.owner,
                        principal=scope.principal,
                        session_id=scope.session_id,
                        spec_id=scope.spec_id,
                        spec_version=scope.spec_version,
                        event_id=digest(
                            ["tether.step.v1", scope.owner, scope.session_id, kind, attempt]
                        ),
                        trace_id=ids.trace_id if ids is not None else None,
                        span_id=ids.span_id if ids is not None else None,
                    ),
                    kind=kind,
                    detail=detail,
                )
            )
        except BaseException:
            self.failed()
