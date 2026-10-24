"""Pure journal plans preserve invocation identity without payload hydration."""

from datetime import UTC, datetime

import pytest
from test_gated_execution import DemoTool

from tether.engine.recovery import plan_resume, resume_chain
from tether.types.canonical import digest
from tether.types.execution import (
    CallManifest,
    Entry,
    ExecutionDenied,
    HistoryRef,
    NotInterrupted,
    ResumeTool,
)
from tether.types.tooling import definition


def declaration(tool):
    return ResumeTool(tool_digest=digest(definition(tool)), replay=tool.replay)


def entry(kind, index, *, turn_id="original", **values):
    return Entry(
        kind=kind, index=index, at=datetime(2026, 1, 1, tzinfo=UTC), turn_id=turn_id, **values
    )


def journal(tool, state="not_started"):
    manifest = CallManifest(
        position=0,
        call_id="repeatable-sdk-id",
        tool=tool.name,
        args_digest="fabricated-raw-digest",
        tool_digest=digest(definition(tool)),
        effect=tool.effect,
        replay=tool.replay,
    )
    entries = [
        entry("turn_started", 0),
        entry(
            "message",
            1,
            call_manifest=(manifest,),
            facts={"role": "assistant"},
            history_ref=HistoryRef(
                provider="fabricated",
                key="source",
                sha256="fabricated",
                owner="owner",
                session_id="session",
            ),
        ),
    ]
    inv = digest(["session", 1, 0])
    if state != "not_started":
        entries.append(
            entry(
                "tool_call",
                len(entries),
                key="intent:" + inv,
                facts={
                    "invocation_id": inv,
                    "status": "started",
                    "tool": tool.name,
                    "call_id": manifest.call_id,
                    "tool_digest": manifest.tool_digest,
                    "effect": tool.effect,
                    "replay": tool.replay,
                },
            )
        )
    if state == "finished":
        entries.append(
            entry(
                "tool_call",
                len(entries),
                key="result:" + inv,
                facts={"invocation_id": inv, "status": "ok", "tool": tool.name},
            )
        )
    if state == "resolved":
        entries.append(
            entry(
                "resolved",
                len(entries),
                key="resolution:" + inv,
                facts={"invocation_id": inv, "outcome": "completed"},
            )
        )
    entries.append(entry("turn_ended", len(entries), facts={"stop_reason": "interrupted"}))
    return tuple(entries), inv


@pytest.mark.parametrize(
    ("state", "safe", "expected"),
    [
        ("not_started", False, "run"),
        ("started", False, "record_interrupted"),
        ("started", True, "rerun"),
        ("finished", False, None),
        ("resolved", False, None),
    ],
)
def test_derived_invocation_state_selects_only_declared_recovery(state, safe, expected):
    tool = DemoTool()
    if safe:
        tool.replay = "safe"
    entries, inv = journal(tool, state)
    before = tuple(e.model_dump(mode="json") for e in entries)
    plan = plan_resume(entries, {tool.name: declaration(tool)}, session_id="session")
    assert plan.interrupted_turn_id == "original"
    assert plan.steps[-1].kind == "model_step"
    if expected is None:
        assert len(plan.steps) == 1
    else:
        step = plan.steps[0]
        assert (step.kind, step.invocation_id, step.call_entry_index, step.call_position) == (
            expected,
            inv,
            1,
            0,
        )
    assert plan_resume(entries, {tool.name: declaration(tool)}, session_id="session") == plan
    assert tuple(e.model_dump(mode="json") for e in entries) == before
    assert not tool.calls


@pytest.mark.parametrize("change", ["missing", "version", "unsafe", "effect"])
def test_started_safe_call_never_replays_under_changed_declaration(change):
    tool = DemoTool()
    tool.replay = "safe"
    entries, inv = journal(tool, "started")
    tools = {tool.name: declaration(tool)}
    if change == "missing":
        tools.clear()
    elif change == "version":
        tool.version = "2"
    elif change == "unsafe":
        tool.replay = "unsafe"
    else:
        tool.effect = "read_only"
    tools = {tool.name: declaration(tool)} if change != "missing" else {}
    step = plan_resume(entries, tools, session_id="session").steps[0]
    assert (step.kind, step.invocation_id) == ("record_interrupted", inv)
    assert not tool.calls


@pytest.mark.parametrize("stop", ["cancelled", "final", "confirmation_required", "model_denied"])
def test_explicit_cancel_and_noninterrupted_terminals_are_never_resumed(stop):
    entries, _ = journal(DemoTool())
    entries = (*entries[:-1], entries[-1].model_copy(update={"facts": {"stop_reason": stop}}))
    with pytest.raises(NotInterrupted):
        plan_resume(entries, {}, session_id="session")


def test_chain_reuses_original_source_positions_and_resolved_calls_stay_finished():
    tool = DemoTool()
    entries, inv = journal(tool, "resolved")
    entries += (
        entry("turn_started", len(entries), turn_id="continued", facts={"resumes": "original"}),
        entry(
            "turn_ended",
            len(entries) + 1,
            turn_id="continued",
            facts={"stop_reason": "interrupted"},
        ),
    )
    assert resume_chain(entries, "continued") == ("continued", "original")
    plan = plan_resume(entries, {tool.name: declaration(tool)}, session_id="session")
    assert plan.interrupted_turn_id == "continued"
    assert [step.kind for step in plan.steps] == ["model_step"]
    assert all(step.invocation_id != inv for step in plan.steps)


@pytest.mark.parametrize("prior", ["missing", "continued", 5])
def test_broken_or_cyclic_chains_refuse(prior):
    entries, _ = journal(DemoTool())
    entries += (
        entry("turn_started", len(entries), turn_id="continued", facts={"resumes": prior}),
        entry(
            "turn_ended",
            len(entries) + 1,
            turn_id="continued",
            facts={"stop_reason": "interrupted"},
        ),
    )
    with pytest.raises(ExecutionDenied, match="resume_chain_invalid"):
        plan_resume(entries, {}, session_id="session")


def test_already_accepted_final_reply_does_not_schedule_another_model_call():
    entries = (
        entry("turn_started", 0),
        entry(
            "message",
            1,
            key="source:final",
            facts={"role": "assistant"},
            history_ref=HistoryRef(
                provider="fabricated",
                key="final",
                sha256="fabricated",
                owner="owner",
                session_id="session",
            ),
        ),
        entry(
            "model_call",
            2,
            facts={"status": "ok", "finish_reason": "stop", "model_call_id": "final"},
        ),
        entry("turn_ended", 3, facts={"stop_reason": "interrupted"}),
    )
    assert plan_resume(entries, {}, session_id="session").steps == ()


@pytest.mark.parametrize("state", ["not_started", "started", "finished", "resolved"])
def test_wrong_session_identity_never_reclassifies_existing_invocation_as_new(state):
    entries, _ = journal(DemoTool(), state)
    with pytest.raises(ExecutionDenied, match="resume_invocation_scope"):
        plan_resume(entries, {}, session_id="wrong-session")


@pytest.mark.parametrize("key", ["intent", "result", "resolution"])
def test_reserved_execution_keys_cannot_be_shadowed_by_unrelated_facts(key):
    state = {"intent": "started", "result": "finished", "resolution": "resolved"}[key]
    entries, inv = journal(DemoTool(), state)
    entries = tuple(
        e.model_copy(update={"kind": "x.fabricated"}) if e.key == key + ":" + inv else e
        for e in entries
    )
    with pytest.raises(ExecutionDenied, match="resume_invocation_invalid"):
        plan_resume(entries, {}, session_id="session")


def test_repeated_model_call_ids_have_distinct_invocations_by_source_position():
    tool = DemoTool()
    entries, first = journal(tool)
    second_source = entries[1].model_copy(update={"index": len(entries) - 1})
    entries = (*entries[:-1], second_source, entries[-1].model_copy(update={"index": len(entries)}))
    plan = plan_resume(entries, {tool.name: declaration(tool)}, session_id="session")
    ids = [step.invocation_id for step in plan.steps if step.kind == "run"]
    assert ids == [first, digest(["session", second_source.index, 0])]
    assert ids[0] != ids[1]


@pytest.mark.parametrize("role", ["summary", "missing"])
def test_summary_or_unbound_terminal_metadata_cannot_finish_recovery(role):
    entries, _ = journal(DemoTool(), "finished")
    call = entry(
        "model_call",
        len(entries) - 1,
        facts={
            "status": "ok",
            "finish_reason": "stop",
            "purpose": role if role == "summary" else "answer",
        },
    )
    entries = (*entries[:-1], call, entries[-1].model_copy(update={"index": len(entries)}))
    if role == "summary":
        assert plan_resume(entries, {}, session_id="session").steps[-1].kind == "model_step"
    else:
        with pytest.raises(ExecutionDenied, match="resume_final_source_missing"):
            plan_resume(entries, {}, session_id="session")


@pytest.mark.parametrize("prior_stop", ["cancelled", "final", "confirmation_required"])
def test_ancestry_cannot_revive_a_noninterrupted_parent(prior_stop):
    entries, _ = journal(DemoTool())
    entries = (*entries[:-1], entries[-1].model_copy(update={"facts": {"stop_reason": prior_stop}}))
    entries += (
        entry("turn_started", len(entries), turn_id="continued", facts={"resumes": "original"}),
        entry(
            "turn_ended",
            len(entries) + 1,
            turn_id="continued",
            facts={"stop_reason": "interrupted"},
        ),
    )
    with pytest.raises(ExecutionDenied, match="resume_chain_invalid"):
        plan_resume(entries, {}, session_id="session")


def test_ancestry_cannot_reach_across_a_superseding_input_branch():
    entries, _ = journal(DemoTool())
    entries += (
        entry("turn_started", len(entries), turn_id="replacement"),
        entry(
            "turn_ended", len(entries) + 1, turn_id="replacement", facts={"stop_reason": "final"}
        ),
    )
    entries += (
        entry("turn_started", len(entries), turn_id="continued", facts={"resumes": "original"}),
        entry(
            "turn_ended",
            len(entries) + 1,
            turn_id="continued",
            facts={"stop_reason": "interrupted"},
        ),
    )
    with pytest.raises(ExecutionDenied, match="resume_chain_invalid"):
        plan_resume(entries, {}, session_id="session")


def test_missing_or_reordered_journal_positions_never_become_a_recovery_plan():
    entries, _ = journal(DemoTool())
    with pytest.raises(ExecutionDenied, match="resume_entry_order"):
        plan_resume((entries[1], entries[0], *entries[2:]), {}, session_id="session")


@pytest.mark.parametrize("change", ["missing_ref", "session", "role"])
def test_unstarted_invocation_requires_a_scoped_assistant_source(change):
    entries, _ = journal(DemoTool())
    source = entries[1]
    if change == "missing_ref":
        source = source.model_copy(update={"history_ref": None})
    elif change == "session":
        assert source.history_ref is not None
        source = source.model_copy(
            update={"history_ref": source.history_ref.model_copy(update={"session_id": "other"})}
        )
    else:
        source = source.model_copy(update={"facts": {"role": "user"}})
    entries = (entries[0], source, *entries[2:])
    with pytest.raises(ExecutionDenied, match="resume_invocation_scope"):
        plan_resume(entries, {}, session_id="session")
