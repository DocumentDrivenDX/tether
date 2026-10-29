"""Immutable request envelopes for caller-origin operations; no model provenance."""

from typing import Literal

from pydantic import Field

from tether.types import JsonValue, Payload
from tether.types.canonical import digest

COMMAND_CONTRACT_VERSION = 1


class InvokeTool(Payload):
    origin: Literal["client"] = "client"
    request_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    spec: str | None = None

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


class AuxiliaryCall(Payload):
    origin: Literal["auxiliary"] = "auxiliary"
    request_id: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    through_index: int = Field(ge=0, strict=True)
    spec: str | None = None

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


class ReconcileSettlement(Payload):
    origin: Literal["reconcile_settlement"] = "reconcile_settlement"
    request_id: str = Field(min_length=1)
    settlement_id: str = Field(min_length=1)
    spec: str | None = None

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


class SpecTransition(Payload):
    origin: Literal["spec_transition"] = "spec_transition"
    request_id: str = Field(min_length=1)
    spec: str = Field(min_length=1)
    expected_spec_digest: str | None = None

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))
