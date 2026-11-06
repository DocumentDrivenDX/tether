"""Functional controls for the registered store reference-measurement contract."""

from copy import deepcopy

import pytest
from benchmark_profiles import IDENTITIES, METRICS, PROFILES
from web_performance import runtime_versions, validate_measurement


def _set_path(target: dict[str, object], path: str, value: object) -> None:
    parts = path.split(".")
    node = target
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise TypeError("synthetic_measurement_path_conflict")
        node = child
    node[parts[-1]] = deepcopy(value)


def _store_measurement() -> dict[str, object]:
    measurement: dict[str, object] = {
        "profile": PROFILES["store"].profile,
        "versions": runtime_versions(),
    }
    for path, value in {
        **IDENTITIES["store"],
        **dict.fromkeys(METRICS["store"], 1),
    }.items():
        _set_path(measurement, path, value)
    return measurement


def test_store_measurement_contract_accepts_complete_fabricated_observation() -> None:
    validate_measurement("store", _store_measurement())


def test_store_measurement_contract_rejects_changed_sample_identity() -> None:
    measurement = _store_measurement()
    workload = measurement["workload"]
    assert isinstance(workload, dict)
    workload["samples"] = 3

    with pytest.raises(ValueError, match="full_reference_measurement_required:store"):
        validate_measurement("store", measurement)
