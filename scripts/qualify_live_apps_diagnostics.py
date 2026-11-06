"""Read bounded, sanitized browser rendezvous diagnostics for live qualification."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_observation import observe_until


async def read_rendezvous(
    path: Path, name: str, *, process: asyncio.subprocess.Process
) -> dict[str, Any]:
    target = path / name
    try:
        async with observe_until("live_browser_rendezvous_not_observed"):
            while True:
                if target.exists():
                    value = json.loads(await asyncio.to_thread(target.read_text))
                    if isinstance(value, dict):
                        return value
                    raise RuntimeError("live_browser_rendezvous_invalid")
                if process.returncode is not None:
                    raise RuntimeError("live_browser_exited_before_rendezvous")
                await asyncio.sleep(0.05)
    except LiveQualificationIncomplete as error:
        scenario = "unknown" if name.startswith("unknown-") else "shared"
        stage = await _browser_stage(path, scenario)
        routes = await _safe_network_summary(path, scenario)
        outcome = await _safe_test_outcome(path, scenario)
        detail = f"{scenario}_{name}_after_{stage}_http_{routes}_test_{outcome}"
        raise LiveQualificationIncomplete(error.reason, detail=detail) from None


async def _browser_stage(path: Path, scenario: str) -> str:
    stage_path = path / f"browser-stage-{scenario}.json"
    if stage_path.exists():
        try:
            marker = json.loads(await asyncio.to_thread(stage_path.read_text))
            candidate = marker.get("value") if isinstance(marker, dict) else None
            if candidate in {"page_loaded", "session_created", "message_sent", "pending_loaded"}:
                return candidate
        except (OSError, json.JSONDecodeError):
            pass
    return "unknown"


def _safe_http_event(item: object) -> str | None:
    if not isinstance(item, dict):
        return None
    method, route, status = item.get("method"), item.get("route"), item.get("status")
    methods = {"GET", "POST", "PATCH", "DELETE"}
    routes = {"capabilities", "sessions", "pending", "confirmation", "turns", "effects", "other"}
    if method not in methods or route not in routes or not isinstance(status, int):
        return None
    if not 100 <= status <= 599:
        return None
    return f"{route}-{method}-{status}"


async def _safe_network_summary(path: Path, scenario: str) -> str:
    network_path = path / f"browser-network-{scenario}.json"
    if network_path.exists():
        try:
            value = json.loads(await asyncio.to_thread(network_path.read_text))
            requests = value.get("requests") if isinstance(value, dict) else None
            if isinstance(requests, list):
                return _count_safe_events(requests)
        except (OSError, json.JSONDecodeError):
            pass
    return "none"


def _count_safe_events(requests: list[object]) -> str:
    counts: dict[str, int] = {}
    for item in requests:
        event = _safe_http_event(item)
        if event is not None:
            counts[event] = counts.get(event, 0) + 1
    safe = [f"{event}-count-{counts[event]}" for event in sorted(counts)[:20]]
    return ",".join(safe) or "none"


async def _safe_test_outcome(path: Path, scenario: str) -> str:
    outcome_path = path / f"browser-outcome-{scenario}.json"
    if not outcome_path.exists():
        return "not-finished"
    try:
        value = json.loads(await asyncio.to_thread(outcome_path.read_text))
    except (OSError, json.JSONDecodeError):
        return "invalid"
    if not isinstance(value, dict):
        return "invalid"

    return _summarize_outcome(value)


def _summarize_outcome(value: dict[str, Any]) -> str:
    title = value.get("title")
    status, expected = value.get("status"), value.get("expected")
    error_count = value.get("error_count")
    category, line = value.get("failure_category"), value.get("failure_line")
    statuses = {"passed", "failed", "timedOut", "skipped", "interrupted"}
    expected_titles = {
        "@covers US-068-AC1 @covers US-068-AC5 live browser, remote TUI "
        "and one approval winner": "AC1-AC5",
        "@covers US-068-AC8 live bounded stop retains unknown effect barrier after restart": "AC8",
    }
    if title not in expected_titles or status not in statuses or expected not in statuses:
        return "invalid"
    if not isinstance(error_count, int) or not 0 <= error_count <= 10:
        return "invalid"
    categories = {"none", "ambiguous_locator", "timeout", "assertion_or_runtime"}
    valid_line = line is None or (isinstance(line, int) and 1 <= line <= 1200)
    if category not in categories or not valid_line:
        return "invalid"
    return (
        f"{expected_titles[title]}-{status}-{expected}-errors-{error_count}-"
        f"{category}-line-{line or 0}"
    )
