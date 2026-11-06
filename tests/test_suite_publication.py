"""Unsafe generated IDs must never enter observed receipt metadata."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
import test_evidence
import test_suite
from process import run_command

ROOT = Path(__file__).resolve().parents[1]


def unsafe_nodes() -> tuple[str, str, str]:
    authorization = " ".join(("Bearer", "fabricated-marker"))
    url = "".join(("https://", "fabricated", ":", "marker", "@example.test"))
    username_only = "".join(("http://", "fabricated", "@example.test"))
    return (
        f"tests/test_fake.py::test_case[{authorization}]",
        f"tests/test_fake.py::test_case[{url}]",
        f"tests/test_fake.py::test_case[{username_only}]",
    )


@pytest.mark.parametrize("node", unsafe_nodes(), ids=["bearer", "userinfo", "username-only"])
def test_credential_shaped_ids_refuse_with_fixed_content_free_error(node):
    with pytest.raises(ValueError, match=r"^test_id_contains_credential_shape$") as captured:
        test_suite.groups([node])
    assert str(captured.value) == "test_id_contains_credential_shape"
    assert node not in str(captured.value)


def test_explicit_concise_ids_preserve_exact_order_and_values():
    nodes = [
        "tests/test_fake.py::test_case[bearer-scheme]",
        "tests/test_fake.py::test_case[userinfo]",
    ]
    assert test_suite.groups(nodes) == [nodes]


@pytest.mark.parametrize("node", unsafe_nodes(), ids=["bearer", "userinfo", "username-only"])
def test_unsafe_collection_stops_before_dispatch_or_observed_receipt_metadata(
    tmp_path, monkeypatch, node
):
    stages = []

    async def command(arguments, deadline_seconds=test_suite.DEFAULT_CHILD_WATCHDOG_SECONDS):
        if arguments[3] == "erase":
            stages.append("erase")
        elif any(value.endswith("collect_tests.py") for value in arguments):
            stages.append("collect")
            await asyncio.to_thread(Path(arguments[-1]).write_text, json.dumps([node]))
        else:
            raise AssertionError("unsafe_id_reached_child_dispatch")
        return 0

    source = {"fabricated-source": "not-acceptance-evidence"}
    monkeypatch.setattr(test_suite, "command", command)
    monkeypatch.setattr(test_evidence, "inventory", lambda root: source)
    evidence = test_evidence.ObservedEvidence(source)
    with pytest.raises(ValueError, match=r"^test_id_contains_credential_shape$"):
        asyncio.run(test_suite.collect_and_run(sys.executable, tmp_path, (), evidence))
    assert stages == ["erase", "collect"]
    assert evidence.inventory == []
    assert evidence.results == []
    receipt = evidence.write(tmp_path, 1)
    try:
        text = receipt.read_text()
        metadata = json.loads(text)
        assert metadata["status"] == "incomplete"
        assert metadata["inventory"] == []
        assert metadata["test_results"] == []
        assert node not in text
    finally:
        receipt.unlink()


def _write_sample(
    root: Path, *, bearer: bool, explicit_id: bool = False, userinfo_only: bool = False
) -> None:
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths=["."]\n')
    if bearer:
        value = '" ".join(("Bearer", "fabricated-marker"))'
    elif userinfo_only:
        value = '"".join(("http://", "fabricated", "@example.test"))'
    else:
        value = '"".join(("https://", "fabricated", ":", "marker", "@example.test"))'
    ids = ', ids=["userinfo"]' if explicit_id else ""
    (root / "test_generated.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        f"value = {value}\n"
        f'@pytest.mark.parametrize("value", [value]{ids})\n'
        "def test_never_runs(value):\n"
        '    Path("dispatched").write_text("unexpected")\n'
    )


def _copy_standalone_collector(tmp_path: Path) -> Path:
    tools = tmp_path / "standalone-tools"
    tools.mkdir()
    collector = tools / "collect_public_tests.py"
    shutil.copy2(ROOT / "scripts/collect_tests.py", collector)
    shutil.copy2(ROOT / "scripts/public_test_ids.py", tools / "public_test_ids.py")
    collector.write_text(collector.read_text().replace('"--no-cov", ', ""))
    return collector


@pytest.mark.parametrize("kind", ["bearer", "userinfo", "username-only"])
def test_real_collector_refuses_before_stdout_or_inventory_publication(tmp_path, kind):
    _write_sample(tmp_path, bearer=kind == "bearer", userinfo_only=kind == "username-only")
    destination = tmp_path / "cases.json"
    result = asyncio.run(
        run_command(
            [sys.executable, str(ROOT / "scripts/collect_tests.py"), str(destination)],
            cwd=tmp_path,
            deadline_seconds=1800,
        )
    )
    assert result.returncode == 1
    assert "test_id_contains_credential_shape" in result.stdout + result.stderr
    for node in unsafe_nodes():
        value = node.partition("[")[2][:-1]
        assert value not in result.stdout + result.stderr
    assert not destination.exists()
    assert not (tmp_path / "dispatched").exists()


def test_real_collector_preserves_exact_explicit_safe_id(tmp_path):
    _write_sample(tmp_path, bearer=False, explicit_id=True)
    destination = tmp_path / "cases.json"
    result = asyncio.run(
        run_command(
            [sys.executable, str(ROOT / "scripts/collect_tests.py"), str(destination)],
            cwd=tmp_path,
            deadline_seconds=1800,
        )
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "test_generated.py::test_never_runs[userinfo]"
    assert json.loads(destination.read_text()) == [expected]
    assert expected in result.stdout
    assert not (tmp_path / "dispatched").exists()


@pytest.mark.parametrize("kind", ["bearer", "userinfo", "username-only"])
def test_copied_standalone_collector_refuses_unsafe_ids_without_publication(
    tmp_path, monkeypatch, kind
):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    collector = _copy_standalone_collector(tmp_path)
    _write_sample(consumer, bearer=kind == "bearer", userinfo_only=kind == "username-only")
    destination = consumer / "cases.json"

    result = asyncio.run(
        run_command(
            [sys.executable, str(collector), str(destination)],
            cwd=consumer,
            deadline_seconds=1800,
        )
    )

    assert result.returncode == 1
    assert "test_id_contains_credential_shape" in result.stdout + result.stderr
    marker = "fabricated-marker" if kind == "bearer" else "fabricated"
    assert marker not in result.stdout + result.stderr
    assert not destination.exists()
    assert not (consumer / "dispatched").exists()


def test_copied_standalone_collector_collects_exact_safe_id_without_repo_path(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    collector = _copy_standalone_collector(tmp_path)
    _write_sample(consumer, bearer=False, explicit_id=True)
    destination = consumer / "cases.json"

    result = asyncio.run(
        run_command(
            [sys.executable, str(collector), str(destination)],
            cwd=consumer,
            deadline_seconds=1800,
        )
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(destination.read_text()) == ["test_generated.py::test_never_runs[userinfo]"]
    assert not (consumer / "dispatched").exists()
