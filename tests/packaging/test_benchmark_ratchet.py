"""Budgets are measured, profile-bound and cannot accept absent/invalid evidence."""

import copy
import hashlib
import json

import pytest
from benchmark_ratchet import capture, compare, independent_result


def measurement():
    return {
        "profile": "fabricated-test-profile",
        "workload": {"samples": 100, "followers": 1000},
        "versions": {"python": "fabricated"},
        "results": {"latency_ms": 10},
    }


def baseline():
    return capture(
        measurement(),
        "fabricated-receipt-hash",
        ["results.latency_ms"],
        ["workload", "versions"],
        1.2,
    )


def test_baseline_retains_observation_and_derived_budget():
    values = baseline()
    assert values["metrics"]["results.latency_ms"] == {"observed": 10, "maximum": 12}
    assert compare(measurement(), values)["status"] == "passed"
    current = measurement()
    current["results"]["latency_ms"] = 12.1
    with pytest.raises(ValueError, match="budget_exceeded"):
        compare(current, values)


def test_first_measurement_cannot_be_its_own_independent_regression_proof():
    original = json.dumps(measurement()).encode()
    values = baseline()
    values["receipt_sha256"] = hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError, match="independent_measurement_required"):
        independent_result(original, values)
    current = measurement()
    current["results"]["latency_ms"] = 11
    result = independent_result(json.dumps(current).encode(), values)
    assert result["baseline_receipt_sha256"] != result["measurement_sha256"]
    metrics = result["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["results.latency_ms"] == 11


@pytest.mark.parametrize("path", ["workload", "versions"])
def test_different_workload_or_version_cannot_borrow_profile_budget(path):
    current = copy.deepcopy(measurement())
    current[path] = {}
    with pytest.raises(ValueError, match="workload_or_version_changed"):
        compare(current, baseline())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "10"])
def test_invalid_measurement_refused(value):
    current = measurement()
    current["results"]["latency_ms"] = value
    with pytest.raises(ValueError, match="metric_not"):
        compare(current, baseline())


def test_missing_metric_and_missing_measured_baseline_refuse():
    current = measurement()
    del current["results"]
    with pytest.raises(ValueError, match="missing_measurement"):
        compare(current, baseline())
    empty = baseline()
    empty["metrics"] = {}
    with pytest.raises(ValueError, match="measured_baseline_required"):
        compare(measurement(), empty)


def test_qualified_metric_receipt_binds_full_current_sources(tmp_path):
    from benchmark_ratchet import qualify
    from web_artifacts import inventory

    current = measurement()
    with pytest.raises(ValueError, match="source_changed_or_incomplete"):
        qualify(current, compare(current, baseline()), tmp_path)
    current["source_sha256"] = inventory(tmp_path)
    result = qualify(current, compare(current, baseline()), tmp_path)
    assert result["status"] == "passed"
    assert result["test_results"] == [
        {
            "node_id": "scripts/benchmark_ratchet.py::fabricated-test-profile::results.latency_ms",
            "outcome": "passed",
        }
    ]
