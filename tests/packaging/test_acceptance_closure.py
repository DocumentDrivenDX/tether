"""Release closure rejects paper mappings, stale inputs and skipped required cases."""

import hashlib
import json

import pytest
import web_closure
from test_evidence import ObservedEvidence
from web_artifacts import MATRIX, inventory
from web_closure import (
    MANIFEST,
    PLAN,
    PRD,
    REQUIREMENTS,
    STP,
    _expected_reference_nodes,
    _performance_union_errors,
    audit,
    case_errors,
    read_receipt,
    task_inventory,
)


def fixture_project(root):
    def write(path, text):
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    criteria = [f"US-{n:03}-AC1" for n in range(65, 70)]
    for number, ac in zip(range(65, 70), criteria, strict=True):
        write(
            f"docs/helix/01-frame/user-stories/US-{number:03}-fabricated.md",
            f"- [ ] **{ac}** — Fabricated criterion\n",
        )
    write(STP, "\n".join(f"| {ac} | Fabricated proving case |" for ac in criteria))
    write(
        PRD,
        "\n".join(
            "- **" + item + "** — Fabricated governing requirement." for item in REQUIREMENTS
        ),
    )
    lines = [
        f"- [x] **WP-{n:02} — Fabricated.** Depends: " + (f"WP-{n - 1:02}." if n else "none.")
        for n in range(45)
    ]
    lines += [
        f"| {requirement} | {ac} | WP-08 |"
        for requirement, ac in zip(
            ["FR-66", "FR-65/67", "FR-68", "FR-69", "FR-70"], criteria, strict=True
        )
    ]
    lines += ["| NFR-9 | All five story sets | WP-01/WP-39 |"]
    write(PLAN, "\n".join(lines))
    write("src/tether/fabricated.py", "# Explicit unit fixture, not a release receipt.\n")
    write("scripts/benchmark_ratchet.py", "# Fabricated strict-performance fixture source.\n")
    write("scripts/performance_admission.py", "# Fabricated isolated admission workflow.\n")
    write(
        "tests/test_fabricated.py",
        "\n".join("# @covers " + ac for ac in criteria) + "\ndef test_fabricated(): pass\n",
    )
    write(
        "tests/test_performance_controls.py",
        "def test_isolated_workflow_admission_controls(): pass\n",
    )
    source = inventory(root)
    receipt_path = "docs/helix/04-build/evidence/web-platform/fabricated.json"
    node = "tests/test_fabricated.py::test_fabricated"
    observed = ObservedEvidence(source)
    observed.inventory = [node]
    observed.inventory_complete = True
    observed.record_group([node], "passed", [{"node_id": node, "outcome": "passed"}], 0)
    observed.coverage_returncode = 0
    evidence_path = root / receipt_path
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    observed.write(root, 0, evidence_path)

    def proof(task):
        return {
            "primary_task": task,
            "implementation": ["src/tether/fabricated.py"],
            "tests": [node],
            "receipts": [receipt_path],
        }

    manifest = {
        "source_sha256": source,
        "cases": {ac: proof("WP-08") for ac in criteria},
        "tasks": {f"WP-{n:02}": proof(f"WP-{n:02}") for n in range(45)},
    }
    performance_receipts, performance_nodes = _write_performance_fixture(root, source)
    manifest["tasks"]["WP-36"] = {
        **proof("WP-36"),
        "implementation": ["scripts/benchmark_ratchet.py"],
        "tests": performance_nodes,
        "receipts": performance_receipts,
    }
    write(MANIFEST, json.dumps(manifest))
    return manifest, receipt_path


def _write_performance_fixture(root, source):
    from benchmark_profiles import METRICS, PROFILES
    from performance_admission import REFERENCE_CONTROL_SCOPE, SYSTEM_PROCESS_EXCLUSION_POLICY

    runtime = {"python": "3.11.17", "platform": "Darwin", "architecture": "arm64"}
    baseline_directory = root / "docs/helix/04-build/evidence/web-platform/performance"
    ratchet_directory = (
        root
        / "docs/helix/04-build/evidence/web-platform/reference-performance/qualified-performance"
    )
    baseline_directory.mkdir(parents=True, exist_ok=True)
    ratchet_directory.mkdir(parents=True, exist_ok=True)
    baselines = {}
    baseline_hashes = {}
    for profile, spec in PROFILES.items():
        name = profile + "-baseline.json"
        baseline = {
            "profile": spec.profile,
            "receipt_sha256": hashlib.sha256((profile + "-measured").encode()).hexdigest(),
            "identity": {"versions": runtime},
            "metrics": {metric: {"observed": 1.0, "maximum": 1.2} for metric in METRICS[profile]},
            "tolerance": 1.2,
        }
        raw = (json.dumps(baseline, sort_keys=True, indent=2) + "\n").encode()
        (baseline_directory / name).write_bytes(raw)
        baselines[profile] = baseline
        baseline_hashes[name] = hashlib.sha256(raw).hexdigest()

    def observation(phase):
        return {
            "observer": "process-table-v1",
            "phase": phase,
            "state": "quiet",
            "observed_at_utc": "2026-11-02T00:00:00+00:00",
            "evidence": "same_uid_external_count=0;other_uid_external_count=0",
            "measurement_overhead_ms": 0.0,
        }

    admission = {
        "control_scope": REFERENCE_CONTROL_SCOPE,
        "binding_sha256": "a" * 64,
        "observations": [observation("before"), observation("through"), observation("after")],
        "observer_control": {
            "sampling_interval_seconds": 1.0,
            "measured_observer_overhead_ms": 0.0,
            "maximum_subprocess_call_seconds": 5.0,
            "limits": [
                "worker",
                "bounded writes",
                "co-tenancy unknown",
                SYSTEM_PROCESS_EXCLUSION_POLICY,
            ],
        },
        "source_sha256": source,
        "baseline_sha256": baseline_hashes,
        "status": "admitted",
        "scope": "controlled-local-reference-performance",
        "qualified_profiles": list(PROFILES),
        "runtime_versions": runtime,
    }
    admission_raw = (json.dumps(admission, sort_keys=True, indent=2) + "\n").encode()
    (ratchet_directory / "admission.json").write_bytes(admission_raw)
    admission_sha = hashlib.sha256(admission_raw).hexdigest()

    receipts = []
    nodes = []
    for profile, spec in PROFILES.items():
        metrics = {name: 1.1 for name in METRICS[profile]}
        expected_nodes = [
            f"scripts/benchmark_ratchet.py::{spec.profile}::{name}" for name in sorted(metrics)
        ]
        ratchet = {
            "status": "passed",
            "profile": spec.profile,
            "metrics": metrics,
            "budgets": baselines[profile]["metrics"],
            "baseline_receipt_sha256": baselines[profile]["receipt_sha256"],
            "measurement_sha256": "b" * 64,
            "schema_version": 1,
            "command": "benchmark_ratchet / compare observed profile against measured budgets",
            "recorded_at": "2026-11-02T00:00:00+00:00",
            "source_sha256": source,
            "test_results": [{"node_id": node, "outcome": "passed"} for node in expected_nodes],
            "limits": [
                "Only selected metrics/workload/profile; unmeasured scopes remain unqualified"
            ],
            "admission_path": "admission.json",
            "admission_sha256": admission_sha,
            "baseline_path": profile + "-baseline.json",
        }
        path = ratchet_directory / (profile + "-ratchet.json")
        path.write_text(json.dumps(ratchet))
        receipts.append(path.relative_to(root).as_posix())
        nodes.extend(expected_nodes)
    return receipts, nodes


def test_complete_observed_fixture_passes_and_missing_manifest_refuses(tmp_path):
    fixture_project(tmp_path)
    assert audit(tmp_path)["errors"] == []
    (tmp_path / MANIFEST).unlink()
    assert audit(tmp_path)["errors"]


@pytest.mark.parametrize("outcome", ["skipped", "xfailed", "failed", "missing"])
def test_required_nonpass_or_unobserved_case_blocks_release(tmp_path, outcome):
    _, path = fixture_project(tmp_path)
    receipt = json.loads((tmp_path / path).read_text())
    receipt["test_results"][0]["outcome"] = outcome
    (tmp_path / path).write_text(json.dumps(receipt))
    assert any(
        "not observed passing" in error or "python_evidence_" in error
        for error in audit(tmp_path)["errors"]
    )


def test_changed_source_and_unchecked_work_cannot_reuse_receipts(tmp_path):
    fixture_project(tmp_path)
    (tmp_path / "src/tether/fabricated.py").write_text("changed")
    assert any("source" in error for error in audit(tmp_path)["errors"])
    plan = tmp_path / PLAN
    plan.write_text(plan.read_text().replace("[x] **WP-08", "[ ] **WP-08"))
    assert "work task remains unchecked:WP-08" in audit(tmp_path)["errors"]


def test_missing_source_or_escaping_receipt_cannot_close_task(tmp_path):
    manifest, _ = fixture_project(tmp_path)
    case = manifest["cases"]["US-065-AC1"]
    case["implementation"] = ["missing.py"]
    assert case_errors(tmp_path, "US-065-AC1", case, "WP-08", inventory(tmp_path))
    case["implementation"] = ["src/tether/fabricated.py"]
    case["receipts"] = ["../outside.json"]
    assert case_errors(tmp_path, "US-065-AC1", case, "WP-08", inventory(tmp_path))


def test_python_quality_stage_requires_bound_complete_python_receipt(tmp_path):
    receipt_path = tmp_path / "docs/helix/04-build/evidence/web-platform/fabricated.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "source_sha256": inventory(tmp_path),
                "command": "fabricated fixture",
                "recorded_at": "unit fixture",
                "test_results": [
                    {
                        "node_id": "scripts/web_quality.py::python-quality",
                        "outcome": "passed",
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="aggregate_python_evidence_binding_missing"):
        read_receipt(
            tmp_path,
            "docs/helix/04-build/evidence/web-platform/fabricated.json",
            inventory(tmp_path),
        )


@pytest.mark.parametrize(
    "node",
    [
        "tests/test_fake.py::test_complete",
        "packages/web-api/tests/test_fake.py::test_complete",
        "examples/embed/tests/test_fake.py::test_complete",
    ],
)
def test_standalone_python_receipts_use_the_strict_python_validator(tmp_path, node):
    current = inventory(tmp_path)
    evidence = ObservedEvidence(current)
    evidence.inventory = [node]
    evidence.inventory_complete = True
    evidence.record_group([node], "passed", [{"node_id": node, "outcome": "passed"}], 0)
    evidence.coverage_returncode = 0
    receipt_path = tmp_path / "docs/helix/04-build/evidence/web-platform/python-quality.json"
    receipt_path.parent.mkdir(parents=True)
    evidence.write(tmp_path, 0, receipt_path)
    relative = receipt_path.relative_to(tmp_path).as_posix()
    assert read_receipt(tmp_path, relative, current) == {node: "passed"}

    value = json.loads(receipt_path.read_text())
    value["command"] = "different command"
    for field in ("schema_version", "profile", "groups", "coverage_returncode"):
        value.pop(field)
    receipt_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="python_evidence_schema_invalid"):
        read_receipt(tmp_path, relative, current)

    value = json.loads(evidence.write(tmp_path, 0, tmp_path / "restored-python.json").read_text())
    value["groups"][0]["returncode"] = False
    receipt_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="python_evidence_group_invalid"):
        read_receipt(tmp_path, relative, current)


def test_installed_matrix_requires_bound_external_embedder_outcomes(tmp_path):
    source = tmp_path / "examples/embed/tests/test_consumer.py"
    source.parent.mkdir(parents=True)
    source.write_text("def test_consumer(): pass\n")
    current = inventory(tmp_path)
    path = tmp_path / "docs/helix/04-build/evidence/web-platform/installed.json"
    path.parent.mkdir(parents=True)
    relative = path.relative_to(tmp_path).as_posix()
    case = {"node_id": "examples/embed/tests/test_consumer.py::test_consumer", "outcome": "passed"}
    installed = [
        {"node_id": f"installed::{profile}::closure", "outcome": "passed"} for profile in MATRIX
    ]
    value = {
        "status": "passed",
        "schema_version": 1,
        "command": web_closure.INSTALLED_COMMAND,
        "recorded_at": "fixture observation",
        "python": "3.11",
        "build_constraints": [],
        "runtime_constraints": [],
        "source_sha256": current,
        "artifacts": {},
        "wheel_metadata": {},
        "installed_profiles": {profile: {} for profile in MATRIX},
        "independent_embedder": {
            "status": "passed",
            "sbom": {},
            "lock_sha256": "1" * 64,
            "consumer_source": "examples/embed",
            "wheel_backed_sources": [],
            "test_results": [case],
            "strict_installed_typing": True,
            "limits": [],
        },
        "compatibility": {},
        "test_results": [
            *installed,
            case,
            {"node_id": "installed::cohort::reproducible-archives", "outcome": "passed"},
        ],
        "limits": [],
    }
    path.write_text(json.dumps(value))
    assert read_receipt(tmp_path, relative, current)[case["node_id"]] == "passed"

    value["independent_embedder"]["test_results"] = [{**case, "outcome": "failed"}]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="installed_embedder_receipt_invalid"):
        read_receipt(tmp_path, relative, current)

    del value["independent_embedder"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="installed_matrix_receipt_invalid"):
        read_receipt(tmp_path, relative, current)


def test_controlled_performance_receipt_requires_exact_admission_and_baseline_join(tmp_path):
    from benchmark_profiles import METRICS, PROFILES
    from performance_admission import REFERENCE_CONTROL_SCOPE, SYSTEM_PROCESS_EXCLUSION_POLICY

    current = inventory(tmp_path)
    runtime = {"python": "3.11.17", "platform": "Darwin", "architecture": "arm64"}
    directory = (
        tmp_path
        / "docs/helix/04-build/evidence/web-platform/reference-performance/qualified-performance"
    )
    directory.mkdir(parents=True)
    baseline_directory = tmp_path / "docs/helix/04-build/evidence/web-platform/performance"
    baseline_directory.mkdir()
    baseline_hashes = {}
    baselines = {}
    for profile, spec in PROFILES.items():
        name = profile + "-baseline.json"
        value = {
            "profile": spec.profile,
            "receipt_sha256": hashlib.sha256((profile + "-measured").encode()).hexdigest(),
            "identity": {"versions": runtime},
            "metrics": {metric: {"observed": 1.0, "maximum": 1.2} for metric in METRICS[profile]},
            "tolerance": 1.2,
        }
        payload = json.dumps(value, sort_keys=True).encode()
        (baseline_directory / name).write_bytes(payload)
        baseline_hashes[name] = hashlib.sha256(payload).hexdigest()
        baselines[profile] = value

    def observation(phase):
        return {
            "observer": "process-table-v1",
            "phase": phase,
            "state": "quiet",
            "observed_at_utc": "2026-11-02T00:00:00+00:00",
            "evidence": "same_uid_external_count=0;other_uid_external_count=0",
            "measurement_overhead_ms": 0.0,
        }

    admission = {
        "control_scope": REFERENCE_CONTROL_SCOPE,
        "binding_sha256": "a" * 64,
        "observations": [observation("before"), observation("through"), observation("after")],
        "observer_control": {
            "sampling_interval_seconds": 1.0,
            "measured_observer_overhead_ms": 0.0,
            "maximum_subprocess_call_seconds": 5.0,
            "limits": [
                "worker",
                "bounded writes",
                "co-tenancy unknown",
                SYSTEM_PROCESS_EXCLUSION_POLICY,
            ],
        },
        "source_sha256": current,
        "baseline_sha256": baseline_hashes,
        "status": "admitted",
        "scope": "controlled-local-reference-performance",
        "qualified_profiles": list(PROFILES),
        "runtime_versions": runtime,
    }
    admission_payload = (json.dumps(admission, sort_keys=True, indent=2) + "\n").encode()
    (directory / "admission.json").write_bytes(admission_payload)
    profile = "store"
    spec = PROFILES[profile]
    baseline = baselines[profile]
    metrics = {name: 1.1 for name in METRICS[profile]}
    receipt = {
        "status": "passed",
        "profile": spec.profile,
        "metrics": metrics,
        "budgets": baseline["metrics"],
        "baseline_receipt_sha256": baseline["receipt_sha256"],
        "measurement_sha256": "b" * 64,
        "schema_version": 1,
        "command": "benchmark_ratchet / compare observed profile against measured budgets",
        "recorded_at": "2026-11-02T00:00:00+00:00",
        "source_sha256": current,
        "test_results": [
            {
                "node_id": f"scripts/benchmark_ratchet.py::{spec.profile}::{metric}",
                "outcome": "passed",
            }
            for metric in sorted(METRICS[profile])
        ],
        "limits": ["Only selected metrics/workload/profile; unmeasured scopes remain unqualified"],
        "admission_path": "admission.json",
        "admission_sha256": hashlib.sha256(admission_payload).hexdigest(),
        "baseline_path": profile + "-baseline.json",
    }
    receipt_path = directory / (profile + "-ratchet.json")
    receipt_path.write_text(json.dumps(receipt))
    relative = receipt_path.relative_to(tmp_path).as_posix()
    assert len(read_receipt(tmp_path, relative, current)) == len(METRICS[profile])

    apps_path = directory / "apps-ratchet.json"
    apps_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "source_sha256": current,
                "command": "fabricated Apps metric claim",
                "recorded_at": "2026-11-02T00:00:00+00:00",
                "test_results": [
                    {
                        "node_id": (
                            "scripts/benchmark_ratchet.py::apps-public-client-read::"
                            "measurements.list_page.p99_ms"
                        ),
                        "outcome": "passed",
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="reference_ratchet_path_unqualified"):
        read_receipt(
            tmp_path,
            apps_path.relative_to(tmp_path).as_posix(),
            current,
        )

    admission["scope"] = "uncontrolled"
    (directory / "admission.json").write_text(json.dumps(admission))
    with pytest.raises(ValueError, match="reference_ratchet_admission_or_baseline_binding_invalid"):
        read_receipt(tmp_path, relative, current)


def test_performance_acceptance_union_requires_all_54_expected_metrics(tmp_path, monkeypatch):
    expected = _expected_reference_nodes()
    assert len(expected) == 54
    monkeypatch.setattr(web_closure, "_referenced_performance_nodes", lambda *_: set())
    assert _performance_union_errors(tmp_path, {}, {}, inventory(tmp_path), required=False) == []
    assert _performance_union_errors(tmp_path, {}, {}, inventory(tmp_path), required=True)
    monkeypatch.setattr(
        web_closure,
        "_referenced_performance_nodes",
        lambda *_: {next(iter(expected))},
    )
    assert _performance_union_errors(tmp_path, {}, {}, inventory(tmp_path), required=False)
    monkeypatch.setattr(
        web_closure,
        "_referenced_performance_nodes",
        lambda *_: {
            "scripts/benchmark_ratchet.py::apps-public-client-read::measurements.list_page.p99_ms"
        },
    )
    assert _performance_union_errors(tmp_path, {}, {}, inventory(tmp_path), required=False)
    monkeypatch.setattr(web_closure, "_referenced_performance_nodes", lambda *_: expected)
    assert _performance_union_errors(tmp_path, {}, {}, inventory(tmp_path), required=True) == []


def test_completed_wp36_workflow_controls_close_without_benchmark_nodes(tmp_path):
    manifest, _ = fixture_project(tmp_path)
    node = "tests/test_performance_controls.py::test_isolated_workflow_admission_controls"
    receipt = ObservedEvidence(manifest["source_sha256"])
    receipt.inventory = [node]
    receipt.inventory_complete = True
    receipt.record_group([node], "passed", [{"node_id": node, "outcome": "passed"}], 0)
    receipt.coverage_returncode = 0
    path = "docs/helix/04-build/evidence/web-platform/workflow-controls.json"
    receipt.write(tmp_path, 0, tmp_path / path)
    proof = manifest["tasks"]["WP-36"]
    proof["implementation"] = ["scripts/performance_admission.py"]
    proof["tests"] = [node]
    proof["receipts"] = [path]
    (tmp_path / MANIFEST).write_text(json.dumps(manifest))
    assert audit(tmp_path)["errors"] == []


def test_completed_wp36_partial_qualified_performance_claim_still_refuses(tmp_path):
    manifest, _ = fixture_project(tmp_path)
    proof = manifest["tasks"]["WP-36"]
    receipt_path = tmp_path / proof["receipts"][0]
    receipt = json.loads(receipt_path.read_text())
    nodes = [item["node_id"] for item in receipt["test_results"]]
    proof["tests"] = nodes
    proof["receipts"] = [receipt_path.relative_to(tmp_path).as_posix()]
    (tmp_path / MANIFEST).write_text(json.dumps(manifest))
    assert (
        "qualified performance receipts must cover the exact admitted metric set"
        in audit(tmp_path)["errors"]
    )


def test_unchecked_wp36_with_zero_performance_nodes_cannot_close_runtime_gate(tmp_path):
    manifest, _ = fixture_project(tmp_path)
    proof = manifest["tasks"]["WP-36"]
    proof["tests"] = ["tests/test_fabricated.py::test_fabricated"]
    proof["receipts"] = ["docs/helix/04-build/evidence/web-platform/fabricated.json"]
    (tmp_path / MANIFEST).write_text(json.dumps(manifest))
    plan = tmp_path / PLAN
    plan.write_text(plan.read_text().replace("[x] **WP-36", "[ ] **WP-36"))
    assert "work task remains unchecked:WP-36" in audit(tmp_path)["errors"]


def test_task_graph_detects_cycles_and_expands_declared_ranges():
    plan = "\n".join(f"- [ ] **WP-{n:02} — Fabricated.** Depends: none." for n in range(45))
    plan = plan.replace(
        "WP-39 — Fabricated.** Depends: none", "WP-39 — Fabricated.** Depends: WP-30 through WP-38"
    )
    tasks, errors = task_inventory(plan)
    assert errors == []
    assert tasks["WP-39"]["dependencies"] == [f"WP-{n:02}" for n in range(30, 39)]
    cyclic = plan.replace(
        "WP-30 — Fabricated.** Depends: none", "WP-30 — Fabricated.** Depends: WP-39"
    )
    assert any("cyclic" in error for error in task_inventory(cyclic)[1])


def test_duplicate_or_extra_acceptance_mapping_refuses(tmp_path):
    fixture_project(tmp_path)
    path = tmp_path / STP
    path.write_text(path.read_text() + "\n| US-065-AC1 | Duplicate |")
    assert "STP acceptance mapping is missing, duplicate or extra" in audit(tmp_path)["errors"]


def test_uncited_observed_test_cannot_close_a_story_criterion(tmp_path):
    manifest, _ = fixture_project(tmp_path)
    test = tmp_path / "tests/test_fabricated.py"
    test.write_text("def test_fabricated(): pass\n")
    case = manifest["cases"]["US-065-AC1"]
    errors = case_errors(tmp_path, "US-065-AC1", case, "WP-08", inventory(tmp_path))
    assert any("@covers citation absent" in error for error in errors)


def test_untracked_proving_source_cannot_close_a_story_criterion(tmp_path):
    manifest, _ = fixture_project(tmp_path)
    excluded = tmp_path / "docs/helix/04-build/evidence/untracked-proof.py"
    excluded.parent.mkdir(parents=True, exist_ok=True)
    excluded.write_text("# @covers US-065-AC1\ndef test_fabricated(): pass\n")
    case = manifest["cases"]["US-065-AC1"]
    case["tests"] = ["docs/helix/04-build/evidence/untracked-proof.py::test_fabricated"]
    errors = case_errors(tmp_path, "US-065-AC1", case, "WP-08", inventory(tmp_path))
    assert any("proving test absent from tested source identity" in error for error in errors)


@pytest.mark.parametrize("case", ["authority", "requirement", "task", "criteria"])
def test_missing_governing_requirement_edge_refuses_structural_closure(tmp_path, case):
    fixture_project(tmp_path)
    path = tmp_path / (PRD if case == "authority" else PLAN)
    replacements = {
        "authority": ("**NFR-9**", "**NFR-99**"),
        "requirement": ("| NFR-9 |", "| NFR-99 |"),
        "task": ("All five story sets | WP-01/WP-39", "All five story sets | WP-99"),
        "criteria": ("All five story sets", "No mapped story criteria"),
    }
    before, after = replacements[case]
    path.write_text(path.read_text().replace(before, after))
    result = audit(tmp_path, inventory_only=True)
    assert result["requirement_count"] == 7
    assert result["errors"]
