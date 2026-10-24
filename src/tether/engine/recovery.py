"""Pure journal recovery planning; no history hydration or tool/model dispatch."""

from collections.abc import Mapping, Sequence

from tether.types.canonical import digest
from tether.types.execution import (
    CallManifest,
    Entry,
    ExecutionDenied,
    NotInterrupted,
    ResumePlan,
    ResumeStep,
    ResumeTool,
)


def interrupted_turn(entries: Sequence[Entry]) -> str:
    lifecycle = next(
        (e for e in reversed(entries) if e.kind in {"turn_started", "turn_resumed", "turn_ended"}),
        None,
    )
    if (
        lifecycle is None
        or lifecycle.kind != "turn_ended"
        or lifecycle.facts.get("stop_reason") != "interrupted"
        or lifecycle.turn_id is None
    ):
        raise NotInterrupted("not_interrupted")
    return lifecycle.turn_id


def _parent_interrupted(entries: Sequence[Entry], source: Entry, parent: str) -> bool:
    previous = next(
        (
            e
            for e in reversed(entries)
            if e.index < source.index and e.kind in {"turn_started", "turn_resumed", "turn_ended"}
        ),
        None,
    )
    return previous is not None and (
        previous.kind,
        previous.turn_id,
        previous.facts.get("stop_reason"),
    ) == ("turn_ended", parent, "interrupted")


def resume_chain(entries: Sequence[Entry], turn_id: str) -> tuple[str, ...]:
    starts: dict[str, Entry] = {}
    for entry in entries:
        if entry.kind == "turn_started" and entry.turn_id is not None:
            if entry.turn_id in starts:
                raise ExecutionDenied("resume_chain_invalid")
            starts[entry.turn_id] = entry
    chain: list[str] = []
    current = turn_id
    while current not in chain:
        source = starts.get(current)
        if source is None:
            raise ExecutionDenied("resume_chain_invalid")
        chain.append(current)
        prior = source.facts.get("resumes")
        if prior is None:
            return tuple(chain)
        if (
            not isinstance(prior, str)
            or not prior
            or prior not in starts
            or starts[prior].index >= source.index
            or not _parent_interrupted(entries, source, prior)
        ):
            raise ExecutionDenied("resume_chain_invalid")
        current = prior
    raise ExecutionDenied("resume_chain_invalid")


def _safe(manifest: CallManifest, tool: ResumeTool | None) -> bool:
    if tool is None or manifest.replay != "safe" or tool.replay != "safe":
        return False
    return tool.tool_digest == manifest.tool_digest


def _finished(keyed: Mapping[str, Entry], invocation_id: str) -> bool:
    completed = False
    for prefix, kind in (("result:", "tool_call"), ("resolution:", "resolved")):
        entry = keyed.get(prefix + invocation_id)
        if entry is None:
            continue
        if entry.kind != kind or entry.facts.get("invocation_id") != invocation_id:
            raise ExecutionDenied("resume_invocation_invalid")
        if kind == "resolved" and entry.facts.get("outcome") not in ("completed", "not_completed"):
            raise ExecutionDenied("resume_invocation_invalid")
        if kind == "tool_call" and entry.facts.get("status") not in (
            "ok",
            "error",
            "withheld",
            "denied",
            "invalid_arguments",
            "interrupted",
        ):
            raise ExecutionDenied("resume_invocation_invalid")
        completed = True
    return completed


def _step(
    source: Entry,
    manifest: CallManifest,
    session_id: str,
    keyed: Mapping[str, Entry],
    tools: Mapping[str, ResumeTool],
) -> ResumeStep | None:
    invocation_id = digest([session_id, source.index, manifest.position])
    if _finished(keyed, invocation_id):
        return None
    intent = keyed.get("intent:" + invocation_id)
    if intent is not None and (
        intent.kind,
        intent.facts.get("status"),
        intent.facts.get("invocation_id"),
        intent.facts.get("call_id"),
        intent.facts.get("tool"),
        intent.facts.get("tool_digest"),
        intent.facts.get("effect"),
        intent.facts.get("replay"),
    ) != (
        "tool_call",
        "started",
        invocation_id,
        manifest.call_id,
        manifest.tool,
        manifest.tool_digest,
        manifest.effect,
        manifest.replay,
    ):
        raise ExecutionDenied("resume_invocation_invalid")
    kind = (
        "run"
        if intent is None
        else "rerun"
        if _safe(manifest, tools.get(manifest.tool))
        else "record_interrupted"
    )
    return ResumeStep(
        kind=kind,
        call_entry_index=source.index,
        call_position=manifest.position,
        invocation_id=invocation_id,
    )


def _needs_model(entries: Sequence[Entry], chain: frozenset[str], session_id: str) -> bool:
    latest = next(
        (
            e
            for e in reversed(entries)
            if e.turn_id in chain
            and e.kind == "model_call"
            and e.facts.get("purpose", "answer") == "answer"
        ),
        None,
    )
    if latest is None or (latest.facts.get("status"), latest.facts.get("finish_reason")) != (
        "ok",
        "stop",
    ):
        return True
    call_id = latest.facts.get("model_call_id")
    source = next((e for e in entries if e.key == "source:" + str(call_id)), None)
    if (
        not isinstance(call_id, str)
        or source is None
        or source.history_ref is None
        or source.kind != "message"
        or source.call_manifest
        or source.turn_id != latest.turn_id
        or source.index >= latest.index
        or source.facts.get("role") != "assistant"
        or source.history_ref.session_id != session_id
    ):
        raise ExecutionDenied("resume_final_source_missing")
    return False


def _scope(entries: Sequence[Entry], chain: frozenset[str], session_id: str) -> None:
    for source in entries:
        if (
            source.turn_id in chain
            and source.call_manifest
            and (
                source.kind != "message"
                or source.facts.get("role") != "assistant"
                or source.history_ref is None
                or source.history_ref.session_id != session_id
            )
        ):
            raise ExecutionDenied("resume_invocation_scope")
    ids = {
        digest([session_id, source.index, m.position])
        for source in entries
        if source.turn_id in chain
        for m in source.call_manifest
    }
    for entry in entries:
        if entry.turn_id not in chain or entry.kind not in {"tool_call", "resolved"}:
            continue
        invocation_id = entry.facts.get("invocation_id")
        if not isinstance(invocation_id, str) or invocation_id not in ids:
            raise ExecutionDenied("resume_invocation_scope")


def plan_resume(
    entries: Sequence[Entry],
    tools: Mapping[str, ResumeTool],
    *,
    session_id: str,
) -> ResumePlan:
    if not session_id:
        raise ExecutionDenied("resume_session_missing")
    if any(e.index != position for position, e in enumerate(entries)):
        raise ExecutionDenied("resume_entry_order")
    turn_id = interrupted_turn(entries)
    chain = frozenset(resume_chain(entries, turn_id))
    _scope(entries, chain, session_id)
    keyed = {e.key: e for e in entries if e.key is not None}
    steps: list[ResumeStep] = []
    for source in entries:
        if source.turn_id not in chain:
            continue
        positions = [m.position for m in source.call_manifest]
        if len(positions) != len(set(positions)) or any(p < 0 for p in positions):
            raise ExecutionDenied("resume_manifest_invalid")
        for manifest in source.call_manifest:
            step = _step(source, manifest, session_id, keyed, tools)
            if step is not None:
                steps.append(step)
    if steps or _needs_model(entries, chain, session_id):
        steps.append(ResumeStep(kind="model_step"))
    return ResumePlan(interrupted_turn_id=turn_id, steps=tuple(steps))
