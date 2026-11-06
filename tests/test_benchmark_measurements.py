from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import benchmark_measurements
import pytest
from benchmark_measurements import distribution, runtime_import_proof


def test_distribution_uses_actual_samples_and_nearest_rank_without_mutation() -> None:
    values = list(reversed(range(100)))
    assert distribution(values) == {"sample_count": 100, "p99_ms": 98.0, "median_ms": 49.5}
    assert values == list(reversed(range(100)))


@pytest.mark.parametrize(
    "values",
    [[], [-1.0], [float("nan")], [float("inf")], [True]],
    ids=["empty", "negative", "nan", "infinite", "boolean"],
)
def test_distribution_refuses_invalid_observations(values: list[float]) -> None:
    with pytest.raises(ValueError, match=r"^benchmark_observations_invalid$"):
        distribution(values)


def test_runtime_import_proof_requires_exact_source_not_project_virtualenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = {"fabricated": "src/fabricated.py"}
    monkeypatch.setattr(
        benchmark_measurements.importlib,
        "import_module",
        lambda name: SimpleNamespace(__file__=str(tmp_path / "src/fabricated.py")),
    )
    assert runtime_import_proof(tmp_path, expected) == expected
    monkeypatch.setattr(
        benchmark_measurements.importlib,
        "import_module",
        lambda name: SimpleNamespace(__file__=str(tmp_path / ".venv/lib/fabricated.py")),
    )
    with pytest.raises(RuntimeError, match="benchmark_runtime_import_path_mismatch"):
        runtime_import_proof(tmp_path, expected)


def test_runtime_import_proof_refuses_expected_path_outside_checkout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="benchmark_runtime_expected_path_invalid"):
        runtime_import_proof(tmp_path, {"fabricated": "../fabricated.py"})
