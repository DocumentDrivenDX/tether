"""Internal preparation state consumed by the summary modules without import cycles."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from tether.types import ModelRequest
from tether.types.context import ContextConfig, ContextPreparation, ModelLimits, TokenEstimate

from ..governance import Governance
from ..tools import ToolExecutor
from .services import ContextServices
from .sources import Snapshot


class ContextState(Protocol):
    gov: Governance
    executor: ToolExecutor
    services: ContextServices
    original: Snapshot | None
    config: ContextConfig
    profile: ModelLimits
    preparation: ContextPreparation
    ordinal: int | None
    strategy_id: str
    strategy_version: str

    def guarded(self) -> AbstractAsyncContextManager[None]: ...
    def summary_profile(self) -> ModelLimits | None: ...
    def scope_digest(self) -> str: ...
    def binding_check(self) -> None: ...
    async def verify_sources(self) -> None: ...
    async def estimate(self, request: ModelRequest) -> TokenEstimate: ...
