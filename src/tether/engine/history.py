"""Preserve separate exchange authority while rebinding the paired message provider."""

from tether.types.execution import MessageHistory
from tether.types.history import HistoryProvider
from tether.types.transactions import TurnMessages


def selected_provider(
    configured: MessageHistory, provider: HistoryProvider | None, bound: MessageHistory
) -> HistoryProvider | None:
    if (
        isinstance(configured, TurnMessages)
        and isinstance(bound, TurnMessages)
        and configured.provider is provider
    ):
        return bound.provider
    return provider
