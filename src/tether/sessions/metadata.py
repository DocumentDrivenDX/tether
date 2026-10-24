"""Bounded owner-authorized metadata commands; the store serializes mutations."""

from tether.types.canonical import canonical
from tether.types.client import ReadLimit
from tether.types.execution import Actor, ExecutionDenied, copy_actor
from tether.types.mutations import CreateReceipt, UpdateReceipt, receipt_store
from tether.types.storage import SessionInit, SessionPatch, SessionSummary, UnknownSession

from .access import SessionAccess
from .receipts import validated


class MetadataCommands:
    def __init__(self, access: SessionAccess):
        self.access = access

    def _bound(self, value: SessionInit | SessionPatch) -> None:
        self.access.check()
        if value.settings is not None and "held" in value.settings:
            raise ExecutionDenied("managed_setting")
        if (
            len(canonical(value.model_dump(mode="json", exclude_unset=True)))
            > self.access.limits.max_request_bytes
        ):
            raise ReadLimit("request_bytes")

    async def create(self, actor: Actor, init: SessionInit) -> SessionSummary:
        actor, init = copy_actor(actor), init.model_copy(deep=True)
        self._bound(init)

        with self.access.observers.client.command(actor, "create"):
            companion_used = False

            async def operation() -> SessionSummary:
                nonlocal companion_used
                store = self.access.store_for(actor)
                companion = receipt_store(store) if self.access.observers.enabled else None
                companion_used = companion is not None
                if companion is not None:
                    raw = await companion.create_with_receipt(actor.id, init)
                    receipt = validated(
                        raw,
                        CreateReceipt,
                        actor.id,
                        self.access.limits.max_mutation_receipt_bytes,
                        ref=init.ref,
                    )
                    self.access.observers.client.mutation(actor, receipt)
                    summary = receipt.summary
                else:
                    summary = await store.create(actor.id, init)
                await self.access.authorize(
                    actor,
                    summary.id,
                    session_id=summary.id,
                )
                return summary

            summary = await self.access.metadata(actor, "owner:" + actor.id, operation)
            if not companion_used:
                self.access.observers.client.returned(actor, summary, "create")
            return summary

    async def update(self, actor: Actor, session_id: str, patch: SessionPatch) -> SessionSummary:
        actor, patch = copy_actor(actor), patch.model_copy(deep=True)
        self._bound(patch)

        with self.access.observers.client.command(actor, "update"):
            companion_used = False

            async def operation() -> SessionSummary:
                nonlocal companion_used
                store = self.access.store_for(actor)
                companion = receipt_store(store) if self.access.observers.enabled else None
                companion_used = companion is not None
                if await store.get(session_id, actor.id) is None:
                    raise UnknownSession("unknown_session")
                await self.access.authorize(
                    actor,
                    session_id,
                    session_id=session_id,
                )
                if companion is not None:
                    raw = await companion.update_with_receipt(session_id, actor.id, patch)
                    receipt = validated(
                        raw,
                        UpdateReceipt,
                        actor.id,
                        self.access.limits.max_mutation_receipt_bytes,
                        session_id=session_id,
                    )
                    self.access.observers.client.mutation(actor, receipt)
                    return receipt.summary
                return await store.update(session_id, actor.id, patch)

            summary = await self.access.metadata(
                actor, session_id, operation, session_id=session_id
            )
            if not companion_used:
                self.access.observers.client.returned(actor, summary, "metadata")
            return summary
