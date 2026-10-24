"""Asynchronous provider-neutral external-system protocols."""

from collections.abc import AsyncIterator
from typing import Protocol

from tether.types import AttemptRecorder as AttemptRecorder
from tether.types import Credential as Credential
from tether.types import ModelRequest, StreamItem

# Execution ports are Tether-owned; callback value types avoid dependency cycles.
from tether.types.execution import MessageHistory as MessageHistory
from tether.types.execution import Policy as Policy
from tether.types.execution import Provider as Provider
from tether.types.execution import ResultGuard as ResultGuard
from tether.types.execution import Tool as Tool
from tether.types.execution import TurnLog as TurnLog


class ModelPort(Protocol):
    provider: str

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]: ...
