"""Observed evidence cannot confuse inventory, skips, duplicates or stale sources."""

import json
from copy import deepcopy

import pytest
from test_evidence import ObservedEvidence, junit_results, validate_python_receipt
from web_artifacts import inventory


def report(tmp_path, cases):
    path = tmp_path / "observed.xml"
    path.write_text("<testsuites><testsuite>" + cases + "</testsuite></testsuites>")
    return path


def test_observed_pass_uses_exact_collected_parametrized_identity(tmp_path):
    path = report(
        tmp_path, '<testcase classname="tests.test_example.Cases" name="test_one[fabricated]"/>'
    )
    node = "tests/test_example.py::Cases::test_one[fabricated]"
    assert junit_results(path, [node]) == [{"node_id": node, "outcome": "passed"}]


@pytest.mark.parametrize(
    ("tag", "outcome"), [("failure", "failed"), ("error", "failed"), ("skipped", "skipped")]
)
def test_nonpassing_observation_never_counts_as_pass(tmp_path, tag, outcome):
    path = report(
        tmp_path, f'<testcase classname="tests.test_example" name="test_one"><{tag}/></testcase>'
    )
    assert junit_results(path, ["tests/test_example.py::test_one"])[0]["outcome"] == outcome


@pytest.mark.parametrize(
    "cases",
    [
        "",
        '<testcase classname="other" name="test_one"/>',
        '<testcase classname="tests.test_example" name="test_one"/>' * 2,
    ],
)
def test_missing_unknown_duplicate_observations_refuse(tmp_path, cases):
    with pytest.raises(
        ValueError, match=r"required_test_outcomes_missing|unknown_or_duplicate_observed_test"
    ):
        junit_results(report(tmp_path, cases), ["tests/test_example.py::test_one"])


def test_missing_report_and_xml_entity_input_refuse(tmp_path):
    with pytest.raises(ValueError, match="missing_observed_test_report"):
        junit_results(tmp_path / "missing", ["tests/test_example.py::test_one"])
    path = tmp_path / "attack.xml"
    path.write_text(
        '<!DOCTYPE x [<!ENTITY expansion "fabricated">]><testsuite>&expansion;</testsuite>'
    )
    from defusedxml.common import EntitiesForbidden

    with pytest.raises(EntitiesForbidden):
        junit_results(path, ["tests/test_example.py::test_one"])


def test_receipt_refuses_source_drift_despite_zero_exit(tmp_path):
    (tmp_path / "pyproject.toml").write_text("first")
    evidence = ObservedEvidence(inventory(tmp_path))
    (tmp_path / "pyproject.toml").write_text("changed")
    path = evidence.write(tmp_path, 0)
    try:
        data = json.loads(path.read_text())
        assert data["status"] == "incomplete"
        assert data["source_stable"] is False
    finally:
        path.unlink()


def test_python_receipt_rejects_boolean_codes_extra_fields_and_oversized_groups(tmp_path):
    source = inventory(tmp_path)
    node = "tests/test_fake.py::test_complete"
    evidence = ObservedEvidence(source)
    evidence.inventory = [node]
    evidence.inventory_complete = True
    evidence.record_group([node], "passed", [{"node_id": node, "outcome": "passed"}], 0)
    evidence.coverage_returncode = 0
    receipt = json.loads(evidence.write(tmp_path, 0, tmp_path / "python-evidence.json").read_text())
    validate_python_receipt(receipt, source)

    mutations = []
    extra = deepcopy(receipt)
    extra["incomplete_reason"] = "watchdog_exhausted"
    mutations.append(extra)
    extra_version = deepcopy(receipt)
    extra_version["schema_version"] = True
    mutations.append(extra_version)
    changed_profile = deepcopy(receipt)
    changed_profile["profile"] = "unqualified"
    mutations.append(changed_profile)
    for path, value in (
        (("exit_code",), False),
        (("coverage_returncode",), False),
        (("groups", 0, "returncode"), False),
        (("groups", 0, "index"), True),
    ):
        changed = deepcopy(receipt)
        target = changed
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        mutations.append(changed)
    oversized = deepcopy(receipt)
    oversized_nodes = [f"tests/test_fake.py::test_{index}" for index in range(33)]
    oversized["inventory"] = oversized_nodes
    oversized["groups"] = [
        {
            "index": 0,
            "status": "passed",
            "node_ids": oversized_nodes,
            "outcome_count": 33,
            "returncode": 0,
        }
    ]
    oversized["test_results"] = [
        {"node_id": value, "outcome": "passed"} for value in oversized_nodes
    ]
    mutations.append(oversized)
    for changed in mutations:
        with pytest.raises(ValueError, match="python_evidence_"):
            validate_python_receipt(changed, source)

    duplicate_group_node = deepcopy(receipt)
    duplicate_group_node["groups"][0]["node_ids"].append(node)
    duplicate_group_node["groups"][0]["outcome_count"] = 2
    duplicate_group_node["inventory"] = [node, node]
    duplicate_group_node["test_results"].append({"node_id": node, "outcome": "passed"})
    with pytest.raises(ValueError, match="python_evidence_"):
        validate_python_receipt(duplicate_group_node, source)
