"""Validate complete workload identities and historical performance budgets."""

from __future__ import annotations

import json
import platform
import re
from pathlib import Path

from benchmark_profiles import (
    DYNAMIC_IDENTITIES,
    EXTERNAL_PROFILES,
    IDENTITIES,
    MEMORY_ALLOCATIONS,
    METRICS,
    PROFILES,
)
from benchmark_ratchet import Baseline, number, select


def runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
    }


def _same_identity(actual: object, expected: object) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(_same_identity(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                _same_identity(left, right) for left, right in zip(actual, expected, strict=True)
            )
        )
    return actual == expected


def baselines(directory: Path) -> dict[str, Baseline]:
    """Refuse absent, incomplete or smaller-workload budgets before seeding."""
    result: dict[str, Baseline] = {}
    for name in PROFILES:
        raw = decode_json_object((directory / f"{name}-baseline.json").read_bytes())
        value = _decode_baseline(raw, name)
        validate_budget(name, value)
        result[name] = value
    return result


def decode_json_object(payload: bytes) -> dict[str, object]:
    """Decode script-owned JSON only when the top-level object is typed."""
    value: object = json.loads(payload)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("json_object_required")
    result: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            result[key] = item
    return result


def _decode_baseline(value: object, name: str) -> Baseline:
    if not isinstance(value, dict):
        raise ValueError("reference_baseline_object_required:" + name)
    profile = value.get("profile")
    receipt = value.get("receipt_sha256")
    identity_value = value.get("identity")
    metrics_value = value.get("metrics")
    tolerance_value = value.get("tolerance")
    if (
        not isinstance(profile, str)
        or not isinstance(receipt, str)
        or not isinstance(identity_value, dict)
        or not isinstance(metrics_value, dict)
    ):
        raise ValueError("reference_baseline_fields_invalid:" + name)
    identity: dict[str, object] = {}
    for key, item in identity_value.items():
        if not isinstance(key, str):
            raise ValueError("reference_baseline_identity_invalid:" + name)
        identity[key] = item
    metrics = {}
    for key, item in metrics_value.items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise ValueError("reference_baseline_metrics_invalid:" + name)
        metrics[key] = {
            "observed": number(item.get("observed"), key + ".observed"),
            "maximum": number(item.get("maximum"), key + ".maximum"),
        }
    tolerance = number(tolerance_value, "tolerance")
    if tolerance < 1:
        raise ValueError("reference_baseline_tolerance_invalid:" + name)
    return {
        "profile": profile,
        "receipt_sha256": receipt,
        "identity": identity,
        "metrics": metrics,
        "tolerance": tolerance,
    }


def _profile_name(name: str) -> str:
    if name in PROFILES:
        return PROFILES[name].profile
    if name in EXTERNAL_PROFILES:
        return EXTERNAL_PROFILES[name]
    raise ValueError("registered_reference_profile_required")


def validate_measurement(name: str, observed: dict[str, object]) -> None:
    """Check full workload identity and scalar completeness, not a speed limit."""
    profile_name = _profile_name(name)
    if not IDENTITIES[name] or not METRICS[name]:
        raise ValueError("reference_measurement_contract_incomplete:" + name)
    if observed.get("profile") != profile_name:
        raise ValueError("reference_measurement_profile_required:" + name)
    if observed.get("versions") != runtime_versions():
        raise ValueError("matching_reference_runtime_required:" + name)
    for path, expected in IDENTITIES[name].items():
        if not _same_identity(select(observed, path), expected):
            raise ValueError("full_reference_measurement_required:" + name + ":" + path)
    for path in METRICS[name]:
        number(select(observed, path), path)
    if name == "engine":
        _validate_engine_counts(observed)
    if name == "apps":
        _validate_apps_identity({path: select(observed, path) for path in DYNAMIC_IDENTITIES[name]})
        _validate_apps_bindings(observed)
    if name == "follower-memory":
        _validate_memory_measurement(observed)


def _byte_summary(observed: dict[str, object], path: str) -> tuple[float, float]:
    summary = select(observed, path)
    if not isinstance(summary, dict) or type(summary.get("sample_count")) is not int:
        raise ValueError("memory_byte_summary_required:" + path)
    if summary["sample_count"] != 100:
        raise ValueError("memory_full_trial_count_required:" + path)
    values = [
        number(summary.get(key), path + "." + key)
        for key in ("min_bytes", "median_bytes", "p99_bytes", "max_bytes")
    ]
    if values != sorted(values):
        raise ValueError("memory_byte_summary_order_required:" + path)
    return values[0], values[-1]


def _validate_memory_measurement(observed: dict[str, object]) -> None:
    for key in MEMORY_ALLOCATIONS:
        _byte_summary(observed, "measurements." + key)
    for key, lower, upper in (
        ("live_buffer_max_per_sample", 760, 1200),
        ("coalesced_event_per_sample", 32767, 34000),
    ):
        minimum, maximum = _byte_summary(
            observed, "measurements.serialized_event_payload_bytes." + key
        )
        if minimum < lower or maximum > upper:
            raise ValueError("memory_serialized_payload_profile_required:" + key)
    before = number(
        select(
            observed, "measurements.aggregate_1000_followers.whole_process_peak_rss_before_bytes"
        ),
        "rss_before",
    )
    after = number(
        select(
            observed, "measurements.aggregate_1000_followers.whole_process_peak_rss_after_bytes"
        ),
        "rss_after",
    )
    if after < before:
        raise ValueError("memory_process_peak_monotonicity_required")


def _validate_engine_counts(observed: dict[str, object]) -> None:
    counts = select(observed, "observed_boundary_calls")
    if not isinstance(counts, dict):
        raise ValueError("engine_boundary_counts_required")
    fields = (
        "model_bodies",
        "attempt_callbacks",
        "storage_bodies",
        "model_stream",
        "attempt_reserve",
        "attempt_start",
        "attempt_observe",
        "history_put",
        "history_read",
        "turnlog_entries",
        "turnlog_append",
    )
    for field in fields:
        value = counts.get(field)
        if type(value) is not int or value <= 0:
            raise ValueError("engine_boundary_counts_required:" + field)


def _validate_apps_bindings(observed: dict[str, object]) -> None:
    for field in ("hardware_client_sha256", "immutable_bundle_sha256", "deployment_binding_sha256"):
        value = observed.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("apps_measurement_binding_required:" + field)


def _validate_apps_identity(identity: dict[str, object]) -> None:
    cardinality = identity.get("workload.list_items_per_page")
    versions = identity.get("http_versions")
    if type(cardinality) is not int or not 0 <= cardinality <= 50:
        raise ValueError("apps_list_cardinality_required")
    if (
        not isinstance(versions, list)
        or not versions
        or any(
            not isinstance(version, str) or version not in {"HTTP/1.1", "HTTP/2"}
            for version in versions
        )
        or versions != sorted(set(versions))
    ):
        raise ValueError("apps_observed_http_versions_required")


def validate_budget(name: str, value: Baseline) -> None:
    if value.get("profile") != _profile_name(name) or not value.get("receipt_sha256"):
        raise ValueError("measured_reference_baseline_required:" + name)
    identity = value.get("identity", {})
    if not identity.get("versions") or not value.get("metrics"):
        raise ValueError("reference_versions_and_metrics_required:" + name)
    if not METRICS[name] <= value["metrics"].keys():
        raise ValueError("all_selected_reference_metrics_required:" + name)
    if identity["versions"] != runtime_versions():
        raise ValueError("matching_reference_runtime_required:" + name)
    for path, budget in value["metrics"].items():
        if number(budget["maximum"], path) < number(budget["observed"], path):
            raise ValueError("budget_below_measured_baseline:" + name + ":" + path)
    for path, expected in IDENTITIES[name].items():
        if not _same_identity(identity.get(path), expected):
            raise ValueError("full_reference_workload_baseline_required:" + name + ":" + path)
    if name == "apps":
        _validate_apps_identity(identity)


__all__ = [
    "baselines",
    "decode_json_object",
    "runtime_versions",
    "validate_budget",
    "validate_measurement",
]
