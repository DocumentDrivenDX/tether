"""Finite volatile settlement double; process durability requires an application adapter."""

from typing import Literal

from tether.testing.conformance.types import require
from tether.types.execution import Actor, ExecutionDenied
from tether.types.settlement import SettlementBinding, SettlementOutcome, SettlementParticipant


class MemorySettlement:
    profile: Literal["independent", "shared"] = "independent"

    def __init__(self, *, id: str = "fabricated", capacity: int = 32) -> None:
        if not id or type(capacity) is not int or capacity <= 0:
            raise ValueError("settlement_capacity")
        self.id, self.capacity = id, capacity
        self.bindings: dict[str, SettlementBinding] = {}
        self.calls = 0
        self.lose_acknowledgment = False

    async def settle(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome:
        if (binding.owner, binding.actor_id, binding.participant) != (actor.id, actor.id, self.id):
            raise ExecutionDenied("settlement_scope")
        previous = self.bindings.get(binding.settlement_id)
        if previous is not None and previous != binding:
            raise ExecutionDenied("settlement_conflict")
        if previous is None:
            if len(self.bindings) >= self.capacity:
                raise ExecutionDenied("settlement_capacity")
            self.bindings[binding.settlement_id] = binding.model_copy(deep=True)
            self.calls += 1
        if self.lose_acknowledgment:
            self.lose_acknowledgment = False
            return "uncertain"
        return "committed" if binding.decision == "commit" else "rolled_back"

    async def reconcile(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome:
        return await self.settle(binding, actor)


async def settlement_suite(
    participant: SettlementParticipant, binding: SettlementBinding, actor: Actor
) -> None:
    expected = "committed" if binding.decision == "commit" else "rolled_back"
    require(await participant.settle(binding, actor) == expected, "settlement_known")
    require(await participant.settle(binding, actor) == expected, "settlement_idempotent")
    require(await participant.reconcile(binding, actor) == expected, "settlement_reconciled")
