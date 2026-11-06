"""Write source-bound, content-free evidence for a live Apps run."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browser_evidence import playwright_results
from qualify_live_apps_observation import observation_watchdog_seconds
from qualify_live_apps_process import capture_owned_command
from web_artifacts import PROJECTS, inventory, write_receipt
from web_transport_receipt import validate_observer, validate_shutdown_observation

ROOT = Path(__file__).resolve().parents[1]
LIVE_CASE_IDS = (
    "tests/ui/databricks-live.spec.ts::"
    "@covers US-068-AC1 @covers US-068-AC5 live browser, remote TUI and one approval winner",
    "tests/ui/databricks-live.spec.ts::"
    "@covers US-068-AC8 live bounded stop retains unknown effect barrier after restart",
)


async def _tool_version(name: str, source: dict[str, str]) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError("qualification_runtime_missing")
    environment = {
        key: source[key] for key in ("PATH", "HOME", "TMPDIR", "TEMP", "TMP") if source.get(key)
    }
    returncode, stdout = await capture_owned_command(
        executable,
        "--version",
        environment=environment,
        timeout_seconds=observation_watchdog_seconds(),
        incomplete_reason="live_runtime_metadata_not_observed",
    )
    if returncode != 0 or stdout is None:
        raise RuntimeError("qualification_runtime_version_unavailable")
    return stdout.decode("utf-8", errors="replace").strip()


def _runtime_version(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError("qualification_runtime_version_invalid")
    if len(value) > 80 or not re.fullmatch(r"[A-Za-z0-9 .+_-]+", value):
        raise RuntimeError("qualification_runtime_version_invalid")
    return value


async def _versions(source: dict[str, str], browser_runtime_path: Path) -> dict[str, object]:
    browser_runtime = json.loads(await asyncio.to_thread(browser_runtime_path.read_text))
    if not isinstance(browser_runtime, dict):
        raise RuntimeError("qualification_browser_runtime_missing")
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "node": await _tool_version("node", source),
        "npm": await _tool_version("npm", source),
        "databricks_cli": _runtime_version(await _tool_version("databricks", source)),
        "playwright": _runtime_version(browser_runtime.get("playwright")),
        "chromium": _runtime_version(browser_runtime.get("chromium")),
    }


def _app_runtime(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError("qualification_runtime_versions_missing")
    result: dict[str, object] = {
        name: _runtime_version(value[name])
        for name in ("python", "platform", "architecture")
        if name in value
    }
    distributions = value.get("distributions")
    if isinstance(distributions, dict):
        names = {*PROJECTS, "hypercorn", "databricks-sdk", "psycopg", "psycopg-binary"}
        result["distributions"] = {
            name: _runtime_version(distributions[name])
            for name in sorted(names)
            if name in distributions
        }
    return result


def _browser_cases(report: Path) -> list[dict[str, object]]:
    results = playwright_results(report, ROOT)
    observed = {str(result["node_id"]) for result in results}
    if observed != set(LIVE_CASE_IDS) or any(result["outcome"] != "passed" for result in results):
        raise RuntimeError("live_browser_required_cases_not_all_passed")
    return results


def validate_receipt_output(output: Path) -> None:
    resolved = output.resolve()
    root = ROOT.resolve()
    if root in resolved.parents and "artifacts" not in resolved.relative_to(root).parts:
        raise RuntimeError("qualification_receipt_path_must_be_external_or_ignored_artifact")
    if resolved.exists():
        raise RuntimeError("qualification_receipt_already_exists")


def profile_binding_sha256(profile_name: str, app_origin: str) -> str:
    binding = profile_name + "\n" + app_origin
    return hashlib.sha256(binding.encode("utf-8")).hexdigest()


async def write_live_receipt(
    output: Path,
    source_before: dict[str, str],
    source: dict[str, str],
    report_path: Path,
    browser_runtime_path: Path,
    shutdown: dict[str, Any],
    *,
    profile_name: str,
    app_origin: str,
    service_caller: bool,
) -> None:
    try:
        transport = await asyncio.to_thread(validate_shutdown_observation, ROOT, shutdown)
    except ValueError:
        raise RuntimeError("qualification_shutdown_evidence_incomplete") from None
    elapsed = shutdown["signal_to_report_seconds"]
    configured = shutdown["configured_shutdown_seconds"]
    outstanding = shutdown["outstanding"]
    http_versions = shutdown["asgi_http_versions"]
    app_versions = _app_runtime(shutdown.get("runtime"))
    runtime_versions = await _versions(source, browser_runtime_path)
    test_results = _browser_cases(report_path)
    source_after = await asyncio.to_thread(inventory, ROOT)
    if source_before != source_after:
        raise RuntimeError("qualification_source_changed_during_live_run")
    receipt = {
        "status": "passed",
        "schema_version": 1,
        "profile": "selected-databricks-apps",
        "auth_mode": "service-caller" if service_caller else "human-bearer",
        "profile_binding_sha256": profile_binding_sha256(profile_name, app_origin),
        "command": (
            "python scripts/qualify_live_apps.py --profile <caller-profile> "
            "--admin-profile <admin-profile> --output <receipt-path>"
        ),
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": source_before,
        "source_sha256_after": source_after,
        "versions": runtime_versions,
        "test_results": test_results,
        "shutdown": {
            "complete": False,
            "outstanding": sorted(outstanding),
            "signal_name": "SIGTERM",
            "signal_to_report_seconds": elapsed,
            "configured_shutdown_seconds": configured,
            "asgi_http_versions": sorted(http_versions),
            "runtime": app_versions,
            "main_returned": True,
            "signal_to_main_return_seconds": shutdown["signal_to_main_return_seconds"],
            "worker_invocation_id": shutdown["worker_invocation_id"],
            "transport": transport,
            "observer": validate_observer(shutdown["observer"]),
        },
        "limits": [
            "One explicitly selected Databricks Apps deployment and caller profile",
            "Fabricated browser, remote-client, and tool interaction only",
            "Temporary instrumented qualification observer; candidate modules byte-identical",
            "Deployed-listener H2C capability and actual proxy protocol reported separately",
        ],
    }
    await asyncio.to_thread(write_receipt, output, receipt)
