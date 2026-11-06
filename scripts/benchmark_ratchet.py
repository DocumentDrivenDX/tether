"""Compare explicit profile measurements to budgets derived from a real baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from web_artifacts import inventory, write_receipt


class Budget(TypedDict):
    observed: float
    maximum: float


class Baseline(TypedDict):
    profile: str
    receipt_sha256: str
    identity: dict[str, object]
    metrics: dict[str, Budget]
    tolerance: float


def select(receipt: dict[str, object], path: str) -> object:
    value: object = receipt
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError("missing_measurement:" + path)
        value = value[part]
    return value


def number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("metric_not_numeric:" + path)
    if not math.isfinite(value) or value < 0:
        raise ValueError("metric_not_finite_nonnegative:" + path)
    return float(value)


def capture(
    receipt: dict[str, object],
    digest: str,
    metrics: list[str],
    identities: list[str],
    tolerance: float,
) -> Baseline:
    profile = receipt.get("profile")
    if not isinstance(profile, str) or not profile or not metrics or not identities:
        raise ValueError("profile_metrics_and_workload_identity_required")
    if not math.isfinite(tolerance) or tolerance < 1:
        raise ValueError("finite_tolerance_at_least_one_required")
    values: dict[str, Budget] = {}
    for path in metrics:
        observed = number(select(receipt, path), path)
        maximum = observed * tolerance
        if not math.isfinite(maximum):
            raise ValueError("budget_overflow:" + path)
        values[path] = {"observed": observed, "maximum": maximum}
    return {
        "profile": profile,
        "receipt_sha256": digest,
        "identity": {path: select(receipt, path) for path in identities},
        "metrics": values,
        "tolerance": tolerance,
    }


def compare(receipt: dict[str, object], baseline: Baseline) -> dict[str, object]:
    if receipt.get("profile") != baseline["profile"]:
        raise ValueError("benchmark_profile_changed")
    if not baseline["metrics"] or not baseline["identity"] or not baseline["receipt_sha256"]:
        raise ValueError("measured_baseline_required")
    for path, expected in baseline["identity"].items():
        if select(receipt, path) != expected:
            raise ValueError("benchmark_workload_or_version_changed:" + path)
    observed: dict[str, float] = {}
    failures = []
    for path, budget in baseline["metrics"].items():
        value = number(select(receipt, path), path)
        maximum = number(budget["maximum"], path)
        measured = number(budget["observed"], path)
        if maximum < measured:
            raise ValueError("budget_below_recorded_baseline:" + path)
        observed[path] = value
        if value > maximum:
            failures.append(path)
    if failures:
        raise ValueError("benchmark_budget_exceeded:" + ",".join(failures))
    return {
        "status": "passed",
        "profile": baseline["profile"],
        "metrics": observed,
        "budgets": baseline["metrics"],
        "baseline_receipt_sha256": baseline["receipt_sha256"],
    }


def independent_result(payload: bytes, baseline: Baseline) -> dict[str, object]:
    digest = hashlib.sha256(payload).hexdigest()
    if digest == baseline["receipt_sha256"]:
        raise ValueError("independent_measurement_required_for_qualified_ratchet")
    return {**compare(json.loads(payload), baseline), "measurement_sha256": digest}


def qualify(
    measurement: dict[str, object], result: dict[str, object], root: Path
) -> dict[str, object]:
    sources = inventory(root)
    if measurement.get("source_sha256") != sources:
        raise ValueError("benchmark_measurement_source_changed_or_incomplete")
    metrics = result["metrics"]
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("qualified_benchmark_metrics_required")
    return {
        **result,
        "schema_version": 1,
        "command": "benchmark_ratchet / compare observed profile against measured budgets",
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": sources,
        "test_results": [
            {
                "node_id": f"scripts/benchmark_ratchet.py::{result['profile']}::{path}",
                "outcome": "passed",
            }
            for path in sorted(metrics)
        ],
        "limits": ["Only selected metrics/workload/profile; unmeasured scopes remain unqualified"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurement", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metric", action="append", default=[])
    parser.add_argument("--identity", action="append", default=[])
    parser.add_argument("--tolerance", type=float, default=1.2)
    arguments = parser.parse_args()
    payload = arguments.measurement.read_bytes()
    receipt = json.loads(payload)
    if arguments.record_baseline:
        if arguments.baseline.exists():
            parser.error("existing baseline requires deliberate review before replacement")
        result = capture(
            receipt,
            hashlib.sha256(payload).hexdigest(),
            arguments.metric,
            arguments.identity,
            arguments.tolerance,
        )
        arguments.baseline.write_text(json.dumps(result, indent=2) + "\n")
    else:
        baseline = json.loads(arguments.baseline.read_bytes())
        result = compare(receipt, baseline)
        if arguments.output is not None:
            result = independent_result(payload, baseline)
            result = qualify(receipt, result, Path.cwd())
            write_receipt(arguments.output, result)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
