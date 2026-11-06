"""Acceptance-closure parsers refuse malformed plans, paths, receipts and case inputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import web_closure as closure

CRITERIA = {"US-065-AC1", "US-065-AC2"}


def _plan(overrides: dict[int, str] | None = None, *, skip: set[int] | None = None) -> str:
    lines = []
    for number in range(45):
        if skip and number in skip:
            continue
        dependency = (overrides or {}).get(number, "none" if number == 0 else f"WP-{number - 1:02}")
        lines.append(f"- [x] **WP-{number:02} — task {number}** Depends: {dependency}")
    return "\n".join(lines)


def test_task_inventory_accepts_chain_and_expands_ranges() -> None:
    plan = _plan({10: "WP-00 through WP-03"})
    tasks, errors = closure.task_inventory(plan)
    assert errors == []
    assert tasks["WP-10"]["dependencies"] == ["WP-00", "WP-01", "WP-02", "WP-03"]
    assert tasks["WP-00"]["completed"] is True


def test_task_inventory_reports_incomplete_duplicate_cyclic_and_unknown() -> None:
    _, errors = closure.task_inventory(_plan(skip={44}))
    assert "primary task inventory must contain WP-00 through WP-44 once" in errors
    duplicated = _plan() + "\n- [ ] **WP-03 — again** Depends: WP-00"
    _, errors = closure.task_inventory(duplicated)
    assert "duplicate task:WP-03" in errors
    _, errors = closure.task_inventory(_plan({0: "WP-44"}))
    assert any(error.startswith("cyclic task dependency:") for error in errors)
    _, errors = closure.task_inventory(_plan({5: "WP-90"}))
    assert "unknown dependency:WP-90" in errors


def _row(requirement: str, stories: str, tasks: str) -> str:
    return f"| {requirement} | {stories} | {tasks} | x |"


def test_primary_mapping_collects_each_acceptance_criterion_once() -> None:
    plan = _row("FR-65", "US-065-AC1, AC2", "WP-01")
    result, errors = closure.primary_mapping(plan, CRITERIA)
    assert errors == []
    assert result == {"US-065-AC1": "WP-01", "US-065-AC2": "WP-01"}


def test_primary_mapping_refuses_duplicates_missing_tasks_and_unmapped_criteria() -> None:
    plan = "\n".join([_row("FR-65", "US-065-AC1", "WP-01"), _row("FR-65", "US-065-AC1", "WP-02")])
    _, errors = closure.primary_mapping(plan, CRITERIA)
    assert "missing_or_duplicate_primary_task:US-065-AC1" in errors
    assert "primary acceptance mapping does not equal governing stories" in errors
    _, errors = closure.primary_mapping(_row("FR-65", "US-065-AC2", "no task"), CRITERIA)
    assert "missing_or_duplicate_primary_task:US-065-AC2" in errors


def _prd(tmp_path: Path, present: set[str]) -> None:
    path = tmp_path / closure.PRD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"**{item}** text" for item in sorted(present)))


def test_requirement_edges_demand_authority_tasks_and_criteria(tmp_path: Path) -> None:
    _prd(tmp_path, set(closure.REQUIREMENTS))
    tasks: Any = {"WP-01": {"completed": True, "dependencies": []}}
    rows = [_row(f"FR-{n}", "All five story sets", "WP-01") for n in range(65, 71)] + [
        _row("NFR-9", "US-065-AC1", "WP-01")
    ]
    assert closure.requirement_edges(tmp_path, "\n".join(rows), CRITERIA, tasks) == []

    only_one = _row("FR-65/66", "US-065-AC1", "WP-01")
    errors = closure.requirement_edges(tmp_path, only_one, CRITERIA, tasks)
    assert "requirement_has_no_acceptance_edge:FR-67" in errors
    assert "requirement_has_no_acceptance_edge:NFR-9" in errors

    broken = "\n".join(
        [
            _row("FR-65", "US-065-AC1", "WP-77"),
            _row("FR-66", "US-065-AC9", "WP-01"),
            _row("FR-67", "unmapped", "WP-01"),
        ]
    )
    errors = closure.requirement_edges(tmp_path, broken, CRITERIA, tasks)
    assert "missing_or_unknown_requirement_task:FR-65" in errors
    assert "missing_or_unknown_requirement_criteria:FR-66" in errors
    assert "missing_or_unknown_requirement_criteria:FR-67" in errors

    _prd(tmp_path, {"FR-65"})
    errors = closure.requirement_edges(tmp_path, "", CRITERIA, tasks)
    assert "governing requirement absent:NFR-9" in errors


@pytest.mark.parametrize("value", [7, "/abs/file", "../escape", "docs/other/file", "docs/missing"])
def test_relative_file_refuses_unsafe_or_missing_paths(tmp_path: Path, value: object) -> None:
    (tmp_path / "docs").mkdir()
    with pytest.raises(ValueError, match=r"evidence path|missing or escaping"):
        closure.relative_file(tmp_path, value, "docs/helix/")


def test_relative_file_accepts_only_contained_regular_files(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "file.txt").write_text("x")
    assert (
        closure.relative_file(tmp_path, "docs/file.txt") == (tmp_path / "docs/file.txt").resolve()
    )
    with pytest.raises(ValueError, match="missing or escaping"):
        closure.relative_file(tmp_path, "docs")


PREFIX = "docs/helix/04-build/evidence/web-platform/"


def _write_receipt(root: Path, name: str, payload: object) -> str:
    path = root / PREFIX / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return PREFIX + name


def _receipt(**change: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "status": "passed",
        "source_sha256": {"a.py": "1"},
        "command": "fabricated",
        "recorded_at": "2026-11-01T00:00:00+00:00",
        "test_results": [{"node_id": "scripts/web_check.py::one", "outcome": "passed"}],
    }
    return value | change


def test_read_receipt_returns_observed_outcomes(tmp_path: Path) -> None:
    name = _write_receipt(tmp_path, "ok.json", _receipt())
    observed = closure.read_receipt(tmp_path, name, {"a.py": "1"})
    assert observed == {"scripts/web_check.py::one": "passed"}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("[]", "explicit JSON object"),
        (_receipt(status="failed"), "failed or source inputs changed"),
        (_receipt(source_sha256={"a.py": "2"}), "failed or source inputs changed"),
        (_receipt(command=""), "missing observed command/time"),
        (_receipt(recorded_at=None), "missing observed command/time"),
        (_receipt(test_results=[]), "no observed exact test outcomes"),
        (_receipt(test_results=["x"]), "observed result must be an object"),
        (_receipt(test_results=[{"node_id": "", "outcome": "passed"}]), "invalid duplicate"),
        (_receipt(test_results=[{"node_id": "n", "outcome": 1}]), "invalid duplicate"),
        (
            _receipt(
                test_results=[{"node_id": "n", "outcome": "passed"}] * 2,
            ),
            "invalid duplicate",
        ),
        (
            _receipt(
                test_results=[
                    {"node_id": "scripts/web_quality.py::python-quality", "outcome": "passed"}
                ]
            ),
            "aggregate_python_evidence_binding_missing",
        ),
    ],
)
def test_read_receipt_refuses_unobserved_or_unbound_evidence(
    tmp_path: Path, payload: object, message: str
) -> None:
    name = _write_receipt(tmp_path, "bad.json", payload)
    with pytest.raises(ValueError, match=message):
        closure.read_receipt(tmp_path, name, {"a.py": "1"})


def test_read_receipt_refuses_oversized_files(tmp_path: Path) -> None:
    name = _write_receipt(tmp_path, "big.json", "{}")
    with (tmp_path / name).open("ab") as stream:
        stream.truncate(8 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="bounded size"):
        closure.read_receipt(tmp_path, name, {})


def _case(root: Path, **change: Any) -> dict[str, Any]:
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "impl.py").write_text("x")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_proof.py").write_text("x")
    value = {
        "implementation": ["src/impl.py"],
        "tests": ["tests/test_proof.py::test_a", "installed::flow"],
        "receipts": ["r.json"],
    }
    return value | change


def test_case_inputs_accept_bound_sources_and_virtual_nodes(tmp_path: Path) -> None:
    current = {"src/impl.py": "1", "tests/test_proof.py": "2"}
    tests, receipts = closure.case_inputs(tmp_path, _case(tmp_path), current)
    assert tests == ["tests/test_proof.py::test_a", "installed::flow"]
    assert receipts == ["r.json"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"implementation": []}, "missing implementation paths"),
        ({"implementation": "src/impl.py"}, "missing implementation paths"),
        ({"implementation": ["src/other.py"]}, "missing or escaping"),
        ({"tests": []}, "missing or duplicate required exact test IDs"),
        ({"tests": ["installed::a", "installed::a"]}, "missing or duplicate"),
        ({"tests": [""]}, "required test identity must be explicit"),
        ({"tests": [5]}, "required test identity must be explicit"),
        ({"receipts": []}, "no observed receipts"),
        ({"receipts": "r.json"}, "no observed receipts"),
    ],
)
def test_case_inputs_refuse_incomplete_cases(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    current = {"src/impl.py": "1", "tests/test_proof.py": "2"}
    with pytest.raises(ValueError, match=message):
        closure.case_inputs(tmp_path, _case(tmp_path, **change), current)


def test_case_inputs_refuse_sources_outside_tested_identity(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(ValueError, match="implementation absent"):
        closure.case_inputs(tmp_path, case, {"tests/test_proof.py": "2"})
    with pytest.raises(ValueError, match="proving test absent"):
        closure.case_inputs(tmp_path, case, {"src/impl.py": "1"})


def test_case_errors_flag_non_object_and_wrong_primary_task(tmp_path: Path) -> None:
    assert closure.case_errors(tmp_path, "US-065-AC1", None, "WP-01", {}) == [
        "US-065-AC1: missing executable acceptance proof"
    ]
    errors = closure.case_errors(
        tmp_path, "NFR-9", {"primary_task": "WP-02", "implementation": []}, "WP-01", {}
    )
    assert "NFR-9: wrong primary task" in errors
