"""Fabricated count-only semantic restriction, not a generic JSON safety algorithm."""

from tether.testing.conformance.types import require
from tether.types import JsonValue
from tether.types.approval_edits import ApprovalEditRequest, ArgumentTransform
from tether.types.execution import ExecutionDenied


class NarrowCount:
    id = "fabricated_count"
    version = "1"

    async def transform(self, request: ApprovalEditRequest) -> dict[str, JsonValue]:
        original, shown = request.original.get("count"), request.shown.get("count")
        if not isinstance(original, (int, str)) or type(shown) is not int:
            raise ExecutionDenied("fabricated_count_invalid")
        if not 0 <= shown <= int(original) or set(request.shown) != {"count"}:
            raise ExecutionDenied("fabricated_count_expanded")
        return dict(request.shown)


async def argument_transform_suite(
    participant: ArgumentTransform, request: ApprovalEditRequest
) -> None:
    before = request.model_dump(mode="json")
    first = await participant.transform(request)
    require(request.model_dump(mode="json") == before, "edit_input_immutable")
    require(await participant.transform(request) == first, "edit_repeatable")
