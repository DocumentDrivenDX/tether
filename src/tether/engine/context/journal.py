"""Session-local preparation identity comes only from accepted immutable journal facts."""

from tether.types.context import ContextError, ContextPreparation, ContextUse
from tether.types.execution import Entry


def next_ordinal(entries: tuple[Entry, ...], model_call_id: str) -> int:
    preparations: dict[int, tuple[Entry, ContextPreparation]] = {}
    used: set[int] = set()
    calls: set[str] = set()
    try:
        for position, entry in enumerate(entries):
            if entry.index != position:
                raise ValueError("context_journal")
            if entry.kind == "context_preparation":
                value = ContextPreparation.model_validate(entry.facts)
                if (
                    value.model_call_id in calls
                    or value.ordinal != len(preparations)
                    or entry.key != f"context:prepare:{value.ordinal}"
                    or entry.index <= value.through_index
                ):
                    raise ValueError("context_preparation")
                preparations[value.ordinal] = entry, value
                calls.add(value.model_call_id)
            elif entry.kind == "context_use":
                use = ContextUse.model_validate(entry.facts)
                prior = preparations.get(use.preparation_ordinal)
                if prior is None:
                    raise ValueError("context_use")
                source, preparation = prior
                if (
                    use.preparation_ordinal in used
                    or entry.key != f"context:use:{use.preparation_ordinal}"
                    or entry.index <= source.index
                    or entry.turn_id != source.turn_id
                    or use.model_call_id != preparation.model_call_id
                    or (use.through_index, use.source_view_digest, use.config_digest)
                    != (
                        preparation.through_index,
                        preparation.source_view_digest,
                        preparation.config_digest,
                    )
                ):
                    raise ValueError("context_use")
                used.add(use.preparation_ordinal)
    except (ValueError, TypeError, AttributeError):
        raise ContextError("context_incompatible") from None
    if model_call_id in calls:
        raise ContextError("context_incompatible")
    return len(preparations)
