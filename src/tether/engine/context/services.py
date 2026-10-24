"""The existing governed ports used by auxiliary calls; no independent Agent runner."""

from collections.abc import Callable
from dataclasses import dataclass

from tether.ports import ModelPort
from tether.types import AttemptRecorder, ModelExecutionLimits, RequestTags
from tether.types.history import HistoryProvider


@dataclass(frozen=True)
class ContextServices:
    model: ModelPort
    limits: ModelExecutionLimits
    history: HistoryProvider | None
    attempt_factory: Callable[[RequestTags], AttemptRecorder] | None
