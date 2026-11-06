"""Optional additive companions for turn-scoped authoritative history."""

from typing import Protocol, runtime_checkable

from tether.types.execution import MessageHistory, TurnLog
from tether.types.history import HistoryProvider

HISTORY_TRANSACTION_VERSION = 1


@runtime_checkable
class TurnHistoryProvider(Protocol):
    def for_turn(self, log: TurnLog) -> HistoryProvider: ...


@runtime_checkable
class TurnMessages(Protocol):
    provider: HistoryProvider

    def for_turn(self, log: TurnLog) -> MessageHistory: ...


@runtime_checkable
class HistoryTurn(Protocol):
    @property
    def history_root(self) -> object: ...

    def bind_history(self, root: object) -> HistoryProvider: ...
