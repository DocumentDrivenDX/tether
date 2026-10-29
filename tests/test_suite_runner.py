"""Every collected case is preserved; process and aggregate coverage failures stop the gate."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import test_suite
from process import run_command

ROOT = Path(__file__).resolve().parents[1]


def test_groups_preserve_case_order_and_finite_argument_budget():
    nodes = [f"tests/test_fake.py::test_case[{i}]" for i in range(70)]
    nodes += [f"tests/test_embedded_lifetime.py::test_case[{i}]" for i in range(19)]
    nodes += [f"tests/test_postgres_processes.py::test_case[{i}]" for i in range(9)]
    nodes += ["tests/test_fake.py::test_last"]
    batches = test_suite.groups(nodes)
    assert [node for batch in batches for node in batch] == nodes
    assert all(len(batch) <= 32 for batch in batches)
    assert all(
        len(batch) <= 8 and all(node.startswith(test_suite.NATIVE_CASE_PREFIXES) for node in batch)
        for batch in batches
        if batch[0].startswith(test_suite.NATIVE_CASE_PREFIXES)
    )
    assert all(sum(len(node.encode()) + 1 for node in batch) <= 64 * 1024 for batch in batches)
    assert len(test_suite.groups(["a" * 40000, "b" * 40000])) == 2


@pytest.mark.parametrize("nodes", [None, [], [""], [1], ["a", "a"], ["a" * 65536]])
def test_invalid_inventory_never_becomes_an_empty_success(nodes):
    with pytest.raises(
        ValueError, match=r"empty_test_inventory|invalid_test_inventory|test_id_too_large"
    ):
        test_suite.groups(nodes)


@pytest.mark.parametrize("failure", ["erase", "collect", "batch", "report"])
def test_each_stage_failure_prevents_a_successful_gate(monkeypatch, failure):
    stages = []

    async def fake(arguments):
        if arguments[-1] == "erase":
            stage = "erase"
        elif any(value.endswith("collect_tests.py") for value in arguments):
            stage = "collect"
            await asyncio.to_thread(
                Path(arguments[-1]).write_text, json.dumps(["tests/test_fake.py::test_case"])
            )
        elif arguments[-1] == "report":
            stage = "report"
        else:
            stage = "batch"
        stages.append(stage)
        return 3 if stage == failure else 0

    monkeypatch.setattr(test_suite, "command", fake)
    assert asyncio.run(test_suite.run_suite(sys.executable)) == 3
    assert stages == ["erase", "collect", "batch", "report"][: stages.index(failure) + 1]


@pytest.mark.parametrize("both_branches", [False, True])
def test_real_inventory_and_aggregate_branch_coverage_gate(tmp_path, both_branches, monkeypatch):
    shared_temporary_root = tmp_path / "shared-pytest-root"
    shared_temporary_root.mkdir()
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(shared_temporary_root))
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["."]\n'
        'addopts="--cov=sample --cov-branch"\n'
        '[tool.coverage.run]\nbranch=true\nsource=["sample"]\n'
        "[tool.coverage.report]\nfail_under=85\n"
    )
    (tmp_path / "sample.py").write_text(
        "def choose(flag):\n    if flag:\n        return 1\n    return 2\n"
    )
    (tmp_path / "tests").mkdir()
    case = "from sample import choose\ndef test_yes(tmp_path):\n    assert choose(True) == 1\n"
    if both_branches:
        case += "def test_no():\n    assert choose(False) == 2\n"
    (tmp_path / "tests/test_sample.py").write_text(case)
    result = asyncio.run(
        run_command([sys.executable, str(ROOT / "scripts/test_suite.py")], cwd=tmp_path)
    )
    assert result.returncode == (0 if both_branches else 2)
    assert not list(shared_temporary_root.iterdir())
    assert "bounded groups" in result.stdout
    if both_branches:
        assert "100%" in result.stdout
    else:
        assert "fail-under=85" in result.stdout
