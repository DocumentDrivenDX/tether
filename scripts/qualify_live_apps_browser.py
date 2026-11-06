"""Bound and sanitize failures from the optional live Playwright child."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from qualify_live_apps_evidence import LIVE_CASE_IDS

OUTPUT_LIMIT = 256 * 1024
CASE_STATUSES = {"passed", "failed", "timedOut", "skipped", "interrupted"}


class LiveBrowserFailure(RuntimeError):
    """A fixed, content-free failure summary for the opt-in browser process."""

    def __init__(self, summary: dict[str, Any]) -> None:
        self.summary = summary
        super().__init__("live_browser_child_failed")


async def capture_output(reader: asyncio.StreamReader) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := await reader.read(4096):
        remaining = OUTPUT_LIMIT - size
        if remaining > 0:
            kept = chunk[:remaining]
            chunks.append(kept)
            size += len(kept)
        if len(chunk) > max(remaining, 0):
            truncated = True
    return b"".join(chunks), truncated


def failure_summary(
    report_path: Path,
    *,
    exit_code: int | None,
    stdout: bytes,
    stderr: bytes,
    output_truncated: bool,
) -> dict[str, Any]:
    cases, report_state = _observed_cases(report_path)
    category = _failure_category(report_state, cases, stdout + b"\n" + stderr)
    return {
        "status": "failed",
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        "error_category": category,
        "output_truncated": output_truncated,
        "cases": cases,
    }


async def browser_failure(
    process: asyncio.subprocess.Process,
    report_path: Path,
    output_tasks: tuple[asyncio.Task[tuple[bytes, bool]], ...],
) -> LiveBrowserFailure:
    captured: list[tuple[bytes, bool]] = []
    truncated = False
    if output_tasks:
        try:
            async with asyncio.timeout(3):
                captured = list(await asyncio.gather(*output_tasks))
        except TimeoutError:
            truncated = True
            for task in output_tasks:
                if not task.done():
                    task.cancel()
            partial = await asyncio.gather(*output_tasks, return_exceptions=True)
            captured = [item for item in partial if isinstance(item, tuple)]
    return LiveBrowserFailure(
        failure_summary(
            report_path,
            exit_code=process.returncode,
            stdout=captured[0][0] if captured else b"",
            stderr=captured[1][0] if len(captured) > 1 else b"",
            output_truncated=truncated or any(item[1] for item in captured),
        )
    )


def _observed_cases(report_path: Path) -> tuple[list[dict[str, str]], str]:
    if not report_path.is_file():
        return _missing_cases(), "missing"
    try:
        with report_path.open("rb") as report_file:
            raw = report_file.read(OUTPUT_LIMIT + 1)
        if len(raw) > OUTPUT_LIMIT:
            return _missing_cases(), "oversized"
        report = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return _missing_cases(), "invalid"
    if not isinstance(report, dict) or not isinstance(report.get("suites"), list):
        return _missing_cases(), "invalid"

    statuses: dict[str, str] = {}
    _collect_suites(report["suites"], statuses)
    results = [
        {"node_id": node_id, "status": statuses.get(node_id, "not_observed")}
        for node_id in LIVE_CASE_IDS
    ]
    return results, "valid"


def _missing_cases() -> list[dict[str, str]]:
    return [{"node_id": node_id, "status": "not_observed"} for node_id in LIVE_CASE_IDS]


def _collect_suites(suites: list[Any], statuses: dict[str, str]) -> None:
    for suite in suites:
        if not isinstance(suite, dict):
            continue
        nested = suite.get("suites")
        if isinstance(nested, list):
            _collect_suites(nested, statuses)
        specs = suite.get("specs")
        if not isinstance(specs, list):
            continue
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            title, filename = spec.get("title"), spec.get("file", suite.get("file"))
            if not isinstance(title, str) or not isinstance(filename, str):
                continue
            node_id = _case_id(filename, title)
            if node_id not in LIVE_CASE_IDS:
                continue
            tests = spec.get("tests")
            statuses[node_id] = _spec_status(tests if isinstance(tests, list) else [])


def _case_id(filename: str, title: str) -> str:
    if filename.replace("\\", "/").endswith("tests/ui/databricks-live.spec.ts"):
        return f"tests/ui/databricks-live.spec.ts::{title}"
    return ""


def _spec_status(tests: list[Any]) -> str:
    observed: list[str] = []
    for test in tests:
        if not isinstance(test, dict):
            continue
        attempts = test.get("results")
        if not isinstance(attempts, list):
            continue
        observed.extend(
            attempt["status"]
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("status") in CASE_STATUSES
        )
    if not observed:
        return "not_observed"
    if all(status == "passed" for status in observed):
        return "passed"
    for status in ("timedOut", "failed", "interrupted", "skipped"):
        if status in observed:
            return status
    return "not_observed"


def _failure_category(report_state: str, cases: list[dict[str, str]], output: bytes) -> str:
    lowered = output.lower()
    patterns = (
        (
            "playwright_configuration",
            (b"error in config file", b"live_profile_configuration_missing", b"no tests found"),
        ),
        (
            "browser_launch",
            (b"browsertype.launch", b"executable doesn't exist", b"failed to launch browser"),
        ),
        ("navigation_failure", (b"net::err_", b"navigation failed", b"page.goto")),
        ("timeout", (b"test timeout of", b"timed out", b"timeout ")),
    )
    marker_category = next(
        (
            category
            for category, markers in patterns
            if any(marker in lowered for marker in markers)
        ),
        None,
    )
    case_category = next(
        (
            category
            for status, category in (("timedOut", "timeout"), ("failed", "test_failure"))
            if any(case["status"] == status for case in cases)
        ),
        None,
    )
    report_category = {
        "invalid": "report_invalid",
        "oversized": "report_oversized",
        "missing": "report_missing",
    }.get(report_state)
    return marker_category or case_category or report_category or "unknown_failure"
