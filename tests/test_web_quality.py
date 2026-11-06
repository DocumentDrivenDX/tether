"""Absent packages, empty inventories and child failures cannot claim web success."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import web_quality
import web_suites
from browser_evidence import incomplete_marker, playwright_timed_out, record
from process import CommandIncompleteError, Result


@pytest.fixture(autouse=True)
def isolated_aggregate_receipt(tmp_path, monkeypatch):
    # Unit dispatch controls never remove or replace a real qualification receipt.
    monkeypatch.setattr(web_quality, "AGGREGATE", tmp_path / "aggregate.json")


@pytest.mark.parametrize("gate", tuple(web_quality.REQUIRED))
def test_unbuilt_gate_never_runs_or_passes(tmp_path, monkeypatch, gate):
    runner = AsyncMock(return_value=0)
    monkeypatch.setattr(web_quality, "command", runner)
    assert asyncio.run(web_quality.execute(tmp_path, gate, "HEAD", None)) == 1
    runner.assert_not_awaited()


def test_empty_required_suite_is_explicit(tmp_path):
    folder = tmp_path / "tests/packaging"
    folder.mkdir(parents=True)
    with pytest.raises(ValueError, match="unbuilt_required_test_suite"):
        web_suites.resolve_paths(tmp_path, ("tests/packaging",))


def test_required_patterns_resolve_files_and_directories_without_duplicates(tmp_path):
    folder = tmp_path / "tests/client"
    folder.mkdir(parents=True)
    (folder / "test_client.py").write_text("def test_client(): pass")
    assert web_suites.resolve_paths(tmp_path, ("tests/client/test_*.py", "tests/client")) == (
        "tests/client/test_client.py",
        "tests/client",
    )


def test_npm_missing_script_and_executable_fail_without_if_present(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: "/usr/bin/npm")
    runner = AsyncMock(return_value=0)
    monkeypatch.setattr(web_quality, "command", runner)
    assert asyncio.run(web_quality.run_npm(tmp_path, ("typecheck",))) == 1
    runner.assert_not_awaited()
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: None)
    assert asyncio.run(web_quality.run_npm(tmp_path, ("build",))) == 1


def test_npm_child_failure_stops_the_gate(tmp_path, monkeypatch):
    scripts = {name: "fabricated-command" for name in ("test", "build")}
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}))
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: "/usr/bin/npm")
    runner = AsyncMock(return_value=7)
    monkeypatch.setattr(web_quality, "command", runner)
    assert asyncio.run(web_quality.run_npm(tmp_path, ("test", "build"))) == 7
    runner.assert_awaited_once_with(
        ["/usr/bin/npm", "run", "test"],
        deadline_seconds=web_quality.DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
        externally_supervised=True,
    )


@pytest.mark.parametrize(
    ("report_case", "returncode", "expected"),
    [
        ("complete", 0, 0),
        ("missing", 0, 1),
        ("duplicate", 0, 1),
        ("unknown", 0, 1),
        ("malformed", 0, 1),
        ("failed", 1, 1),
        ("skipped", 0, 1),
    ],
)
def test_native_owner_stage_requires_exact_fresh_junit_outcomes(
    tmp_path, monkeypatch, capsys, report_case, returncode, expected
):
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: "/qualified/node")
    names: list[str] = list(web_quality.NATIVE_OWNER_TESTS)
    if report_case == "missing":
        names.pop()
    elif report_case == "duplicate":
        names[-1] = names[0]
    elif report_case == "unknown":
        names[-1] = "not-an-approved-owner-test"

    async def command(arguments, **kwargs):
        assert arguments[:3] == [
            "/qualified/node",
            "--test",
            "--test-reporter=junit",
        ]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["deadline_seconds"] == 17
        report = Path(arguments[3].split("=", 1)[1])
        if report_case == "malformed":
            await asyncio.to_thread(report.write_text, "<not-junit")
            return returncode
        cases = []
        for index, name in enumerate(names):
            status = (
                "<failure/>"
                if report_case == "failed" and index == 0
                else "<skipped/>"
                if report_case == "skipped" and index == 0
                else ""
            )
            cases.append(f'<testcase classname="test" name="{name}">{status}</testcase>')
        await asyncio.to_thread(
            report.write_text, "<testsuites>" + "".join(cases) + "</testsuites>"
        )
        return returncode

    monkeypatch.setattr(web_quality, "command", command)
    assert (
        asyncio.run(web_quality.run_native_owner_tests(tmp_path, deadline_seconds=17)) == expected
    )
    if report_case == "failed":
        assert (
            "tests/test_functional_command_owner.mjs::" + names[0] + ": failed"
            in capsys.readouterr().err
        )
    elif report_case == "skipped":
        assert (
            "tests/test_functional_command_owner.mjs::" + names[0] + ": skipped"
            in capsys.readouterr().err
        )


@pytest.mark.parametrize(("status", "raises_incomplete"), [("timedOut", True), ("failed", False)])
def test_playwright_watchdog_status_is_incomplete_not_assertion_failure(
    tmp_path, monkeypatch, status, raises_incomplete
):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test:e2e": "playwright"}}))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: "/usr/bin/npm")
    monkeypatch.setattr(web_quality, "inventory", lambda _: {"tests/ui/reference.spec.ts": "hash"})

    async def failed_npm(*_args, **_kwargs):
        (artifacts / "browser-report.json").write_text(
            json.dumps({"suites": [{"specs": [{"tests": [{"results": [{"status": status}]}]}]}]})
        )
        return 1

    monkeypatch.setattr(web_quality, "command", failed_npm)
    if raises_incomplete:
        from process import CommandIncompleteError

        with pytest.raises(CommandIncompleteError, match="tool_command_watchdog_exhausted"):
            asyncio.run(web_quality.run_npm(tmp_path, ("test:e2e",)))
    else:
        assert asyncio.run(web_quality.run_npm(tmp_path, ("test:e2e",))) == 1


def test_multi_phase_archive_wrapper_has_its_own_finite_bound(tmp_path, monkeypatch):
    scripts = {"check:packages": "fabricated-multi-phase-command"}
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}))
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: "/usr/bin/npm")
    runner = AsyncMock(return_value=9)
    monkeypatch.setattr(web_quality, "command", runner)
    assert asyncio.run(web_quality.run_npm(tmp_path, ("check:packages",))) == 9
    runner.assert_awaited_once_with(
        ["/usr/bin/npm", "run", "check:packages"],
        deadline_seconds=web_quality.DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
        externally_supervised=True,
    )


def test_package_marker_is_a_strict_incomplete_join(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"check:packages": "fabricated-command"}})
    )
    monkeypatch.setattr(web_quality.shutil, "which", lambda _: "/usr/bin/npm")

    async def owner(*_args, **_kwargs):
        marker = tmp_path / "artifacts/packages-incomplete.json"
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "incomplete",
                    "framework": "packages",
                    "reason": "output_capture_failed",
                    "phase": "execution",
                }
            )
        )
        return 2

    monkeypatch.setattr(web_quality, "command", owner)
    with pytest.raises(CommandIncompleteError, match="tool_command_output_capture_failed"):
        asyncio.run(web_quality.run_npm(tmp_path, ("check:packages",)))


@pytest.mark.parametrize("raw", ["invalid", "999", "1_000", "1e3", "1000.0", "01", str(2**31)])
def test_functional_watchdog_rejects_invalid_bounds(raw):
    with pytest.raises(ValueError, match="functional_watchdog_invalid"):
        web_quality.functional_watchdog_seconds({web_quality.FUNCTIONAL_WATCHDOG_ENV: raw})


def test_functional_watchdog_uses_configured_milliseconds():
    assert (
        web_quality.functional_watchdog_seconds({web_quality.FUNCTIONAL_WATCHDOG_ENV: "2400000"})
        == 2400
    )


def test_functional_watchdog_accepts_supported_timer_maximum():
    assert (
        web_quality.functional_watchdog_seconds(
            {web_quality.FUNCTIONAL_WATCHDOG_ENV: str(2**31 - 1)}
        )
        == (2**31 - 1) / 1000
    )


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        ("timedOut", "", True),
        ("failed", "Test timeout of 30000ms exceeded", False),
        ("failed", "assertion mentions timeout", False),
    ],
)
def test_playwright_timeout_uses_structured_attempt_status(tmp_path, status, message, expected):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "suites": [
                    {
                        "specs": [
                            {
                                "tests": [
                                    {"results": [{"status": status, "error": {"message": message}}]}
                                ]
                            }
                        ]
                    }
                ]
            }
        )
    )
    assert playwright_timed_out(report) is expected


@pytest.mark.parametrize(
    "message",
    [
        "Timed out waiting 1800000ms from config.webServer.",
        "Timed out waiting 1800s for the run to run",
        "assertion failed: timed out waiting for the UI",
        "Timed out waiting 1800000ms from user code.",
    ],
)
def test_playwright_root_error_text_does_not_claim_structured_timeout(tmp_path, message):
    report = tmp_path / "playwright.json"
    report.write_text(json.dumps({"suites": [], "errors": [{"message": message}]}))
    assert playwright_timed_out(report) is False


def test_direct_evidence_consumer_refuses_timeout_marker(tmp_path):
    marker_directory = tmp_path / "artifacts"
    marker_directory.mkdir()
    (marker_directory / "vitest-incomplete.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "incomplete",
                "framework": "vitest",
                "reason": "owner_watchdog",
                "phase": "execution",
            }
        )
    )
    with pytest.raises(CommandIncompleteError, match="tool_command_watchdog_exhausted"):
        record(tmp_path, "test", {})


def test_malformed_incomplete_marker_is_refused(tmp_path):
    marker_directory = tmp_path / "artifacts"
    marker_directory.mkdir()
    (marker_directory / "vitest-incomplete.json").write_text(
        json.dumps({"status": "incomplete", "framework": "vitest"})
    )
    with pytest.raises(ValueError, match="framework_incomplete_marker_invalid"):
        incomplete_marker(tmp_path, "vitest")


@pytest.mark.parametrize(
    ("framework", "reason", "phase", "schema_version"),
    [
        ("vitest", "owner_watchdog", "execution", True),
        ("vitest", "playwright_timedout", "execution", 1),
    ],
)
def test_incomplete_marker_rejects_python_boolean_and_wrong_framework(
    tmp_path, framework, reason, phase, schema_version
):
    marker_directory = tmp_path / "artifacts"
    marker_directory.mkdir()
    (marker_directory / f"{framework}-incomplete.json").write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "status": "incomplete",
                "framework": framework,
                "reason": reason,
                "phase": phase,
            }
        )
    )
    with pytest.raises(ValueError, match="framework_incomplete_marker_invalid"):
        incomplete_marker(tmp_path, framework)


def test_old_vitest_timeout_error_text_remains_an_ordinary_failed_test(tmp_path):
    source = tmp_path / "packages/react/test/example.test.tsx"
    source.parent.mkdir(parents=True)
    source.write_text("export {};\n")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "vitest-report.json").write_text(
        json.dumps(
            {
                "testResults": [
                    {
                        "name": str(source),
                        "assertionResults": [
                            {
                                "fullName": "ordinary assertion",
                                "status": "failed",
                                "failureMessages": ["Error: Test timed out in 30000ms."],
                            }
                        ],
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="required_browser_or_JS_tests_not_all_passed"):
        record(tmp_path, "test", {})


def test_output_capture_marker_is_typed_incomplete(tmp_path):
    marker_directory = tmp_path / "artifacts"
    marker_directory.mkdir()
    (marker_directory / "vitest-incomplete.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "incomplete",
                "framework": "vitest",
                "reason": "output_capture_failed",
                "phase": "execution",
            }
        )
    )
    with pytest.raises(CommandIncompleteError, match="tool_command_output_capture_failed"):
        record(tmp_path, "test", {})


def test_gate_command_owns_its_process_group(monkeypatch):
    runner = AsyncMock(return_value=Result(0, "", ""))
    monkeypatch.setattr(web_quality, "run_command", runner)
    assert asyncio.run(web_quality.command(["/usr/bin/npm", "run", "test"])) == 0
    runner.assert_awaited_once_with(
        ["/usr/bin/npm", "run", "test"],
        capture=False,
        deadline_seconds=web_quality.DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
        own_process_group=True,
    )


def test_platform_requires_an_explicit_selected_profile(tmp_path, monkeypatch):
    runner = AsyncMock(return_value=0)
    monkeypatch.setattr(web_quality, "command", runner)
    assert asyncio.run(web_quality.simple_gate(tmp_path, "platform-check", None)) == 1
    runner.assert_not_awaited()


def test_aggregate_failure_and_migration_debt_refuse_release(tmp_path, monkeypatch):
    monkeypatch.setattr(web_quality, "run_npm", AsyncMock(return_value=0))
    monkeypatch.setattr(web_quality, "record_aggregate", AsyncMock())
    check = AsyncMock(return_value=4)
    monkeypatch.setattr(web_quality.quality, "check", check)
    assert asyncio.run(web_quality.complete_gate(tmp_path, "release-check", "HEAD", "dev")) == 4
    check.return_value = 0
    monkeypatch.setattr(web_quality, "command", AsyncMock(return_value=0))
    monkeypatch.setattr(web_quality, "run_npm", AsyncMock(return_value=0))
    monkeypatch.setattr(web_quality, "run_native_owner_tests", AsyncMock(return_value=0))
    monkeypatch.setattr(web_quality, "load_debt", lambda _: {"legacy.py": frozenset({"private"})})
    platform = AsyncMock(return_value=0)
    monkeypatch.setattr(web_quality, "simple_gate", platform)
    assert asyncio.run(web_quality.complete_gate(tmp_path, "release-check", "HEAD", "dev")) == 1
    platform.assert_not_awaited()


@pytest.mark.parametrize("failure", ["collect", "batch"])
def test_selected_suite_child_failure_is_not_a_pass(tmp_path, monkeypatch, failure):
    folder = tmp_path / "tests/client"
    folder.mkdir(parents=True)
    (folder / "test_client.py").write_text("def test_client(): pass")
    monkeypatch.chdir(tmp_path)
    stages = []

    async def child(arguments, **kwargs):
        if arguments[1].endswith("collect_tests.py"):
            stages.append("collect")
            await asyncio.to_thread(
                Path(arguments[2]).write_text,
                json.dumps(["tests/client/test_client.py::test_client"]),
            )
            code = 6 if failure == "collect" else 0
        else:
            stages.append("batch")
            assert kwargs["deadline_seconds"] == 30 * 60
            assert kwargs["own_process_group"] is True
            code = 6 if failure == "batch" else 0
        if arguments[1].endswith("collect_tests.py"):
            assert kwargs["deadline_seconds"] == 30 * 60
            assert kwargs["own_process_group"] is True
        return Result(code, "", "")

    monkeypatch.setattr(web_suites, "run_command", child)
    assert asyncio.run(web_suites.run_selected(("tests/client",))) == 6
    assert stages == (["collect"] if failure == "collect" else ["collect", "batch"])


def test_selected_suite_runs_exact_real_collected_tests(tmp_path, monkeypatch):
    folder = tmp_path / "tests"
    folder.mkdir()
    (folder / "test_real.py").write_text("def test_real():\n    assert 2 + 2 == 4\n")
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(web_suites.run_selected(("tests/test_*.py",))) == 0


def test_main_reports_missing_input_as_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["web-quality", "api-contract"])
    assert asyncio.run(web_quality.main()) == 1
