"""Every collected case is preserved; process and aggregate coverage failures stop the gate."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest
import selected_profiles_process
import test_evidence
import test_suite
from context_lifetime_controls import wait_event_or_task
from process import CommandIncompleteError, run_command
from process_lifetime_controls import ShieldCancellationObserver

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "packages/databricks/tests/test_carrier.py",
        "packages/web-api/tests/test_api_contract.py",
    ],
)
def test_package_collection_keeps_exact_ids_without_root_test_plugin(tmp_path, module):
    inventories = []
    for index, selected in enumerate(([module], ["tests/test_client_reads.py", module])):
        destination = tmp_path / f"inventory-{index}.json"
        result = asyncio.run(
            run_command(
                [
                    sys.executable,
                    str(ROOT / "scripts/collect_tests.py"),
                    str(destination),
                    *selected,
                ],
                cwd=ROOT,
            )
        )
        assert result.returncode == 0, result.stdout + result.stderr
        inventories.append(
            [node for node in json.loads(destination.read_text()) if node.startswith(module + "::")]
        )
    assert inventories[0]
    assert inventories[0] == inventories[1]


@pytest.fixture(autouse=True)
def isolate_control_test_source_identity(monkeypatch):
    # In-process command fakes exercise dispatch, not this checkout's source proof.
    # Real nested runner subprocesses retain actual source inventory validation.
    def fabricated(root):
        return {"fabricated-unit-control": "not-runtime-evidence"}

    monkeypatch.setattr(test_suite, "inventory", fabricated)
    monkeypatch.setattr(test_evidence, "inventory", fabricated)


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


def test_measured_expensive_cases_get_separate_children_without_larger_deadlines():
    nodes = ["tests/test_fake.py::before"]
    nodes += [f"tests/test_client_benchmark.py::case[{index}]" for index in range(3)]
    nodes += ["tests/test_fake.py::after"]
    nodes += [f"tests/test_postgres_resource_shutdown.py::case[{index}]" for index in range(2)]
    batches = test_suite.groups(nodes)
    assert [node for batch in batches for node in batch] == nodes
    assert all(len(batch) == 1 for batch in batches)


@pytest.mark.parametrize("nodes", [None, [], [""], [1], ["a", "a"], ["a" * 65536]])
def test_invalid_inventory_never_becomes_an_empty_success(nodes):
    with pytest.raises(
        ValueError, match=r"empty_test_inventory|invalid_test_inventory|test_id_too_large"
    ):
        test_suite.groups(nodes)


@pytest.mark.parametrize("failure", ["erase", "collect", "batch", "report"])
def test_each_stage_failure_prevents_a_successful_gate(monkeypatch, failure):
    stages = []

    async def fake(arguments, deadline_seconds=test_suite.DEFAULT_CHILD_WATCHDOG_SECONDS):
        if arguments[3] == "erase":
            stage = "erase"
        elif any(value.endswith("collect_tests.py") for value in arguments):
            stage = "collect"
            await asyncio.to_thread(
                Path(arguments[-1]).write_text, json.dumps(["tests/test_fake.py::test_case"])
            )
        elif arguments[3] == "report":
            stage = "report"
        else:
            stage = "batch"
        stages.append(stage)
        if stage == "batch" and failure != "batch":
            report = next(
                value.removeprefix("--junitxml=")
                for value in arguments
                if value.startswith("--junitxml=")
            )
            await asyncio.to_thread(
                Path(report).write_text,
                '<testsuites><testsuite><testcase classname="tests.test_fake" '
                'name="test_case"/></testsuite></testsuites>',
            )
        return 3 if stage == failure else 0

    monkeypatch.setattr(test_suite, "command", fake)
    assert asyncio.run(test_suite.run_suite(sys.executable)) != 0
    assert stages == ["erase", "collect", "batch", "report"][: stages.index(failure) + 1]


def test_partial_junit_retains_only_prior_complete_group_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    nodes = [f"tests/test_fake.py::test_case_{index}" for index in range(33)]
    source = {"fabricated-source": "fixed"}
    monkeypatch.setattr(test_suite, "inventory", lambda root: source)
    monkeypatch.setattr(test_evidence, "inventory", lambda root: source)
    receipt_path = tmp_path / "python-evidence.json"
    batch_count = 0

    async def fake(arguments, deadline_seconds=test_suite.DEFAULT_CHILD_WATCHDOG_SECONDS):
        nonlocal batch_count
        if arguments[3] == "erase":
            return 0
        if any(value.endswith("collect_tests.py") for value in arguments):
            await asyncio.to_thread(Path(arguments[-1]).write_text, json.dumps(nodes))
            return 0
        report = next(
            value.removeprefix("--junitxml=")
            for value in arguments
            if value.startswith("--junitxml=")
        )
        batch = [value for value in arguments if value in nodes]
        batch_count += 1
        if batch_count == 2:
            await asyncio.to_thread(Path(report).write_text, "<testsuites><broken>")
            return 0
        cases = "".join(
            f'<testcase classname="tests.test_fake" name="{node.split("::")[-1]}"/>'
            for node in batch
        )
        await asyncio.to_thread(
            Path(report).write_text,
            "<testsuites><testsuite>" + cases + "</testsuite></testsuites>",
        )
        return 0

    monkeypatch.setattr(test_suite, "command", fake)
    assert asyncio.run(test_suite.run_suite(sys.executable, evidence_path=receipt_path)) == 1
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "incomplete"
    assert receipt["inventory_complete"] is True
    assert len(receipt["test_results"]) == 32
    assert receipt["pending_nodes"] == [nodes[-1]]
    assert [group["status"] for group in receipt["groups"]] == ["passed", "incomplete"]
    assert "exit_code" not in receipt


@pytest.mark.parametrize(
    ("reason", "phase"),
    [
        ("watchdog_exhausted", "execution"),
        ("output_capture_failed", "execution"),
        ("unsupported_platform", "spawn"),
    ],
)
def test_command_incomplete_receipt_never_contains_zero_child_exit(
    tmp_path, monkeypatch, reason, phase
):
    monkeypatch.chdir(tmp_path)
    node = "tests/test_fake.py::test_case"
    source = {"fabricated-source": "fixed"}
    monkeypatch.setattr(test_suite, "inventory", lambda root: source)
    monkeypatch.setattr(test_evidence, "inventory", lambda root: source)
    receipt_path = tmp_path / "python-evidence.json"

    async def fake(arguments, deadline_seconds=test_suite.DEFAULT_CHILD_WATCHDOG_SECONDS):
        if arguments[3] == "erase":
            return 0
        if any(value.endswith("collect_tests.py") for value in arguments):
            await asyncio.to_thread(Path(arguments[-1]).write_text, json.dumps([node]))
            return 0
        raise CommandIncompleteError(reason, phase)

    monkeypatch.setattr(test_suite, "command", fake)
    assert asyncio.run(test_suite.run_suite(sys.executable, evidence_path=receipt_path)) == 1
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "incomplete"
    assert receipt["pending_nodes"] == [node]
    assert receipt["incomplete_reason"] == reason
    assert receipt["incomplete_phase"] == phase
    assert receipt["groups"] == [
        {
            "index": 0,
            "status": "incomplete",
            "node_ids": [node],
            "outcome_count": 0,
            "reason": reason,
        }
    ]
    assert "exit_code" not in receipt
    with pytest.raises(ValueError, match="python_evidence_schema_invalid"):
        test_evidence.validate_python_receipt(receipt, source)


def test_cancellation_waits_for_incomplete_receipt_write_to_settle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = {"fabricated-source": "fixed"}
    monkeypatch.setattr(test_suite, "inventory", lambda root: source)
    monkeypatch.setattr(test_evidence, "inventory", lambda root: source)
    receipt_path = tmp_path / "cancelled-python-evidence.json"
    write_started = asyncio.Event()
    release_write = threading.Event()
    original_write = test_evidence.ObservedEvidence.write
    loop: asyncio.AbstractEventLoop | None = None

    def held_write(evidence, root, code, destination=None):
        assert loop is not None
        loop.call_soon_threadsafe(write_started.set)
        release_write.wait()
        return original_write(evidence, root, code, destination)

    async def fail_erase(arguments, deadline_seconds=test_suite.DEFAULT_CHILD_WATCHDOG_SECONDS):
        return 3

    monkeypatch.setattr(test_evidence.ObservedEvidence, "write", held_write)
    monkeypatch.setattr(test_suite, "command", fail_erase)
    observer = ShieldCancellationObserver(1)
    observer.install(selected_profiles_process, monkeypatch)

    async def scenario():
        nonlocal loop
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(test_suite.run_suite(sys.executable, evidence_path=receipt_path))
        observer.target = task
        try:
            await wait_event_or_task(write_started, task)
            task.cancel()
            await wait_event_or_task(observer.acknowledged[0], task)
            assert not task.done()
            release_write.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release_write.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "incomplete"
    assert receipt["incomplete_phase"] == "erase"


@pytest.mark.parametrize("both_branches", [False, True])
def test_real_inventory_and_aggregate_branch_coverage_gate(tmp_path, both_branches, monkeypatch):
    shared_temporary_root = tmp_path / "shared-pytest-root"
    shared_temporary_root.mkdir()
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(shared_temporary_root))
    # The nested gate must not erase the outer suite's coverage database.
    monkeypatch.setenv("COVERAGE_FILE", str(tmp_path / ".coverage"))
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


def test_concurrent_real_suites_do_not_erase_shared_or_each_other_data(tmp_path, monkeypatch):
    shared = tmp_path / ".coverage"
    shared.write_text("unrelated coverage writer")
    monkeypatch.setenv("COVERAGE_FILE", str(shared))
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["."]\n'
        '[tool.coverage.run]\nbranch=true\nsource=["sample"]\n'
        "[tool.coverage.report]\nfail_under=85\n"
    )
    (tmp_path / "sample.py").write_text(
        "def choose(flag):\n    if flag:\n        return 1\n    return 2\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text(
        "from sample import choose\ndef test_both():\n"
        "    assert choose(True) == 1\n    assert choose(False) == 2\n"
    )

    async def concurrently():
        return await asyncio.gather(
            *(
                run_command([sys.executable, str(ROOT / "scripts/test_suite.py")], cwd=tmp_path)
                for _ in range(2)
            )
        )

    results = asyncio.run(concurrently())
    assert all(result.returncode == 0 for result in results)
    assert all("100%" in result.stdout for result in results)
    assert shared.read_text() == "unrelated coverage writer"
    data_paths = {
        argument
        for result in results
        for argument in result.stdout.split()
        if argument.startswith("--data-file=")
    }
    assert len(data_paths) == 2


def test_empty_required_package_suite_refuses_despite_other_collected_cases(monkeypatch):
    stages = []

    async def fake(arguments, deadline_seconds=test_suite.DEFAULT_CHILD_WATCHDOG_SECONDS):
        stages.append(arguments[3])
        if any(value.endswith("collect_tests.py") for value in arguments):
            await asyncio.to_thread(
                Path(arguments[-1]).write_text, json.dumps(["tests/test_real.py::test_one"])
            )
        return 0

    monkeypatch.setattr(test_suite, "command", fake)
    assert asyncio.run(test_suite.run_suite(sys.executable, ("packages/web-api/tests",))) != 0
    assert stages == ["erase", "run"]
