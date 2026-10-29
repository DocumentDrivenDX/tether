"""Post-admission preparation cannot replace an accepted request."""

from typing import Literal, Protocol

from pydantic import Field

from tether.types import AttachmentRef, Payload
from tether.types.execution import Actor, HistoryRef

PREPARATION_CONTRACT_VERSION = 1


class PreparationRequest(Payload):
    actor: Actor
    session_id: str
    turn_id: str
    request_id: str | None
    fingerprint: str
    source_index: int
    source: HistoryRef
    attachments: tuple[AttachmentRef, ...]


class PreparationResult(Payload):
    state: Literal["ready", "needs_input", "refused"]
    reason: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,95}$")
    references: tuple[HistoryRef, ...] = ()


class Preparation(Protocol):
    async def prepare(self, request: PreparationRequest) -> PreparationResult: ...
