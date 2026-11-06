"""Shared scalar summaries for explicitly scoped reference measurements."""

from __future__ import annotations

import importlib
import math
import platform
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict


class Distribution(TypedDict):
    sample_count: int
    p99_ms: float
    median_ms: float


class RuntimeVersions(TypedDict):
    python: str
    platform: str
    architecture: str


def distribution(values_ms: Sequence[float]) -> Distribution:
    """Summarize actual nonnegative observations using the nearest-rank p99."""
    if not values_ms or any(
        isinstance(value, bool) or not math.isfinite(value) or value < 0 for value in values_ms
    ):
        raise ValueError("benchmark_observations_invalid")
    ordered = sorted(values_ms)
    return {
        "sample_count": len(ordered),
        "p99_ms": float(ordered[math.ceil(len(ordered) * 0.99) - 1]),
        "median_ms": float(statistics.median(ordered)),
    }


def runtime_versions() -> RuntimeVersions:
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
    }


def runtime_import_proof(
    project_root: Path, expected_module_paths: Mapping[str, str]
) -> dict[str, str]:
    """Require the measured modules to come from their exact checkout paths."""
    root = project_root.resolve()
    if not expected_module_paths:
        raise ValueError("benchmark_runtime_imports_required")
    proof: dict[str, str] = {}
    for name, relative in expected_module_paths.items():
        expected = (root / relative).resolve()
        if Path(relative).is_absolute() or not expected.is_relative_to(root):
            raise ValueError("benchmark_runtime_expected_path_invalid")
        source = getattr(importlib.import_module(name), "__file__", None)
        if not isinstance(source, str) or Path(source).resolve() != expected:
            raise RuntimeError("benchmark_runtime_import_path_mismatch")
        proof[name] = str(expected.relative_to(root))
    return proof
