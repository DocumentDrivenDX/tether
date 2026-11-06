"""Stage failures cannot be converted into a completed aggregate receipt."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import web_quality as gate


@pytest.mark.parametrize("failure", ["build", "python", "ui", "installed", None])
def test_complete_pipeline_removes_old_receipt_and_records_only_after_all_stages(
    tmp_path, monkeypatch, failure
):
    receipt = tmp_path / "aggregate.json"
    receipt.write_text("stale pass")
    monkeypatch.setattr(gate, "AGGREGATE", receipt)
    stages = []

    async def stage(name):
        assert not receipt.exists()
        stages.append(name)
        return 9 if failure == name else 0

    async def npm(root, scripts):
        assert root == tmp_path
        return await stage("build" if scripts == ("build",) else "ui")

    async def python(base, **kwargs):
        assert base == "fabricated-base"
        assert "packages/web-api/tests" in kwargs["required_roots"]
        return await stage("python")

    async def installed(arguments, **kwargs):
        assert arguments[-2:] == ["--retain-workdir", str(tmp_path / "outside")]
        assert kwargs["deadline_seconds"] == 900
        return await stage("installed")

    async def record(root, sources, python_evidence_path):
        assert root == tmp_path
        assert sources == {}
        assert python_evidence_path.name == "python-evidence.json"
        assert stages == ["build", "python", "ui", "installed"]
        stages.append("record")

    monkeypatch.setattr(gate, "run_npm", npm)
    monkeypatch.setattr(gate.quality, "check", python)
    monkeypatch.setattr(gate, "command", installed)
    monkeypatch.setattr(gate, "record_aggregate", record)
    code = asyncio.run(
        gate.complete_gate(tmp_path, "check", "fabricated-base", None, tmp_path / "outside")
    )
    order = ["build", "python", "ui", "installed", "record"]
    if failure:
        assert code == 9
        assert stages == order[: order.index(failure) + 1]
    else:
        assert code == 0
        assert stages == order


@pytest.mark.parametrize("gate_name", ["api-contract", "package-check", "ui-check"])
def test_selected_suite_failure_prevents_following_external_commands(
    tmp_path, monkeypatch, gate_name
):
    monkeypatch.setattr(gate, "run_selected", AsyncMock(return_value=3))
    command, npm = AsyncMock(), AsyncMock()
    monkeypatch.setattr(gate, "command", command)
    monkeypatch.setattr(gate, "run_npm", npm)
    assert asyncio.run(gate.simple_gate(tmp_path, gate_name, None)) == 3
    command.assert_not_awaited()
    npm.assert_not_awaited()


@pytest.mark.parametrize("layer_code", [0, 4])
def test_boundary_failure_stops_before_browser_boundary_command(tmp_path, monkeypatch, layer_code):
    command = AsyncMock(return_value=layer_code)
    npm = AsyncMock(return_value=0)
    monkeypatch.setattr(gate, "command", command)
    monkeypatch.setattr(gate, "run_npm", npm)
    assert asyncio.run(gate.simple_gate(tmp_path, "boundaries", None)) == layer_code
    if layer_code:
        npm.assert_not_awaited()
    else:
        npm.assert_awaited_once_with(tmp_path, ("check:boundaries",))


def test_npm_cannot_claim_success_from_malformed_script_registry(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": ["build"]}))
    command = AsyncMock()
    monkeypatch.setattr(gate, "command", command)
    assert asyncio.run(gate.run_npm(tmp_path, ("build",))) == 1
    command.assert_not_awaited()


@pytest.mark.parametrize("script", ["test", "test:e2e", "check:packages"])
def test_observed_js_receipt_is_written_after_success_and_stale_report_removed(
    tmp_path, monkeypatch, script
):
    reports = {
        "test": "vitest-report.json",
        "test:e2e": "browser-report.json",
        "check:packages": "npm-package-report.json",
    }
    report = tmp_path / "artifacts" / reports[script]
    report.parent.mkdir()
    report.write_text("stale")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {script: "fabricated"}}))
    monkeypatch.setattr(gate.shutil, "which", lambda _: "/fabricated/npm")
    observed = []

    async def command(arguments, **kwargs):
        assert not report.exists()
        observed.append("command")
        return 0

    def record(root, name, before):
        assert root == tmp_path
        assert name == script
        assert before
        assert observed == ["command"]
        observed.append("record")

    monkeypatch.setattr(gate, "command", command)
    monkeypatch.setattr(gate, "record", record)
    assert asyncio.run(gate.run_npm(tmp_path, (script,))) == 0
    assert observed == ["command", "record"]
