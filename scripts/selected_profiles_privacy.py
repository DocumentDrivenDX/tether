"""Validate actual APP and unfiltered platform logs against fresh request metadata."""

from __future__ import annotations

import json
import re
import uuid
from urllib.parse import unquote, urlsplit

import httpx
from selected_profiles_privacy_types import PrivacyObservationIncomplete, PrivacyRejection

_REQUEST = re.compile(
    r'"(?P<method>[A-Z]+) (?P<target>[^ "\r\n]{1,8192}) HTTP/[0-9.]+" '
    r"(?P<status>[1-5][0-9]{2})(?: |$)"
)


def _entries(value: str) -> list[tuple[str, str]]:
    result = []
    try:
        for line in value.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError
            source, message = item.get("source"), item.get("message")
            if not isinstance(source, str) or not isinstance(message, str):
                raise ValueError
            result.append((source, message))
    except (TypeError, ValueError):
        raise PrivacyObservationIncomplete("platform_log_format_invalid") from None
    return result


def validate_privacy(
    app_logs: str,
    platform_logs: str,
    content_markers: tuple[str, ...],
    credential_marker: str,
    invocation_id: str,
    *,
    success_session_id: str,
    rejected_session_id: str,
    api_prefix: str,
    diagnostics: tuple[str, ...] = (),
) -> None:
    if (
        not content_markers
        or any(not marker for marker in content_markers)
        or not credential_marker
        or not invocation_id
        or not success_session_id
        or not rejected_session_id
    ):
        raise RuntimeError("privacy_markers_missing")
    if not app_logs.strip() or not platform_logs.strip():
        raise PrivacyObservationIncomplete("platform_log_stream_empty")
    for value in (app_logs, platform_logs, *diagnostics):
        if any(marker in value for marker in content_markers) or credential_marker in value:
            raise RuntimeError("privacy_marker_found_in_platform_logs")
    app_entries = _entries(app_logs)
    platform_entries = _entries(platform_logs)
    worker = f"TETHER_QUALIFICATION_WORKER_STARTED {invocation_id}"
    if not any(source == "APP" and worker in message for source, message in app_entries):
        raise PrivacyObservationIncomplete("privacy_app_window_not_anchored")
    if not all(
        _http_anchors(platform_entries, success_session_id, rejected_session_id, api_prefix)
    ):
        raise PrivacyObservationIncomplete("privacy_platform_window_not_anchored")


def _http_anchors(
    entries: list[tuple[str, str]],
    success_session_id: str,
    rejected_session_id: str,
    api_prefix: str,
) -> tuple[bool, bool]:
    success, rejected = False, False
    successful_path = f"{api_prefix}/sessions/{success_session_id}"
    rejected_path = f"{api_prefix}/sessions/{rejected_session_id}/turns"
    for source, message in entries:
        if source != "HTTP":
            continue
        match = _REQUEST.search(message)
        if match is None:
            continue
        try:
            path = unquote(urlsplit(match["target"]).path)
        except ValueError:
            continue
        status = int(match["status"])
        if 200 <= status < 300 and (
            path == successful_path or path.startswith(successful_path + "/")
        ):
            success = True
        if match["method"] == "POST" and path == rejected_path and status in {401, 403}:
            rejected = True
    return success, rejected


async def rejected_request(origin: str, prefix: str, authorization: str) -> PrivacyRejection:
    content = " ".join(("fabricated", "content", uuid.uuid4().hex))
    credential = "-".join(("fabricated", "credential", uuid.uuid4().hex))
    session_id = uuid.uuid4().hex
    async with httpx.AsyncClient(
        base_url=origin, timeout=30, http2=True, trust_env=False
    ) as client:
        response = await client.post(
            prefix + "/sessions/" + session_id + "/turns",
            json={"request_id": uuid.uuid4().hex, "text": content},
            headers={
                "Authorization": authorization,
                "X-Tether-Service-Caller-Authorization": " ".join(("Bearer", credential)),
            },
        )
    if response.status_code not in {401, 403}:
        raise RuntimeError("privacy_probe_was_not_refused")
    return PrivacyRejection(content, credential, session_id)
