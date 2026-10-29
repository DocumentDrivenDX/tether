"""Message view over the full content port; execution facts grant reachability elsewhere."""

from tether.types import Message
from tether.types.execution import Actor, ExecutionDenied, HistoryRef, MessageHistory, TurnLog
from tether.types.history import HistoryProvider, HistoryRecord
from tether.types.transactions import TurnHistoryProvider


class MessageHistoryAdapter:
    def __init__(self, provider: HistoryProvider) -> None:
        self.provider = provider

    def for_turn(self, log: TurnLog) -> MessageHistory:
        provider = self.provider
        if isinstance(provider, TurnHistoryProvider):
            return MessageHistoryAdapter(provider.for_turn(log))
        return self

    async def put(self, *, actor: Actor, session_id: str, key: str, message: Message) -> HistoryRef:
        return await self.provider.put(
            actor=actor,
            session_id=session_id,
            key=key,
            record=HistoryRecord(kind="message", message=message),
        )

    async def read(self, ref: HistoryRef, *, actor: Actor, session_id: str) -> Message:
        if ref.owner != actor.id or ref.session_id != session_id:
            raise ExecutionDenied("history_denied")
        values = await self.provider.read([ref], actor=actor)
        if len(values) == 1 and values[0].ref == ref and values[0].status == "pending":
            raise ExecutionDenied("history_pending")
        if len(values) != 1 or values[0].ref != ref or values[0].status != "ready":
            raise ExecutionDenied("history_unavailable")
        record = values[0].record
        if record is None or record.kind != "message" or record.message is None:
            raise ExecutionDenied("history_integrity")
        return record.message.model_copy(deep=True)
