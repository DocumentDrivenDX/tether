"""CLI orchestration propagates failures rather than silently passing a gate."""

import asyncio
import io
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import AsyncMock

import check_ceilings
import check_dependencies
import check_distribution
import check_file_size
import check_layers
import claude_hook
import pytest
import quality
from process import Result


def test_dependency_cli_reports_missing_and_contaminated_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def absent() -> dict[str, str]:
        raise PackageNotFoundError("missing")

    monkeypatch.setattr(check_dependencies, "reachable_dependencies", absent)
    assert check_dependencies.main() == 1
    monkeypatch.setattr(check_dependencies, "reachable_dependencies", lambda: {"openai": "1"})
    assert check_dependencies.main() == 1
    monkeypatch.setattr(
        check_dependencies,
        "reachable_dependencies",
        lambda: {
            "pydantic-ai-slim": "1",
            "pydantic": "1",
            "anyio": "1",
        },
    )
    assert check_dependencies.main() == 0


def test_simple_cli_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_file_size, "check_sizes", lambda _: [])
    assert check_file_size.main() == 0
    monkeypatch.setattr(check_file_size, "check_sizes", lambda _: ["too big"])
    assert check_file_size.main() == 1
    monkeypatch.setattr(check_layers, "check_tree", lambda _: [])
    assert check_layers.main() == 0
    monkeypatch.setattr(check_layers, "check_tree", lambda _: ["cycle"])
    assert check_layers.main() == 1
    monkeypatch.setattr(check_ceilings, "check", AsyncMock(return_value=["raised"]))
    monkeypatch.setattr(sys, "argv", ["check", "HEAD"])
    assert asyncio.run(check_ceilings.main()) == 1


@pytest.mark.parametrize("code", [0, 3])
def test_shared_recipe_stops_on_failure(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    runner = AsyncMock(return_value=Result(code, "", ""))
    monkeypatch.setattr(quality, "run_command", runner)
    suite = AsyncMock(return_value=0)
    monkeypatch.setattr(quality, "run_suite", suite)
    monkeypatch.setattr(sys, "argv", ["quality"])
    assert asyncio.run(quality.main()) == code
    assert runner.await_count == (1 if code else 7)
    assert suite.await_count == (0 if code else 1)


@pytest.mark.parametrize(
    ("mode", "active", "code", "expected"),
    [
        ("stop", True, 0, 0),
        ("stop", False, 0, 0),
        ("post-edit", False, 1, 2),
        ("post-edit", False, 0, 0),
    ],
)
def test_agent_hook_failures_and_recursion(
    monkeypatch: pytest.MonkeyPatch, mode: str, active: bool, code: int, expected: int
) -> None:
    import json

    runner = AsyncMock(return_value=Result(code, "bad", "error"))
    monkeypatch.setattr(claude_hook, "run_command", runner)
    monkeypatch.setattr(sys, "argv", ["hook", mode])
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "stop_hook_active": active,
                    "tool_input": {"file_path": str(Path.cwd() / "test.py")},
                }
            )
        ),
    )
    assert asyncio.run(claude_hook.main()) == expected
    assert runner.await_count == (0 if active else 2 if mode == "post-edit" and not code else 1)


def test_distribution_command_propagates_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        check_distribution, "run_command", AsyncMock(return_value=Result(1, "", "bad"))
    )
    with pytest.raises(ValueError, match="bad"):
        asyncio.run(check_distribution.run([sys.executable]))
    monkeypatch.setattr(sys, "argv", ["distribution", "/missing-dist"])
    assert asyncio.run(check_distribution.main()) == 1


def test_shared_recipe_propagates_aggregate_suite_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quality, "run_command", AsyncMock(return_value=Result(0, "", "")))
    suite = AsyncMock(return_value=2)
    monkeypatch.setattr(quality, "run_suite", suite)
    monkeypatch.setattr(sys, "argv", ["quality"])
    assert asyncio.run(quality.main()) == 2
    suite.assert_awaited_once_with(sys.executable)
