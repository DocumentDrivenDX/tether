"""Finite preparation dummy and public companion conformance checks."""

from tether.testing.conformance.types import require
from tether.types.preparation import Preparation, PreparationRequest, PreparationResult


class ReadyPreparation:
    def __init__(self, *, capacity: int = 32) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("preparation_capacity")
        self.capacity = capacity
        self.requests: list[PreparationRequest] = []

    async def prepare(self, request: PreparationRequest) -> PreparationResult:
        if len(self.requests) >= self.capacity:
            raise ValueError("preparation_capacity")
        self.requests.append(PreparationRequest.model_validate(request.model_dump(mode="python")))
        return PreparationResult(state="ready", reason="ready")


async def preparation_suite(participant: Preparation, request: PreparationRequest) -> None:
    before = request.model_dump(mode="json")
    result = await participant.prepare(request)
    require(request.model_dump(mode="json") == before, "preparation_input_unchanged")
    require(
        PreparationResult.model_validate(result.model_dump(mode="json")) == result,
        "preparation_result_valid",
    )
