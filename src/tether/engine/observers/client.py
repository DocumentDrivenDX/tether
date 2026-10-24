"""Client metadata observations have no journal, disclosure or mutation authority."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from tether.types.canonical import digest
from tether.types.execution import Actor
from tether.types.mutations import CreateReceipt, UpdateReceipt
from tether.types.storage import SessionSummary
from tether.types.telemetry import AuditContext, AuditRecord, TraceIds

from .delivery import AuditDelivery
from .metadata import ACTIONS, REASONS, code
from .tracing import GuardedTracer


@dataclass(frozen=True)
class Gate:
    context: AuditContext | None
    request_id: str | None
    action: str
    resource_digest: str | None
    delivery: AuditDelivery
    failed: Callable[[], None]
    tracer: GuardedTracer

    def completed(self, allowed: bool, reason: str) -> None:
        try:
            if type(allowed) is not bool:
                self.failed()
                return
            self.tracer.set_current_attribute("tether.allowed", allowed)
            self.tracer.set_current_attribute("tether.reason", code(reason, REASONS))
            if self.context is None:
                return
            self.delivery.submit(
                AuditRecord(
                    context=self.context,
                    kind="authorization",
                    detail={
                        "origin": "client",
                        "gate_request_id": self.request_id,
                        "action": self.action,
                        "allowed": allowed,
                        "reason": code(reason, REASONS),
                        "resource_digest": self.resource_digest,
                    },
                )
            )
        except BaseException:
            self.failed()


def context(
    actor: Actor,
    event_id: str,
    ids: TraceIds | None,
    session_id: str | None = None,
    spec_id: str | None = None,
) -> AuditContext:
    return AuditContext(
        actor=actor.id,
        principal=actor.principal or actor.id,
        event_id=event_id,
        session_id=session_id,
        spec_id=spec_id,
        trace_id=ids.trace_id if ids is not None else None,
        span_id=ids.span_id if ids is not None else None,
    )


class ClientObservers:
    def __init__(
        self,
        delivery: AuditDelivery,
        tracer: GuardedTracer,
        enabled: bool,
        failed: Callable[[], None],
    ) -> None:
        self.delivery, self.tracer, self.enabled, self.failed = delivery, tracer, enabled, failed

    @contextmanager
    def command(
        self, actor: Actor, operation: Literal["create", "update"]
    ) -> Generator[None, None, None]:
        with self.tracer.start_as_current_span(
            "tether.client." + operation,
            attributes={
                "tether.owner": actor.id,
                "tether.principal": actor.principal or actor.id,
            },
        ) as span:
            try:
                yield
            except BaseException as error:
                span.error("cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
                raise

    @contextmanager
    def gate(
        self, actor: Actor, resource: str, action: str, session_id: str | None
    ) -> Generator[Gate, None, None]:
        with self.tracer.start_as_current_span(
            "tether.gate",
            attributes={
                "tether.origin": "client",
                "tether.action": code(action, ACTIONS),
                "tether.owner": actor.id,
                "tether.principal": actor.principal or actor.id,
            },
        ) as span:
            gate = Gate(
                None, None, code(action, ACTIONS), None, self.delivery, self.failed, self.tracer
            )
            if self.enabled:
                try:
                    request_id = str(uuid4())
                    gate = Gate(
                        context(
                            actor,
                            digest(["tether.client.gate.v1", request_id]),
                            span.ids,
                            session_id,
                        ),
                        request_id,
                        code(action, ACTIONS),
                        digest(resource),
                        self.delivery,
                        self.failed,
                        self.tracer,
                    )
                except BaseException:
                    self.failed()
            try:
                yield gate
            except BaseException as error:
                span.error("cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
                raise

    def mutation(self, actor: Actor, receipt: CreateReceipt | UpdateReceipt) -> None:
        if not self.enabled:
            return
        try:
            commit = receipt.commit
            if commit is None:
                return
            summary = receipt.summary
            self.tracer.set_current_attribute("tether.session_id", summary.id)
            ids = self.tracer.current_ids()
            if isinstance(receipt, CreateReceipt):
                self.delivery.submit(
                    AuditRecord(
                        context=context(
                            actor,
                            digest(["tether.audit.v1", "genesis", actor.id, summary.id]),
                            ids,
                            summary.id,
                            summary.spec_id,
                        ),
                        kind="session_started",
                        detail={"origin": "client", "commit_seq": commit.seq},
                    )
                )
            else:
                for entry in commit.entries:
                    category = (
                        "settings"
                        if entry.kind == "settings"
                        else ("title" if entry.facts.get("event") == "titled" else "tags")
                    )
                    self.delivery.submit(
                        AuditRecord(
                            context=context(
                                actor,
                                digest(
                                    [
                                        "tether.audit.v1",
                                        actor.id,
                                        summary.id,
                                        entry.index,
                                        "session_settings",
                                    ]
                                ),
                                ids,
                                summary.id,
                                summary.spec_id,
                            ),
                            kind="session_settings",
                            detail={
                                "origin": "client",
                                "commit_seq": commit.seq,
                                "entry_position": entry.index,
                                "source_turn_id": entry.turn_id,
                                "category": category,
                            },
                        )
                    )
        except BaseException:
            self.failed()

    def returned(
        self, actor: Actor, summary: SessionSummary, operation: Literal["create", "metadata"]
    ) -> None:
        if not self.enabled:
            return
        try:
            self.delivery.submit(
                AuditRecord(
                    context=context(
                        actor,
                        digest(["tether.client.return.v1", str(uuid4())]),
                        self.tracer.current_ids(),
                        summary.id,
                        summary.spec_id,
                    ),
                    kind="x.client." + operation + "_returned",
                    detail={"origin": "client", "semantic_receipts": False},
                )
            )
        except BaseException:
            self.failed()
