"""Derive release obligations and verify observed source-bound acceptance evidence."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import TypedDict

from web_artifacts import MATRIX, inventory

PLAN = "docs/helix/04-build/web-platform-implementation-plan.md"
STP = "docs/helix/03-test/test-plans/STP-001-web-platform-acceptance.md"
PRD = "docs/helix/01-frame/prd.md"
MANIFEST = "docs/helix/04-build/evidence/web-platform/acceptance-closure.json"
REQUIREMENTS = {*(f"FR-{number}" for number in range(65, 71)), "NFR-9"}
# These run this audit or prepare the subsequent authorized disposition; requiring
# their own completed checkboxes here would make the release gate circular.
CONTROL_TASKS = {"WP-39", "WP-40", "WP-44"}
INSTALLED_COMMAND = "make web-package-check / clean build and installed matrix"


class AuditResult(TypedDict):
    requirement_count: int
    criteria_count: int
    primary_tasks: int
    errors: list[str]
    scope: str


class Task(TypedDict):
    completed: bool
    dependencies: list[str]


def ac_inventory(root: Path) -> tuple[set[str], list[str]]:
    values = []
    for number in range(65, 70):
        files = list((root / "docs/helix/01-frame/user-stories").glob(f"US-{number:03}-*.md"))
        if len(files) != 1:
            raise ValueError(f"one_governing_story_required:US-{number:03}")
        values.extend(
            re.findall(r"^- \[[ x]\] \*\*(US-\d{3}-AC\d+)\*\*", files[0].read_text(), re.M)
        )
    duplicates = [key for key, count in Counter(values).items() if count != 1]
    if not values or duplicates:
        raise ValueError("empty_or_duplicate_acceptance_inventory")
    mapped = re.findall(r"^\| (US-\d{3}-AC\d+) \|", (root / STP).read_text(), re.M)
    errors = []
    if Counter(mapped) != Counter(values):
        errors.append("STP acceptance mapping is missing, duplicate or extra")
    return set(values), errors


def task_inventory(plan: str) -> tuple[dict[str, Task], list[str]]:
    pattern = r"^- \[([ x])\] \*\*(WP-\d{2}) —[^\n]+\*\* Depends: ([^\n]+)"
    tasks: dict[str, Task] = {}
    errors = []
    for done, task, dependency in re.findall(pattern, plan, re.M):
        if task in tasks:
            errors.append("duplicate task:" + task)
        expanded = re.sub(
            r"WP-(\d{2}) through WP-(\d{2})",
            lambda m: " ".join(f"WP-{n:02}" for n in range(int(m[1]), int(m[2]) + 1)),
            dependency,
        )
        tasks[task] = {"completed": done == "x", "dependencies": re.findall(r"WP-\d{2}", expanded)}
    if set(tasks) != {f"WP-{number:02}" for number in range(45)}:
        errors.append("primary task inventory must contain WP-00 through WP-44 once")
    done, active = set(), set()

    def visit(task: str) -> None:
        if task in active:
            errors.append("cyclic task dependency:" + task)
            return
        if task in done:
            return
        if task not in tasks:
            errors.append("unknown dependency:" + task)
            return
        active.add(task)
        for parent in tasks[task]["dependencies"]:
            visit(parent)
        active.remove(task)
        done.add(task)

    for task in tasks:
        visit(task)
    return tasks, errors


def primary_mapping(plan: str, criteria: set[str]) -> tuple[dict[str, str], list[str]]:
    result, errors = {}, []
    for line in plan.splitlines():
        cells = line.split("|")
        if len(cells) < 5 or not re.search(r"US-\d{3}-AC", cells[2]):
            continue
        found = re.search(r"US-\d{3}", cells[2])
        if found is None:
            errors.append("missing story in acceptance mapping")
            continue
        story = found[0]
        task = re.search(r"WP-\d{2}", cells[3])
        for short in re.findall(r"AC\d+", cells[2]):
            ac = story + "-" + short
            if ac in result or task is None:
                errors.append("missing_or_duplicate_primary_task:" + ac)
            elif task is not None:
                result[ac] = task[0]
    if set(result) != criteria:
        errors.append("primary acceptance mapping does not equal governing stories")
    return result, errors


def requirement_edges(
    root: Path, plan: str, criteria: set[str], tasks: dict[str, Task]
) -> list[str]:
    authority = set(re.findall(r"\*\*((?:NFR|FR)-\d+)\*\*", (root / PRD).read_text()))
    errors = ["governing requirement absent:" + item for item in sorted(REQUIREMENTS - authority)]
    found: set[str] = set()
    for line in plan.splitlines():
        cells = line.split("|")
        if len(cells) < 5:
            continue
        named: set[str] = set()
        for prefix, numbers in re.findall(r"((?:NFR|FR)-)(\d+(?:/\d+)*)", cells[1]):
            named.update(prefix + number for number in numbers.split("/"))
        selected = named & REQUIREMENTS
        if not selected:
            continue
        linked = re.findall(r"WP-\d{2}", cells[3])
        if not linked or any(task not in tasks for task in linked):
            errors.append("missing_or_unknown_requirement_task:" + ",".join(sorted(selected)))
        story = re.search(r"US-\d{3}", cells[2])
        covered = (
            {story[0] + "-" + ac for ac in re.findall(r"AC\d+", cells[2])}
            if story is not None
            else criteria
            if cells[2].strip() == "All five story sets"
            else set()
        )
        if not covered or not covered <= criteria:
            errors.append("missing_or_unknown_requirement_criteria:" + ",".join(sorted(selected)))
        found.update(selected)
    errors.extend(
        "requirement_has_no_acceptance_edge:" + item for item in sorted(REQUIREMENTS - found)
    )
    return errors


def relative_file(root: Path, value: object, prefix: str = "") -> Path:
    if not isinstance(value, str):
        raise ValueError("evidence path must be explicit")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or (prefix and not value.startswith(prefix)):
        raise ValueError("evidence path escapes allowed root")
    full = (root / path).resolve()
    if root.resolve() not in full.parents or not full.is_file():
        raise ValueError("missing or escaping evidence path:" + value)
    return full


def read_receipt(root: Path, value: str, current: dict[str, str]) -> dict[str, str]:
    path = relative_file(root, value, "docs/helix/04-build/evidence/web-platform/")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("receipt exceeds bounded size")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("receipt must be an explicit JSON object")
    if data.get("status") != "passed" or data.get("source_sha256") != current:
        raise ValueError("receipt failed or source inputs changed:" + value)
    if not data.get("command") or not data.get("recorded_at"):
        raise ValueError("receipt missing observed command/time:" + value)
    results = data.get("test_results")
    if not isinstance(results, list) or not results:
        raise ValueError("receipt has no observed exact test outcomes:" + value)
    observed = {}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("observed result must be an object")
        node, outcome = result.get("node_id"), result.get("outcome")
        if (
            not isinstance(node, str)
            or not node
            or node in observed
            or not isinstance(outcome, str)
        ):
            raise ValueError("invalid duplicate or missing observed test identity:" + value)
        observed[node] = outcome
    _validate_bound_receipt(root, path, data, current, observed, value)
    return observed


def _validate_bound_receipt(
    root: Path, path: Path, data: dict, current: dict[str, str], observed: dict, value: str
) -> None:
    python_test_nodes = {
        node
        for node in observed
        if Path(node.split("::", 1)[0]).suffix == ".py"
        and Path(node.split("::", 1)[0]).parts[0] in {"tests", "packages", "examples"}
    }
    python_markers = (
        data.get("schema_version") == 2
        or data.get("profile") == "local-deterministic"
        or data.get("command") == "make check / bounded Python aggregate"
        or "groups" in data
        or "coverage_returncode" in data
    )
    installed_matrix = data.get("command") == INSTALLED_COMMAND
    if python_markers or (python_test_nodes and not installed_matrix):
        from test_evidence import validate_python_receipt

        validate_python_receipt(data, current)
    elif installed_matrix:
        _validate_installed_embedder(data, current, observed, python_test_nodes)
    if "scripts/web_quality.py::python-quality" in observed and "python_quality" not in data:
        raise ValueError("aggregate_python_evidence_binding_missing:" + value)
    if "python_quality" in data:
        from web_gate_receipt import verify_aggregate

        verify_aggregate(root, path)
    if any(node.startswith("scripts/benchmark_ratchet.py::") for node in observed):
        from web_performance_receipt import verify_reference_ratchet

        verify_reference_ratchet(root, path, data, current)


def _validate_installed_embedder(
    data: dict, current: dict[str, str], observed: dict[str, str], python_nodes: set[str]
) -> None:
    """Validate the standalone pytest outcomes carried by the installed matrix."""
    matrix = data.get("installed_profiles")
    if (
        type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or set(data)
        != {
            "status",
            "schema_version",
            "command",
            "recorded_at",
            "python",
            "build_constraints",
            "runtime_constraints",
            "source_sha256",
            "artifacts",
            "wheel_metadata",
            "installed_profiles",
            "independent_embedder",
            "compatibility",
            "test_results",
            "limits",
        }
        or not isinstance(matrix, dict)
        or set(matrix) != set(MATRIX)
        or observed.get("installed::cohort::reproducible-archives") != "passed"
        or any(observed.get(f"installed::{name}::closure") != "passed" for name in MATRIX)
    ):
        raise ValueError("installed_matrix_receipt_invalid")
    embedder = data["independent_embedder"]
    if (
        not isinstance(embedder, dict)
        or set(embedder)
        != {
            "status",
            "sbom",
            "lock_sha256",
            "consumer_source",
            "wheel_backed_sources",
            "test_results",
            "strict_installed_typing",
            "limits",
        }
        or embedder["status"] != "passed"
        or embedder["consumer_source"] != "examples/embed"
        or embedder["strict_installed_typing"] is not True
        or not isinstance(embedder["lock_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", embedder["lock_sha256"]) is None
        or not isinstance(embedder["test_results"], list)
        or not python_nodes
        or any(
            not node.startswith("examples/embed/tests/")
            or "::" not in node
            or node.split("::", 1)[0] not in current
            or observed[node] != "passed"
            for node in python_nodes
        )
        or embedder["test_results"]
        != [{"node_id": node, "outcome": "passed"} for node in observed if node in python_nodes]
    ):
        raise ValueError("installed_embedder_receipt_invalid")


def case_inputs(root: Path, case: dict, current: dict[str, str]) -> tuple[list, list]:
    paths = case.get("implementation")
    if not isinstance(paths, list) or not paths:
        raise ValueError("missing implementation paths")
    for path in paths:
        relative_file(root, path)
        if path not in current:
            raise ValueError("implementation absent from tested source identity")
    tests, receipts = case.get("tests"), case.get("receipts")
    if not isinstance(tests, list) or not tests or len(tests) != len(set(tests)):
        raise ValueError("missing or duplicate required exact test IDs")
    for test in tests:
        if not isinstance(test, str) or not test:
            raise ValueError("required test identity must be explicit")
        if not test.startswith(("installed::", "platform::")):
            source = test.split("::", 1)[0]
            relative_file(root, source)
            if source not in current:
                raise ValueError("proving test absent from tested source identity")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("no observed receipts")
    return tests, receipts


def case_errors(
    root: Path, ac: str, case: object, primary: str, current: dict[str, str]
) -> list[str]:
    if not isinstance(case, dict):
        return [ac + ": missing executable acceptance proof"]
    errors = []
    if case.get("primary_task") != primary:
        errors.append(ac + ": wrong primary task")
    try:
        tests, receipts = case_inputs(root, case, current)
        if ac.startswith("US-"):
            citation_paths = set(case["implementation"])
            citation_paths.update(
                test.split("::", 1)[0]
                for test in tests
                if not test.startswith(("installed::", "platform::"))
            )
            if not any(
                "@covers " + ac in relative_file(root, path).read_text() for path in citation_paths
            ):
                errors.append(ac + ": required @covers citation absent from proving source")
        observed = {}
        for receipt in receipts:
            observed.update(read_receipt(root, receipt, current))
        for test in tests:
            if observed.get(test) != "passed":
                errors.append(ac + ": required test not observed passing:" + str(test))
    except (OSError, ValueError, TypeError) as error:
        errors.append(ac + ": " + str(error))
    return errors


def _expected_reference_nodes() -> set[str]:
    from benchmark_profiles import METRICS, PROFILES

    return {
        f"scripts/benchmark_ratchet.py::{PROFILES[profile].profile}::{metric}"
        for profile in PROFILES
        for metric in METRICS[profile]
    }


def _referenced_performance_nodes(
    root: Path, proofs: list[object], current: dict[str, str]
) -> set[str]:
    observed: set[str] = set()
    for proof in proofs:
        if not isinstance(proof, dict) or not isinstance(proof.get("receipts"), list):
            continue
        for receipt in proof["receipts"]:
            if not isinstance(receipt, str):
                continue
            try:
                results = read_receipt(root, receipt, current)
            except (OSError, ValueError, TypeError):
                continue
            observed.update(
                node for node in results if node.startswith("scripts/benchmark_ratchet.py::")
            )
    return observed


def _performance_union_errors(
    root: Path, cases: dict, proofs: dict, current: dict[str, str], *, required: bool
) -> list[str]:
    nodes = _referenced_performance_nodes(root, [*cases.values(), *proofs.values()], current)
    if (required or nodes) and nodes != _expected_reference_nodes():
        return ["qualified performance receipts must cover the exact admitted metric set"]
    return []


def audit(root: Path, *, inventory_only: bool = False) -> AuditResult:
    criteria, errors = ac_inventory(root)
    plan = (root / PLAN).read_text()
    tasks, task_errors = task_inventory(plan)
    mapping, map_errors = primary_mapping(plan, criteria)
    errors.extend([*task_errors, *map_errors])
    errors.extend(requirement_edges(root, plan, criteria, tasks))
    result: AuditResult = {
        "requirement_count": len(REQUIREMENTS),
        "criteria_count": len(criteria),
        "primary_tasks": len(tasks),
        "errors": errors,
        "scope": "observed acceptance and completed work; final control tasks remain explicit",
    }
    if inventory_only:
        result["scope"] = "structural inventory only; no runtime acceptance"
        return result
    return _audit_runtime_evidence(root, result, criteria, tasks, mapping)


def _audit_runtime_evidence(
    root: Path,
    result: AuditResult,
    criteria: set[str],
    tasks: dict[str, Task],
    mapping: dict[str, str],
) -> AuditResult:
    errors = result["errors"]
    current = inventory(root)
    path = root / MANIFEST
    data = json.loads(path.read_text()) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError("closure manifest must be an explicit JSON object")
    cases = data.get("cases", {})
    if data.get("source_sha256") != current:
        errors.append("closure manifest missing or source identity changed")
    if not isinstance(cases, dict) or set(cases) != criteria:
        errors.append("closure cases must equal governing acceptance inventory exactly")
        cases = cases if isinstance(cases, dict) else {}
    for ac in sorted(criteria):
        errors.extend(case_errors(root, ac, cases.get(ac), mapping.get(ac, ""), current))
    proofs = data.get("tasks", {})
    if not isinstance(proofs, dict):
        proofs = {}
    for task, value in tasks.items():
        if task in CONTROL_TASKS:
            continue
        if not value["completed"]:
            errors.append("work task remains unchecked:" + task)
        errors.extend(case_errors(root, task, proofs.get(task), task, current))
    if isinstance(proofs, dict):
        errors.extend(
            _performance_union_errors(
                root,
                cases,
                proofs,
                current,
                required=False,
            )
        )
    return result
