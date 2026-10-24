"""Generation fencing is checked inside the file writer before any journal bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from tether.types.execution import Clock
from tether.types.storage import LeaseLost, TurnInProgress


@dataclass(frozen=True)
class Lease:
    generation: int
    expires_at: datetime
    turn_id: str


class Leases:
    def __init__(self, clock: Clock, seconds: float) -> None:
        self.clock, self.seconds = clock, seconds
        self.active: dict[str, Lease] = {}
        self.generations: dict[str, int] = {}

    def next(self, session_id: str, turn_id: str) -> Lease:
        if session_id in self.active:
            raise TurnInProgress("turn_in_progress")
        return Lease(
            self.generations.get(session_id, 0) + 1,
            self.clock() + timedelta(seconds=self.seconds),
            turn_id,
        )

    def check(self, session_id: str, generation: int) -> None:
        lease = self.active.get(session_id)
        if lease is None or lease.generation != generation or self.clock() >= lease.expires_at:
            raise LeaseLost("lease_lost")

    def publish(self, session_id: str, lease: Lease | None, generation: int) -> None:
        self.generations[session_id] = generation
        if lease is None:
            self.active.pop(session_id, None)
        else:
            self.active[session_id] = lease
