"""Privacy and bounds for optional live browser failure diagnostics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from qualify_live_apps_browser import (
    OUTPUT_LIMIT,
    capture_output,
    failure_summary,
)
from qualify_live_apps_evidence import LIVE_CASE_IDS


def _report(*, second_status: str = "not observed", error: str = "") -> dict[str, object]:
    return {
        "suites": [
            {
                "file": "tests/ui/databricks-live.spec.ts",
                "specs": [
                    {
                        "title": node_id.split("::", 1)[1],
                        "tests": [{"results": [{"status": "passed"}]}],
                    }
                    for node_id in LIVE_CASE_IDS[:1]
                ]
                + [
                    {
                        "title": LIVE_CASE_IDS[1].split("::", 1)[1],
                        "tests": [
                            {"results": [{"status": second_status, "error": {"message": error}}]}
                        ],
                    }
                ],
            }
        ]
    }


def test_failure_summary_reports_exact_cases_without_report_content(tmp_path: Path) -> None:
    marker = "PRIVATE_MARKER_" + "approval-content"
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(second_status="failed", error=marker)))

    summary = failure_summary(
        report,
        exit_code=1,
        stdout=b"",
        stderr=("Error: " + marker).encode(),
        output_truncated=False,
    )

    assert summary["status"] == "failed"
    assert summary["exit_code"] == 1
    assert summary["error_category"] == "test_failure"
    assert summary["cases"] == [
        {"node_id": LIVE_CASE_IDS[0], "status": "passed"},
        {"node_id": LIVE_CASE_IDS[1], "status": "failed"},
    ]
    assert marker not in json.dumps(summary)


@pytest.mark.parametrize(
    ("report_state", "report_bytes", "expected"),
    [
        ("missing", None, "report_missing"),
        ("invalid", b"not-json", "report_invalid"),
        ("oversized", b"x" * (OUTPUT_LIMIT + 1), "report_oversized"),
    ],
    ids=("missing-report", "invalid-json", "oversized-report"),
)
def test_missing_or_untrusted_report_has_fixed_category(
    tmp_path: Path,
    report_state: str,
    report_bytes: bytes | None,
    expected: str,
) -> None:
    report = tmp_path / "report.json"
    if report_bytes is not None:
        report.write_bytes(report_bytes)

    summary = failure_summary(
        report,
        exit_code=2,
        stdout=b"",
        stderr=b"",
        output_truncated=False,
    )

    assert summary["error_category"] == expected
    assert all(case["status"] == "not_observed" for case in summary["cases"])
    assert report_state in {"missing", "invalid", "oversized"}


def test_error_category_uses_allowlisted_launch_class_only() -> None:
    marker = "PRIVATE_MARKER_" + "url-and-token"
    summary = failure_summary(
        Path("/path/that/does/not/exist"),
        exit_code=1,
        stdout=b"",
        stderr=("BrowserType.launch: executable doesn't exist " + marker).encode(),
        output_truncated=False,
    )

    assert summary["error_category"] == "browser_launch"
    assert marker not in json.dumps(summary)
    assert "/path/that/does/not/exist" not in json.dumps(summary)


@pytest.mark.parametrize(
    ("output", "category"),
    [
        (b"Error in config file: PRIVATE_MARKER", "playwright_configuration"),
        (b"page.goto: Navigation failed at PRIVATE_MARKER", "navigation_failure"),
        (b"Test timeout of 120000ms exceeded PRIVATE_MARKER", "timeout"),
    ],
)
def test_browser_output_is_reduced_to_fixed_failure_categories(
    tmp_path: Path, output: bytes, category: str
) -> None:
    summary = failure_summary(
        tmp_path / "absent-report.json",
        exit_code=1,
        stdout=output,
        stderr=b"",
        output_truncated=False,
    )

    assert summary["error_category"] == category
    assert "PRIVATE_MARKER" not in json.dumps(summary)


@pytest.mark.anyio
async def test_capture_output_drains_beyond_limit_without_retaining_it() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"z" * (OUTPUT_LIMIT + 128))
    reader.feed_eof()

    captured, truncated = await capture_output(reader)

    assert len(captured) == OUTPUT_LIMIT
    assert truncated is True
    assert reader.at_eof()
