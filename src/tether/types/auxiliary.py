"""Application-selected tool-free purposes; scheduling remains application owned."""

from pydantic import Field

from tether.types import ModelExecutionLimits, Payload

AUXILIARY_CONTRACT_VERSION = 1


class AuxiliaryProfile(Payload):
    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    model: str = Field(min_length=1)
    instructions: str
    limits: ModelExecutionLimits = Field(default_factory=ModelExecutionLimits)
