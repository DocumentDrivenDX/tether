"""One atomic edited-value/approval decision batch, prepared before continuation."""

from tether.types.execution import AgentSpec, Answer, ExecutionDenied, NewEntry, PendingConfirmation

from .confirmations import approval_entries
from .edits import prepare
from .governance import Governance
from .registry import Registry
from .workers import Workers


async def confirmed_entries(
    gov: Governance,
    workers: Workers,
    pending: PendingConfirmation,
    answer: Answer,
    state: str,
    registry: Registry,
    spec: AgentSpec,
    *,
    resuming: bool,
) -> list[NewEntry]:
    edited: list[NewEntry] = []
    if answer.arguments is not None:
        if state != "approved" or pending.tool not in registry.tools:
            raise ExecutionDenied("approval_edit_invalid")
        pending, edited = await prepare(
            gov, workers, pending, answer.arguments, registry.tools[pending.tool].tool, spec
        )
    return [*edited, *await approval_entries(gov, pending, state, answer.note, resuming=resuming)]
