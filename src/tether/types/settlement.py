"""Durable settlement identity and independent-backend reconciliation ports."""

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from pydantic import Field

from tether.types import Payload
from tether.types.execution import Actor, Entry, NewEntry

SETTLEMENT_CONTRACT_VERSION = 1
SettlementDecision = Literal["commit", "rollback"]
SettlementOutcome = Literal["committed", "rolled_back", "uncertain"]


class SettlementBinding(Payload):
    settlement_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    participant: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    owner: str
    session_id: str
    actor_id: str
    principal: str
    spec_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: SettlementDecision


class SettlementParticipant(Protocol):
    id: str
    profile: Literal["independent", "shared"]

    async def settle(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome: ...
    async def reconcile(self, binding: SettlementBinding, actor: Actor) -> SettlementOutcome: ...


@runtime_checkable
class SettlementLog(Protocol):
    async def settle(
        self,
        entries: Sequence[NewEntry],
        binding: SettlementBinding,
        participant: SettlementParticipant,
    ) -> tuple[Entry, ...]: ...
