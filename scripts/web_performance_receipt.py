"""Strict read-side joins for controlled local performance evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import TypeGuard


def verify_reference_ratchet(
    root: Path, path: Path, receipt: dict, current: dict[str, str]
) -> None:
    from benchmark_profiles import METRICS, PROFILES
    from performance_admission import REFERENCE_CONTROL_SCOPE, SYSTEM_PROCESS_EXCLUSION_POLICY

    profile = _reference_profile(root, path, PROFILES)
    admission, baseline = _load_reference_bindings(root, path, receipt, current, profile, PROFILES)
    _validate_reference_admission(admission, current, PROFILES, REFERENCE_CONTROL_SCOPE)
    _validate_reference_baseline(
        baseline, profile, PROFILES[profile].profile, METRICS[profile], admission
    )
    _validate_ratchet_payload(
        receipt, baseline, profile, PROFILES[profile].profile, METRICS[profile]
    )
    _validate_admission_observations(admission, SYSTEM_PROCESS_EXCLUSION_POLICY)


def _reference_profile(root: Path, path: Path, profiles: dict) -> str:
    prefix = Path(
        "docs/helix/04-build/evidence/web-platform/reference-performance/qualified-performance"
    )
    profile = path.name.removesuffix("-ratchet.json")
    if path.relative_to(root.resolve()).parent != prefix or profile not in profiles:
        raise ValueError("reference_ratchet_path_unqualified")
    return profile


def _load_reference_bindings(
    root: Path, path: Path, receipt: dict, current: dict, profile: str, profiles: dict
):
    directory = path.parent
    admission_path = directory / "admission.json"
    baseline_name = profile + "-baseline.json"
    baseline_path = root / "docs/helix/04-build/evidence/web-platform/performance" / baseline_name
    if not admission_path.is_file() or admission_path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("reference_admission_missing_or_unbounded")
    admission_bytes = admission_path.read_bytes()
    if (
        not baseline_path.is_file()
        or baseline_path.stat().st_size > 2 * 1024 * 1024
        or receipt.get("admission_path") != "admission.json"
        or receipt.get("admission_sha256") != hashlib.sha256(admission_bytes).hexdigest()
        or receipt.get("baseline_path") != baseline_name
    ):
        raise ValueError("reference_ratchet_admission_or_baseline_binding_invalid")
    admission = json.loads(admission_bytes)
    baseline = json.loads(baseline_path.read_bytes())
    if not isinstance(admission, dict) or not isinstance(baseline, dict):
        raise ValueError("reference_admission_or_baseline_object_required")
    baseline_hashes = admission.get("baseline_sha256")
    if (
        not isinstance(baseline_hashes, dict)
        or set(baseline_hashes) != {name + "-baseline.json" for name in profiles}
        or admission.get("source_sha256") != current
        or receipt.get("source_sha256") != current
    ):
        raise ValueError("reference_admission_scope_or_source_invalid")
    for name in profiles:
        candidate = baseline_path.parent / f"{name}-baseline.json"
        if (
            not candidate.is_file()
            or candidate.stat().st_size > 2 * 1024 * 1024
            or not isinstance(baseline_hashes.get(candidate.name), str)
            or hashlib.sha256(candidate.read_bytes()).hexdigest() != baseline_hashes[candidate.name]
        ):
            raise ValueError("reference_baseline_admission_hash_mismatch:" + name)
    return admission, baseline


def _validate_reference_admission(
    admission: dict, current: dict, profiles: dict, control_scope: str
) -> None:
    expected = {
        "control_scope",
        "binding_sha256",
        "observations",
        "observer_control",
        "source_sha256",
        "baseline_sha256",
        "status",
        "scope",
        "qualified_profiles",
        "runtime_versions",
    }
    binding = admission.get("binding_sha256")
    runtime = admission.get("runtime_versions")
    if (
        set(admission) != expected
        or admission.get("status") != "admitted"
        or admission.get("scope") != "controlled-local-reference-performance"
        or admission.get("control_scope") != control_scope
        or admission.get("qualified_profiles") != list(profiles)
        or admission.get("source_sha256") != current
        or not isinstance(binding, str)
        or re.fullmatch(r"[0-9a-f]{64}", binding) is None
        or not isinstance(runtime, dict)
        or set(runtime) != {"python", "platform", "architecture"}
        or any(not isinstance(item, str) or not item for item in runtime.values())
    ):
        raise ValueError("reference_admission_scope_or_source_invalid")


def _validate_reference_baseline(
    baseline: dict, profile: str, profile_name: str, metric_names: set[str], admission: dict
) -> None:
    metrics = baseline.get("metrics")
    runtime = admission["runtime_versions"]
    tolerance = baseline.get("tolerance")
    if (
        set(baseline) != {"profile", "receipt_sha256", "identity", "metrics", "tolerance"}
        or baseline.get("profile") != profile_name
        or not isinstance(baseline.get("receipt_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", baseline["receipt_sha256"]) is None
        or not isinstance(baseline.get("identity"), dict)
        or baseline["identity"].get("versions") != runtime
        or not isinstance(metrics, dict)
        or set(metrics) != metric_names
        or not _nonnegative_number(tolerance)
        or tolerance < 1
    ):
        raise ValueError("reference_baseline_contract_invalid:" + profile)


def _validate_ratchet_payload(
    receipt: dict, baseline: dict, profile: str, profile_name: str, metric_names: set[str]
) -> None:
    expected_fields = {
        "status",
        "profile",
        "metrics",
        "budgets",
        "baseline_receipt_sha256",
        "measurement_sha256",
        "schema_version",
        "command",
        "recorded_at",
        "source_sha256",
        "test_results",
        "limits",
        "admission_path",
        "admission_sha256",
        "baseline_path",
    }
    metrics = receipt.get("metrics")
    budgets = receipt.get("budgets")
    expected_nodes = [
        {"node_id": f"scripts/benchmark_ratchet.py::{profile_name}::{name}", "outcome": "passed"}
        for name in sorted(metric_names)
    ]
    if (
        set(receipt) != expected_fields
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "passed"
        or receipt.get("profile") != profile_name
        or receipt.get("command")
        != "benchmark_ratchet / compare observed profile against measured budgets"
        or not isinstance(receipt.get("recorded_at"), str)
        or not receipt.get("recorded_at")
        or receipt.get("limits")
        != ["Only selected metrics/workload/profile; unmeasured scopes remain unqualified"]
        or not isinstance(metrics, dict)
        or set(metrics) != metric_names
        or not isinstance(budgets, dict)
        or budgets != baseline["metrics"]
        or receipt.get("baseline_receipt_sha256") != baseline["receipt_sha256"]
        or not isinstance(receipt.get("measurement_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["measurement_sha256"]) is None
        or receipt.get("test_results") != expected_nodes
    ):
        raise ValueError("reference_ratchet_contract_invalid:" + profile)
    for name, observed in metrics.items():
        budget = baseline["metrics"][name]
        if (
            not isinstance(budget, dict)
            or set(budget) != {"observed", "maximum"}
            or any(not _nonnegative_number(budget.get(key)) for key in ("observed", "maximum"))
            or budget["maximum"] < budget["observed"]
            or not _nonnegative_number(observed)
            or observed > budget["maximum"]
        ):
            raise ValueError("reference_ratchet_metric_invalid:" + name)


def _nonnegative_number(value: object) -> TypeGuard[int | float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return math.isfinite(value) and value >= 0


def _validate_admission_observations(admission: dict, exclusion_policy: str) -> None:
    observations = admission.get("observations")
    if not isinstance(observations, list) or len(observations) < 3:
        raise ValueError("reference_admission_observations_incomplete")
    phases = []
    overhead = 0.0
    expected_fields = {
        "observer",
        "phase",
        "state",
        "observed_at_utc",
        "evidence",
        "measurement_overhead_ms",
    }
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != expected_fields:
            raise ValueError("reference_admission_observation_invalid")
        elapsed = observation.get("measurement_overhead_ms")
        if (
            observation.get("observer") != "process-table-v1"
            or observation.get("phase") not in {"before", "through", "after"}
            or observation.get("state") != "quiet"
            or not isinstance(observation.get("observed_at_utc"), str)
            or not observation.get("observed_at_utc")
            or not isinstance(observation.get("evidence"), str)
            or not observation.get("evidence")
            or not _nonnegative_number(elapsed)
        ):
            raise ValueError("reference_admission_observation_invalid")
        phases.append(observation["phase"])
        overhead += elapsed
    observer = admission.get("observer_control")
    if not isinstance(observer, dict) or set(observer) != {
        "sampling_interval_seconds",
        "measured_observer_overhead_ms",
        "maximum_subprocess_call_seconds",
        "limits",
    }:
        raise ValueError("reference_admission_observer_control_invalid")
    if (
        not _nonnegative_number(observer.get("sampling_interval_seconds"))
        or observer["sampling_interval_seconds"] == 0
        or observer.get("measured_observer_overhead_ms") != overhead
        or observer.get("maximum_subprocess_call_seconds") != 5.0
        or not isinstance(observer.get("limits"), list)
        or len(observer["limits"]) != 4
        or observer["limits"][-1] != exclusion_policy
        or phases[0] != "before"
        or phases[-1] != "after"
        or "through" not in phases[1:-1]
    ):
        raise ValueError("reference_admission_observer_control_invalid")
