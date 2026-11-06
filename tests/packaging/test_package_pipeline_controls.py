"""Artifact orchestration refuses incomplete builds and preserves isolated inputs.

Command doubles exercise controller policy only; they are not installed-package
acceptance evidence. Archive comparisons below use actual fabricated ZIP files.
"""

from __future__ import annotations

import asyncio
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import check_web_packages as gate
import pytest
from package_command_owner import INHERITED_POLICY, use_package_command_policy
from process import Result


def archive(path: Path, payload: str = "fabricated") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as output:
        output.writestr("fabricated/data", payload)


def test_no_build_tool_cannot_create_workspace_or_success_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda _: None)
    output = tmp_path / "observed.json"
    with pytest.raises(ValueError, match="uv_required"):
        asyncio.run(gate.run(tmp_path, output, tmp_path / "retained"))
    assert not output.exists()
    assert not (tmp_path / "retained").exists()


@pytest.mark.parametrize("pins", [[], ["hatchling>=1"]])
def test_build_refuses_unpinned_toolchain_before_any_build(tmp_path, monkeypatch, pins):
    root, retained = tmp_path / "checkout", tmp_path / "private-build"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.uv]\nbuild-constraint-dependencies=" + json.dumps(pins)
    )
    monkeypatch.setattr(gate.shutil, "which", lambda _: "/fabricated/uv")
    build = AsyncMock()
    monkeypatch.setattr(gate, "build", build)
    output = tmp_path / "observed.json"
    with pytest.raises(ValueError, match="exact_build_toolchain_constraints_required"):
        asyncio.run(gate.run(root, output, retained))
    build.assert_not_awaited()
    assert not output.exists()
    assert not (retained / "build-constraints.txt").exists()


def test_checked_success_uses_real_bounded_python_child(tmp_path):
    output = asyncio.run(
        gate.checked([sys.executable, "-I", "-c", "print('fabricated subprocess')"], tmp_path)
    )
    assert output == "fabricated subprocess\n"


def test_package_child_policy_is_explicit_and_external_mode_inherits(monkeypatch, tmp_path):
    runner = AsyncMock(return_value=Result(0, "", ""))
    monkeypatch.setattr(gate, "run_command", runner)
    asyncio.run(gate.checked(["fabricated"], tmp_path))
    first_call = runner.await_args
    assert first_call is not None
    assert first_call.kwargs == {
        "cwd": tmp_path,
        "deadline_seconds": None,
        "own_process_group": True,
    }
    runner.reset_mock()
    with use_package_command_policy(INHERITED_POLICY):
        asyncio.run(gate.checked_remote(["fabricated"], tmp_path))
    second_call = runner.await_args
    assert second_call is not None
    assert second_call.kwargs == {
        "cwd": tmp_path,
        "deadline_seconds": None,
        "own_process_group": False,
    }


def test_incomplete_package_gate_does_not_replace_existing_receipt(tmp_path, monkeypatch):
    output = tmp_path / "receipt.json"
    output.write_text("prior receipt\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["check_web_packages.py", "--output", str(output)])
    monkeypatch.delenv("TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED", raising=False)

    async def incomplete(*_args, **_kwargs):
        raise gate.CommandIncompleteError("output_capture_failed", "execution")

    monkeypatch.setattr(gate, "run", incomplete)
    assert asyncio.run(gate.main()) == 2
    assert output.read_text() == "prior receipt\n"
    assert json.loads((tmp_path / "artifacts/packages-incomplete.json").read_text()) == {
        "schema_version": 1,
        "status": "incomplete",
        "framework": "packages",
        "reason": "output_capture_failed",
        "phase": "execution",
    }


def test_lock_without_registry_candidates_cannot_establish_runtime_cohort(tmp_path):
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname="fabricated"\nversion="1.0"\nsource={editable="."}\n'
    )
    with pytest.raises(ValueError, match="locked_registry_runtime_cohort_required"):
        gate.locked_runtime_pins(tmp_path)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_build_requires_one_wheel_and_sdist_and_pins_build_inputs(tmp_path, monkeypatch, count):
    monkeypatch.setattr(gate, "PROJECTS", {"fabricated": "."})
    commands = []

    def child(arguments, cwd):
        commands.append((arguments, cwd))
        destination = Path(arguments[arguments.index("--out-dir") + 1])
        destination.mkdir(parents=True)
        for index in range(count):
            archive(destination / f"fabricated-{index}.whl")
        (destination / "fabricated.tar.gz").write_bytes(b"fabricated")
        return ""

    async def checked(arguments, cwd):
        return await asyncio.to_thread(child, arguments, cwd)

    monkeypatch.setattr(gate, "checked", checked)
    pending = gate.build("/fabricated/uv", tmp_path, tmp_path / "out", tmp_path / "pins")
    if count == 1:
        wheels = asyncio.run(pending)
        assert wheels["fabricated"].is_file()
    else:
        with pytest.raises(ValueError, match="build_requires_wheel_and_sdist"):
            asyncio.run(pending)
    arguments, cwd = commands[0]
    assert arguments[arguments.index("--python") + 1] == sys.executable
    assert arguments[arguments.index("--build-constraints") + 1] == str(tmp_path / "pins")
    assert "--no-sources" in arguments
    assert cwd == tmp_path


@pytest.mark.parametrize("changed", [False, True])
def test_sdist_rebuild_requires_exact_archive_members(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(gate, "PROJECTS", {"fabricated": "."})
    first = tmp_path / "first/fabricated"
    first.mkdir(parents=True)
    (first / "fabricated.tar.gz").write_bytes(b"fabricated sdist")
    wheel = first / "fabricated.whl"
    archive(wheel)
    commands = []

    def child(arguments, cwd):
        commands.append((arguments, cwd))
        destination = Path(arguments[arguments.index("--out-dir") + 1])
        archive(destination / "fabricated.whl", "changed" if changed else "fabricated")
        return ""

    async def checked(arguments, cwd):
        return await asyncio.to_thread(child, arguments, cwd)

    monkeypatch.setattr(gate, "checked", checked)
    pending = gate.rebuild_sdists(
        "/fabricated/uv", tmp_path / "first", tmp_path, tmp_path / "pins", {"fabricated": wheel}
    )
    if changed:
        with pytest.raises(ValueError, match="sdist_rebuilt_wheel_contents_differ"):
            asyncio.run(pending)
    else:
        assert asyncio.run(pending) == [
            {"node_id": "installed::fabricated::sdist-rebuilt-wheel", "outcome": "passed"}
        ]
    arguments, cwd = commands[0]
    assert arguments[-1] == str(first / "fabricated.tar.gz")
    assert cwd == tmp_path
    assert "--no-sources" in arguments


def test_checked_failure_does_not_print_fabricated_consumer_source(tmp_path, monkeypatch, capsys):
    command = AsyncMock(return_value=Result(7, "safe stdout", "safe stderr"))
    monkeypatch.setattr(gate, "run_command", command)
    with pytest.raises(ValueError, match="artifact_command_failed:7"):
        asyncio.run(
            gate.checked([sys.executable, "-I", "-c", "fabricated source marker"], tmp_path)
        )
    captured = capsys.readouterr()
    assert "fabricated source marker" not in captured.out
    assert "<fabricated installed consumer>" in captured.out
    assert "safe stdout" in captured.out
    assert "safe stderr" in captured.err


def test_profile_checks_stop_on_failed_consumer_without_terminal_or_browser_claim(
    tmp_path, monkeypatch
):
    command = AsyncMock(side_effect=ValueError("fabricated_consumer_failure"))
    browser = AsyncMock()
    monkeypatch.setattr(gate, "checked", command)
    monkeypatch.setattr(gate, "installed_browser", browser)
    with pytest.raises(ValueError, match="fabricated_consumer_failure"):
        asyncio.run(
            gate.profile_checks(
                tmp_path / "bin/python", "combined", ("novirhiza-tether",), ("tui",), tmp_path
            )
        )
    command.assert_awaited_once()
    browser.assert_not_awaited()


def test_profile_checks_run_each_selected_contract_and_return_observed_browser_ids(
    tmp_path, monkeypatch
):
    command = AsyncMock(return_value="")
    browser = AsyncMock(return_value=[{"node_id": "fabricated-browser", "outcome": "passed"}])
    monkeypatch.setattr(gate, "checked", command)
    monkeypatch.setattr(gate, "installed_browser", browser)
    python = tmp_path / "bin/python"
    result = asyncio.run(
        gate.profile_checks(
            python, "combined", ("novirhiza-tether", "novirhiza-tether-web-ui"), ("tui",), tmp_path
        )
    )
    assert [item["node_id"] for item in result] == [
        "installed::combined::closure",
        "installed::combined::host-recovery",
        "installed::combined::legacy-composition",
        "installed::combined::packaged-assets",
        "installed::combined::terminal-entrypoint",
        "fabricated-browser",
    ]
    terminal = python.parent / ("tether.exe" if sys.platform == "win32" else "tether")
    assert command.await_args_list[-1].args[0] == [str(terminal), "--help"]
    browser.assert_awaited_once_with(python)


@pytest.mark.parametrize("missing", ["npm", "env"])
def test_installed_browser_requires_both_tools_before_running(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(
        gate.shutil, "which", lambda name: None if name == missing else "/bin/" + name
    )
    runner = AsyncMock()
    monkeypatch.setattr(gate, "checked", runner)
    with pytest.raises(ValueError, match="npm_and_env_required"):
        asyncio.run(gate.installed_browser(tmp_path / "bin/python"))
    runner.assert_not_awaited()


@pytest.mark.parametrize("outcome", ["passed", "failed"])
def test_installed_browser_uses_venv_entrypoint_and_fresh_report(tmp_path, monkeypatch, outcome):
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "artifacts/browser-report.json"
    report.parent.mkdir()
    report.write_text("stale report")
    monkeypatch.setattr(gate.shutil, "which", lambda name: "/bin/" + name)
    commands = []

    async def checked(arguments, cwd):
        assert not report.exists()
        commands.append((arguments, cwd))
        report.write_text("new fabricated report")
        return ""

    monkeypatch.setattr(gate, "checked", checked)
    monkeypatch.setattr(
        gate, "playwright_results", lambda *_: [{"node_id": "fabricated", "outcome": outcome}]
    )
    python = tmp_path / "venv/bin/python"
    pending = gate.installed_browser(python)
    if outcome == "passed":
        assert asyncio.run(pending)[0]["outcome"] == "passed"
    else:
        with pytest.raises(ValueError, match="required_cases_did_not_pass"):
            asyncio.run(pending)
    assert commands[0] == (
        ["/bin/env", f"TETHER_BROWSER_PYTHON={python}", "/bin/npm", "run", "test:e2e"],
        tmp_path,
    )


def test_matrix_preserves_runtime_pins_extras_and_dev_harness_separation(tmp_path, monkeypatch):
    core = "novirhiza-tether"
    monkeypatch.setattr(gate, "MATRIX", {"remote": ((core,), ("remote",), ("fastapi",))})
    commands = []

    async def checked(arguments, cwd):
        commands.append((arguments, cwd))
        if "-c" not in arguments:
            return ""
        return json.dumps(
            {
                "modules": {"tether.client": "fabricated-installed-path"},
                "dependencies": [[core, "0.2.0.dev0"]],
                "metadata": [
                    {
                        "name": core,
                        "version": "0.2.0.dev0",
                        "requires": [],
                        "license_expression": "MIT OR Apache-2.0",
                        "license": "",
                    }
                ],
            }
        )

    monkeypatch.setattr(gate, "checked", checked)
    monkeypatch.setattr(gate, "profile_checks", AsyncMock(return_value=[]))
    result = asyncio.run(
        gate.matrix("/fabricated/uv", {core: tmp_path / "core.whl"}, tmp_path, tmp_path / "pins")
    )
    install = commands[1][0]
    assert install[install.index("--constraint") + 1] == str(tmp_path / "pins")
    assert str(tmp_path / "core.whl") + "[remote]" in install
    assert all("hypercorn" not in argument for argument in install)
    assert result["remote"]["harness_requirements"] == []
    assert result["remote"]["isolated_import_paths_verified"] is True
    assert result["remote"]["sbom"]["bomFormat"] == "CycloneDX"
    assert commands[2][0][:3] == [str(tmp_path / "remote/bin/python"), "-I", "-c"]
