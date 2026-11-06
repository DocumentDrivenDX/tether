"""Bounded APP and unfiltered platform log subscriptions for live privacy evidence."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from selected_profiles_privacy import _http_anchors
from selected_profiles_privacy_types import (
    PrivacyObservationIncomplete,
    PrivacyRejection,
    PrivacyTurn,
    PrivacyWindow,
)
from selected_profiles_process import await_retained


@dataclass
class _Follower:
    process: asyncio.subprocess.Process
    stdout: asyncio.Task[bytes]
    stderr: asyncio.Task[bytes]
    ready: asyncio.Event
    stdout_data: bytearray
    stderr_data: bytearray
    stdout_changed: asyncio.Event
    capture_overflow: asyncio.Event


def _clean_environment(home: str) -> tuple[str, ...]:
    """Build an env -i prefix so inherited Databricks credentials cannot override a profile."""
    return ("/usr/bin/env", "-i", "HOME=" + home, "PATH=/usr/bin:/bin:/usr/sbin:/sbin")


__all__ = ["_clean_environment", "capture_privacy_window"]


async def _read_bounded(
    stream: asyncio.StreamReader,
    ready: asyncio.Event,
    limit: int = 2_000_000,
    *,
    captured: bytearray | None = None,
    changed: asyncio.Event | None = None,
    overflow: asyncio.Event | None = None,
) -> bytes:
    result = bytearray()
    overflowed = False
    try:
        while chunk := await stream.read(32_768):
            if not overflowed:
                remaining = limit - len(result)
                retained = chunk[:remaining]
                result.extend(retained)
                if captured is not None:
                    captured.extend(retained)
                overflowed = len(retained) < len(chunk)
                if overflowed and overflow is not None:
                    overflow.set()
            ready.set()
            if changed is not None:
                changed.set()
        if overflowed:
            raise PrivacyObservationIncomplete("platform_log_capture_limit_exceeded")
        return bytes(result)
    finally:
        ready.set()
        if changed is not None:
            changed.set()


async def _start_follower(profile: str, app_name: str, source: str | None) -> _Follower:
    executable = shutil.which("databricks")
    if executable is None:
        raise PrivacyObservationIncomplete("platform_log_cli_missing")
    command = [
        *_clean_environment(str(Path.home())),
        executable,
        "apps",
        "logs",
        app_name,
        "--tail-lines",
        "1",
        "--follow",
        "--timeout",
        "180s",
        "--profile",
        profile,
        "--output",
        "json",
    ]
    if source is not None:
        command.extend(("--source", source))
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    cancelled_while_spawning = False
    while not spawning.done():
        try:
            await asyncio.shield(spawning)
        except asyncio.CancelledError:
            cancelled_while_spawning = True
    try:
        process = spawning.result()
    except OSError:
        raise PrivacyObservationIncomplete("platform_log_subscription_start_failed") from None
    if process.stdout is None or process.stderr is None:
        process.kill()
        await await_retained(asyncio.create_task(process.wait()))
        if cancelled_while_spawning:
            raise asyncio.CancelledError
        raise PrivacyObservationIncomplete("platform_log_pipe_unavailable")
    ready = asyncio.Event()
    stdout_data = bytearray()
    stderr_data = bytearray()
    stdout_changed = asyncio.Event()
    capture_overflow = asyncio.Event()
    follower = _Follower(
        process,
        asyncio.create_task(
            _read_bounded(
                process.stdout,
                ready,
                captured=stdout_data,
                changed=stdout_changed,
                overflow=capture_overflow,
            )
        ),
        asyncio.create_task(
            _read_bounded(
                process.stderr,
                asyncio.Event(),
                captured=stderr_data,
                changed=stdout_changed,
                overflow=capture_overflow,
            )
        ),
        ready,
        stdout_data,
        stderr_data,
        stdout_changed,
        capture_overflow,
    )
    if cancelled_while_spawning or process.returncode is not None:
        cleanup = asyncio.create_task(_stop_follower(follower))
        await await_retained(cleanup)
        if cancelled_while_spawning:
            raise asyncio.CancelledError
        raise PrivacyObservationIncomplete("platform_log_subscription_did_not_start")
    return follower


async def _stop_follower(follower: _Follower) -> tuple[bytes, bytes]:
    if follower.process.returncode is None:
        follower.process.terminate()
    try:
        await asyncio.wait_for(follower.process.wait(), timeout=5)
    except TimeoutError:
        follower.process.kill()
        await follower.process.wait()
    result: list[bytes] = []
    incomplete = False
    for reader in (follower.stdout, follower.stderr):
        try:
            result.append(await asyncio.wait_for(reader, timeout=5))
        except TimeoutError:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            incomplete = True
        except RuntimeError:
            incomplete = True
            result.append(b"")
    if incomplete:
        raise PrivacyObservationIncomplete("platform_log_capture_incomplete")
    return result[0], result[1]


async def _stop_followers(followers: list[_Follower]) -> list[tuple[bytes, bytes]]:
    results = await asyncio.gather(
        *(_stop_follower(follower) for follower in followers), return_exceptions=True
    )
    if any(isinstance(result, BaseException) for result in results):
        raise PrivacyObservationIncomplete("platform_log_capture_incomplete")
    return [result for result in results if not isinstance(result, BaseException)]


def _complete_log_entries(value: bytes) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in value.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PrivacyObservationIncomplete("platform_log_record_invalid") from None
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("source"), str)
            or not isinstance(item.get("message"), str)
        ):
            raise PrivacyObservationIncomplete("platform_log_record_invalid")
        entries.append((item["source"], item["message"]))
    return entries


async def _wait_for_privacy_witnesses(
    followers: list[_Follower], success: PrivacyTurn, rejected: PrivacyRejection, api_prefix: str
) -> None:
    app_follower, platform_follower = followers
    worker_marker = f"TETHER_QUALIFICATION_WORKER_STARTED {success.invocation_id}"
    private_markers = tuple(
        marker.encode("utf-8")
        for marker in (
            success.content_marker,
            rejected.content_marker,
            rejected.credential_marker,
        )
        if marker
    )
    while True:
        for follower in followers:
            follower.stdout_changed.clear()
        if any(follower.capture_overflow.is_set() for follower in followers):
            raise PrivacyObservationIncomplete("platform_log_capture_limit_exceeded")
        if any(
            marker in value
            for follower in followers
            for value in (bytes(follower.stdout_data), bytes(follower.stderr_data))
            for marker in private_markers
        ):
            raise RuntimeError("privacy_marker_found_in_platform_logs")
        app_entries = _complete_log_entries(bytes(app_follower.stdout_data))
        platform_entries = _complete_log_entries(bytes(platform_follower.stdout_data))
        app_seen = any(
            source == "APP" and worker_marker in message for source, message in app_entries
        )
        success_seen, rejection_seen = _http_anchors(
            platform_entries,
            success.session_id,
            rejected.session_id,
            api_prefix,
        )
        if app_seen and success_seen and rejection_seen:
            if any(
                follower.process.returncode is not None or follower.stdout.done()
                for follower in followers
            ):
                raise PrivacyObservationIncomplete("platform_log_capture_incomplete")
            return
        if any(follower.stdout.done() for follower in followers):
            raise PrivacyObservationIncomplete("platform_log_capture_incomplete")
        changed = [asyncio.create_task(follower.stdout_changed.wait()) for follower in followers]
        try:
            await asyncio.wait(changed, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in changed:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*changed, return_exceptions=True)


async def capture_privacy_window(
    profile: str,
    app_name: str,
    send_success: Callable[[], Awaitable[PrivacyTurn]],
    send_rejected: Callable[[], Awaitable[PrivacyRejection]],
    api_prefix: str,
) -> PrivacyWindow:
    """@covers US-068-AC10: capture requests under active APP and unfiltered follows."""
    followers: list[_Follower] = []
    try:
        followers.append(await _start_follower(profile, app_name, "APP"))
        followers.append(await _start_follower(profile, app_name, None))
        # Start both subscriptions, then let the fresh AC10 requests provide the
        # window anchors. Readiness and a post-request delay are not correctness
        # evidence; the caller validates the captured invocation and HTTP records.
        success = await send_success()
        rejected = await send_rejected()
        if not success.invocation_id:
            raise PrivacyObservationIncomplete("privacy_invocation_marker_unavailable")
        await _wait_for_privacy_witnesses(followers, success, rejected, api_prefix)
    except BaseException as error:
        cleanup = asyncio.create_task(_stop_followers(followers))
        try:
            await await_retained(cleanup)
        except asyncio.CancelledError:
            raise
        except BaseException as cleanup_error:
            if isinstance(error, asyncio.CancelledError):
                raise error from None
            raise cleanup_error from error
        raise
    stopped = await await_retained(asyncio.create_task(_stop_followers(followers)))
    output = [part for pair in stopped for part in pair]
    if len(followers) != 2:
        raise PrivacyObservationIncomplete("platform_log_subscriptions_incomplete")
    if not success.invocation_id:
        raise PrivacyObservationIncomplete("privacy_invocation_marker_unavailable")
    return PrivacyWindow(
        app_logs=output[0].decode("utf-8"),
        platform_logs=output[2].decode("utf-8"),
        diagnostics=(output[1].decode("utf-8"), output[3].decode("utf-8")),
        success=success,
        rejected=rejected,
    )
