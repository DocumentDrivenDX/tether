"""Trusted tool-owned semantic transforms; JSON subset comparisons confer no authority."""

from typing import Protocol

from tether.types import JsonValue, Payload
from tether.types.execution import Actor

APPROVAL_EDIT_CONTRACT_VERSION = 1


class ApprovalEditRequest(Payload):
    actor: Actor
    session_id: str
    invocation_id: str
    original: dict[str, JsonValue]
    shown: dict[str, JsonValue]


class ArgumentTransform(Protocol):
    id: str
    version: str

    async def transform(self, request: ApprovalEditRequest) -> dict[str, JsonValue]: ...
