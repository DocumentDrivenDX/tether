"""Sanitize observed JS/browser reports; never copy config, paths or captured content."""

from __future__ import annotations

import json
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from process import CommandIncompleteError, CommandPhase, CommandReason
from web_artifacts import inventory, write_receipt

INCOMPLETE_REASONS: dict[str, tuple[CommandReason, CommandPhase]] = {
    "owner_watchdog": ("watchdog_exhausted", "execution"),
    "cleanup_unconfirmed": ("cleanup_unconfirmed", "cleanup"),
    "output_capture_failed": ("output_capture_failed", "execution"),
    "unsupported_platform": ("unsupported_platform", "spawn"),
    "playwright_timedout": ("watchdog_exhausted", "execution"),
    "dispatch_failed": ("dispatch_failed", "spawn"),
    "owned_group_not_empty": ("owned_group_not_empty", "cleanup"),
}


def _timeout_marker(root: Path, framework: str) -> Path:
    return root / "artifacts" / f"{framework}-incomplete.json"


def source_path(root: Path, value: str) -> str:
    path = Path(value)
    full = path.resolve() if path.is_absolute() else (root / path).resolve()
    if root.resolve() not in full.parents or not full.is_file():
        raise ValueError("observed_test_source_outside_project_or_missing")
    return str(full.relative_to(root.resolve()))


def playwright_results(path: Path, root: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    results = []
    report_root = Path(data.get("config", {}).get("rootDir", root))

    def visit(suites: list[dict]) -> None:
        for suite in suites:
            visit(suite.get("suites", []))
            for spec in suite.get("specs", []):
                filename = spec.get("file", suite.get("file", ""))
                selected = Path(filename)
                if not selected.is_absolute() and not (root / selected).is_file():
                    selected = report_root / selected
                file = source_path(root, str(selected))
                for test in spec.get("tests", []):
                    attempts = test.get("results", [])
                    passed = attempts and all(
                        attempt.get("status") == "passed" for attempt in attempts
                    )
                    identity = file + "::" + spec["title"]
                    if test.get("projectName"):
                        identity += "::" + test["projectName"]
                    results.append(
                        {
                            "node_id": identity,
                            "outcome": "passed"
                            if passed and test.get("expectedStatus") == "passed"
                            else "failed",
                            "duration_ms": sum(attempt.get("duration", 0) for attempt in attempts),
                        }
                    )

    visit(data.get("suites", []))
    return unique(results)


def playwright_timed_out(path: Path) -> bool:
    """Use only Playwright's structured test-attempt or full-run status."""
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("suites"), list):
        return False
    return data.get("status") == "timedout" or _suites_timed_out(data["suites"])


def _suites_timed_out(suites: list[dict]) -> bool:
    for suite in suites:
        nested = suite.get("suites", [])
        if isinstance(nested, list) and _suites_timed_out(nested):
            return True
        for spec in suite.get("specs", []):
            if not isinstance(spec, dict):
                continue
            for test in spec.get("tests", []):
                if not isinstance(test, dict):
                    continue
                for attempt in test.get("results", []):
                    if isinstance(attempt, dict) and attempt.get("status") == "timedOut":
                        return True
    return False


def vitest_results(path: Path, root: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    results = []
    for file in data.get("testResults", []):
        source = source_path(root, file["name"])
        for assertion in file.get("assertionResults", []):
            results.append(
                {
                    "node_id": source + "::" + assertion["fullName"],
                    "outcome": assertion.get("status", "missing"),
                    "duration_ms": assertion.get("duration", 0),
                }
            )
    return unique(results)


def package_results(path: Path, root: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    if data.get("status") != "passed":
        raise ValueError("package_checks_not_passed")
    expected = {
        "scripts/web/check-packages.mjs::" + case
        for case in (
            "framework-free-import",
            "reproducible-archives",
            "typed-react-slots",
            "text-escaping",
        )
    }
    results = []
    for case in data.get("test_results", []):
        node = case.get("node_id")
        if not isinstance(node, str) or node not in expected:
            raise ValueError("unexpected_package_check_identity")
        source_path(root, node.split("::", 1)[0])
        results.append({"node_id": node, "outcome": case.get("outcome", "missing")})
    if {case["node_id"] for case in results} != expected:
        raise ValueError("missing_required_package_check_identity")
    return unique(results)


def observed_sbom(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text()).get("sbom")
    if (
        not isinstance(value, dict)
        or value.get("bomFormat") != "CycloneDX"
        or value.get("specVersion") != "1.6"
        or not value.get("components")
        or not value.get("dependencies")
    ):
        raise ValueError("actual_installed_package_sbom_required")
    if re.search(r"file:|/private/|/Users/|[A-Z]:\\", json.dumps(value), re.I):
        raise ValueError("private_location_in_package_sbom")
    return value


def unique(results: list[dict[str, object]]) -> list[dict[str, object]]:
    identifiers = [result["node_id"] for result in results]
    if not results or len(identifiers) != len(set(identifiers)):
        raise ValueError("empty_or_duplicate_observed_browser_tests")
    return results


def incomplete_marker(root: Path, framework: str) -> tuple[CommandReason, CommandPhase] | None:
    path = _timeout_marker(root, framework)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "status", "framework", "reason", "phase"}
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != 1
        or data.get("status") != "incomplete"
        or data.get("framework") != framework
        or data.get("reason") not in INCOMPLETE_REASONS
        or (data.get("reason") == "playwright_timedout" and framework != "playwright")
        or (framework == "packages" and data.get("reason") == "playwright_timedout")
    ):
        raise ValueError("framework_incomplete_marker_invalid")
    expected_reason, expected_phase = INCOMPLETE_REASONS[data["reason"]]
    if data.get("phase") != expected_phase:
        raise ValueError("framework_incomplete_marker_invalid")
    return expected_reason, expected_phase


def record(root: Path, script: str, before: dict[str, str]) -> Path:
    parsers = {
        "test": (vitest_results, "vitest-report.json", "JS-unit"),
        "test:e2e": (playwright_results, "browser-report.json", "local-browser"),
        "check:packages": (package_results, "npm-package-report.json", "installed-npm-packages"),
    }
    parser, filename, profile = parsers[script]
    report = root / "artifacts" / filename
    if script in {"test", "test:e2e"}:
        framework = "vitest" if script == "test" else "playwright"
        marker = incomplete_marker(root, framework)
        if marker is not None:
            raise CommandIncompleteError(*marker)
        if script == "test:e2e" and report.is_file() and playwright_timed_out(report):
            raise CommandIncompleteError("watchdog_exhausted", "execution")
    results = parser(report, root)
    if not all(result["outcome"] == "passed" for result in results):
        raise ValueError("required_browser_or_JS_tests_not_all_passed")
    if before != inventory(root):
        raise ValueError("source_changed_during_browser_or_JS_acceptance")
    with tempfile.NamedTemporaryFile(
        prefix="tether-js-evidence-", suffix=".json", delete=False
    ) as file:
        destination = Path(file.name)
    write_receipt(
        destination,
        {
            "status": "passed",
            "schema_version": 1,
            "command": "npm run " + script,
            "recorded_at": datetime.now(UTC).isoformat(),
            "source_sha256": before,
            "test_results": results,
            "profile": profile,
            **(
                {"sbom": observed_sbom(root / "artifacts" / filename)}
                if script == "check:packages"
                else {}
            ),
            "limits": ["Observed named checks only; no live-platform inference"],
        },
    )
    print(f"Observed JavaScript evidence: {destination}", flush=True)
    return destination
