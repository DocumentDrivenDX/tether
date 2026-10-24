"""Prepare incomplete groups before one accepted close transaction."""

from tether.types.execution import EffectsUnresolved, ExecutionDenied, NewEntry

from .effects import unresolved_effects
from .governance import Governance
from .starts import interrupted_calls


async def close_session(gov: Governance) -> None:
    decision = await gov.decision("read_session", gov.log.session.id)
    if not decision.facts["allowed"]:
        await gov.commit([decision])
        raise ExecutionDenied(str(decision.facts["reason"]))
    if unresolved_effects(gov.log.entries):
        raise EffectsUnresolved("effects_unresolved")
    closures = await interrupted_calls(gov)
    decision = await gov.decision("read_session", gov.log.session.id)
    if not decision.facts["allowed"]:
        await gov.commit([decision])
        raise ExecutionDenied(str(decision.facts["reason"]))
    await gov.commit(
        [
            decision,
            *closures,
            NewEntry(kind="session", facts={"event": "closed"}),
        ]
    )
