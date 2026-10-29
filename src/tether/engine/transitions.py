"""Explicit specification changes publish only after every prior call group settles."""

from collections.abc import Callable

from tether.types.canonical import digest
from tether.types.commands import SpecTransition
from tether.types.execution import AgentSpec, ExecutionDenied, NewEntry, TurnResult

from .effects import unresolved_effects
from .governance import Governance
from .registry import Registry


async def transition(
    gov: Governance,
    command: SpecTransition,
    spec: AgentSpec,
    registry: Registry,
    accepted: Callable[[], None] | None,
) -> TurnResult:
    keys = {entry.key for entry in gov.log.entries}
    if unresolved_effects(gov.log.entries) or any(
        "result:" + digest([gov.log.session.id, entry.index, manifest.position]) not in keys
        for entry in gov.log.entries
        for manifest in entry.call_manifest
    ):
        raise ExecutionDenied("spec_transition_unsettled")
    previous = next(
        (entry for entry in reversed(gov.log.entries) if entry.kind == "turn_started"), None
    )
    old = previous.facts.get("spec_digest") if previous is not None else None
    if command.expected_spec_digest is not None and command.expected_spec_digest != old:
        raise ExecutionDenied("spec_transition_conflict")
    await gov.authorize("transition_spec", spec.id)
    binding = {
        "spec_id": spec.id,
        "spec_version": spec.version,
        "spec_digest": digest(spec.model_dump(mode="json")),
        "registry_generation": registry.generation,
    }
    await gov.commit(
        [
            NewEntry(
                kind="turn_started",
                turn_id=gov.turn_id,
                key="request:" + command.request_id,
                facts={"origin": command.origin, "input_digest": command.fingerprint, **binding},
            ),
            NewEntry(
                kind="spec_transition",
                turn_id=gov.turn_id,
                key="transition:" + command.request_id,
                facts={"previous_spec_digest": old, **binding},
            ),
            NewEntry(kind="turn_ended", turn_id=gov.turn_id, facts={"stop_reason": "final"}),
        ]
    )
    if accepted is not None:
        accepted()
    return TurnResult(turn_id=gov.turn_id, stop_reason="final")
