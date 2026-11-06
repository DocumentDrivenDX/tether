"""Web store benchmark driver argument bounds, profile selection and receipt publication."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import benchmark_web
import pytest


def _observation() -> dict[str, Any]:
    return {
        "profile": "fabricated-profile",
        "workload": {"sessions": 1},
        "journal_bytes": 12,
        "results": {"metric": 1.5},
        "limits": {"limit": 2},
        "durability": "fabricated",
        "checks": {"ok": True},
        "scope": "fabricated-scope",
    }


def _arguments(output: Path, storage: str = "posix") -> argparse.Namespace:
    return argparse.Namespace(
        sessions=2, entries=3, large=199, samples=100, storage=storage, output=output
    )


def test_receipt_binds_versions_sources_and_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(benchmark_web.PROJECT)
    output = tmp_path / "receipt.json"
    observation: Any = _observation()
    benchmark_web.write_receipt(_arguments(output), observation, {"source.py": "digest"})
    receipt = json.loads(output.read_text())
    assert receipt["profile"] == "fabricated-profile"
    assert receipt["source_sha256"] == {"source.py": "digest"}
    assert receipt["results"] == {"metric": 1.5}
    assert set(receipt["versions"]) == {"python", "platform", "architecture"}
    assert "scripts/benchmark_web.py" in receipt["source"]
    assert all(len(value) == 64 for value in receipt["source"].values())


@pytest.mark.parametrize(
    ("storage", "profile"),
    [("posix", "local-posix-jsonl"), ("memory", "memoryfiles-jsonl-reference-capacity")],
)
def test_benchmark_selects_profile_and_prints_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    storage: str,
    profile: str,
) -> None:
    monkeypatch.chdir(benchmark_web.PROJECT)
    seen: dict[str, Any] = {}

    async def fake_profile(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return _observation()

    monkeypatch.setattr(benchmark_web, "benchmark_store_profile", fake_profile)
    monkeypatch.setattr(benchmark_web, "inventory", lambda _root: {"source.py": "digest"})
    output = tmp_path / "receipt.json"
    asyncio.run(benchmark_web.benchmark(_arguments(output, storage)))
    assert seen["profile"] == profile
    assert (seen["sessions"], seen["entries_per_session"]) == (2, 3)
    assert (seen["large_session_entries"], seen["samples"]) == (199, 100)
    assert json.loads(capsys.readouterr().out) == {"metric": 1.5}
    assert json.loads(output.read_text())["profile"] == "fabricated-profile"


def test_benchmark_refuses_source_change_during_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter([{"a": "1"}, {"a": "2"}])

    async def fake_profile(**_kwargs: Any) -> dict[str, Any]:
        return _observation()

    monkeypatch.setattr(benchmark_web, "benchmark_store_profile", fake_profile)
    monkeypatch.setattr(benchmark_web, "inventory", lambda _root: next(answers))
    output = tmp_path / "receipt.json"
    with pytest.raises(RuntimeError, match="benchmark_source_changed_during_observation"):
        asyncio.run(benchmark_web.benchmark(_arguments(output)))
    assert not output.exists()


@pytest.mark.parametrize(
    "flags",
    [
        ["--sessions", "0"],
        ["--entries", "-1"],
        ["--large", "198"],
        ["--samples", "99"],
    ],
)
def test_main_refuses_undersized_workloads_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str]
) -> None:
    ran: list[bool] = []

    async def should_not_run(_arguments: argparse.Namespace) -> None:
        ran.append(True)

    monkeypatch.setattr(benchmark_web, "benchmark", should_not_run)
    monkeypatch.setattr(
        sys, "argv", ["benchmark_web", "--output", str(tmp_path / "o.json"), *flags]
    )
    with pytest.raises(SystemExit) as caught:
        benchmark_web.main()
    assert caught.value.code == 2
    assert ran == []


def test_main_runs_default_reference_sizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[argparse.Namespace] = []

    async def record(arguments: argparse.Namespace) -> None:
        seen.append(arguments)

    monkeypatch.setattr(benchmark_web, "benchmark", record)
    monkeypatch.setattr(sys, "argv", ["benchmark_web", "--output", str(tmp_path / "o.json")])
    benchmark_web.main()
    arguments = seen[0]
    assert (arguments.sessions, arguments.entries, arguments.large, arguments.samples) == (
        10_000,
        200,
        100_000,
        100,
    )
    assert arguments.storage == "posix"
