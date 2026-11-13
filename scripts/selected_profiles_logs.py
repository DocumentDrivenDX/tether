"""Bounded APP and unfiltered platform log subscriptions for live privacy evidence."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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
    process_exit: asyncio.Task[int] | None = None


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
    command = _follower_command(profile, app_name, source)
    process, cancelled_while_spawning = await _spawn_follower(command)
    if process.stdout is None or process.stderr is None:
        cleanup = asyncio.create_task(_stop_incomplete_process(process))
        try:
            await await_retained(cleanup)
        except BaseException as error:
            if cancelled_while_spawning:
                raise asyncio.CancelledError from error
            raise PrivacyObservationIncomplete("platform_log_capture_incomplete") from error
        if cancelled_while_spawning:
            raise asyncio.CancelledError
        raise PrivacyObservationIncomplete("platform_log_pipe_unavailable")
    follower = _new_follower(process)
    if cancelled_while_spawning or process.returncode is not None:
        cleanup = asyncio.create_task(_stop_follower(follower))
        await await_retained(cleanup)
        if cancelled_while_spawning:
            raise asyncio.CancelledError
        raise PrivacyObservationIncomplete("platform_log_subscription_did_not_start")
    return follower


def _follower_command(profile: str, app_name: str, source: str | None) -> list[str]:
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
    return command


async def _spawn_follower(command: list[str]) -> tuple[asyncio.subprocess.Process, bool]:
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
        if cancelled_while_spawning:
            raise asyncio.CancelledError from None
        raise PrivacyObservationIncomplete("platform_log_subscription_start_failed") from None
    return process, cancelled_while_spawning


def _new_follower(process: asyncio.subprocess.Process) -> _Follower:
    stdout, stderr = process.stdout, process.stderr
    if stdout is None or stderr is None:
        raise PrivacyObservationIncomplete("platform_log_pipe_unavailable")
    ready = asyncio.Event()
    stdout_data = bytearray()
    stderr_data = bytearray()
    stdout_changed = asyncio.Event()
    capture_overflow = asyncio.Event()
    process_exit = (
        asyncio.create_task(process.wait())
        if isinstance(process, asyncio.subprocess.Process)
        else None
    )
    return _Follower(
        process,
        asyncio.create_task(
            _read_bounded(
                stdout,
                ready,
                captured=stdout_data,
                changed=stdout_changed,
                overflow=capture_overflow,
            )
        ),
        asyncio.create_task(
            _read_bounded(
                stderr,
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
        process_exit,
    )


async def _stop_incomplete_process(process: asyncio.subprocess.Process) -> None:
    failures: list[BaseException] = []
    readers = tuple(
        asyncio.create_task(_read_bounded(stream, asyncio.Event()))
        for stream in (process.stdout, process.stderr)
        if stream is not None
    )
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except BaseException as error:
            failures.append(error)
    try:
        await process.wait()
    except BaseException as error:
        failures.append(error)
    outcomes = await asyncio.gather(*readers, return_exceptions=True)
    failures.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if failures:
        raise BaseExceptionGroup("platform_log_pipe_start_cleanup_failed", failures)


async def _stop_follower(follower: _Follower) -> tuple[bytes, bytes]:
    errors: list[BaseException] = []
    await _stop_owned_process(follower.process, errors)
    result = await _drain_follower_readers(follower, errors)
    _raise_capture_errors(errors)
    return result


async def _stop_owned_process(
    process: asyncio.subprocess.Process, errors: list[BaseException]
) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)
    try:
        await process.wait()
    except BaseException as error:
        errors.append(error)
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except BaseException as error:
            errors.append(error)
        try:
            await process.wait()
        except BaseException as error:
            errors.append(error)


async def _drain_follower_readers(
    follower: _Follower, errors: list[BaseException]
) -> tuple[bytes, bytes]:
    readers = await asyncio.gather(follower.stdout, follower.stderr, return_exceptions=True)
    if follower.process_exit is not None:
        process_exit = (await asyncio.gather(follower.process_exit, return_exceptions=True))[0]
        if isinstance(process_exit, BaseException):
            errors.append(process_exit)
    result: list[bytes] = []
    for reader in readers:
        if isinstance(reader, BaseException):
            errors.append(reader)
            result.append(b"")
        else:
            result.append(reader)
    return result[0], result[1]


def _raise_capture_errors(errors: list[BaseException]) -> None:
    if not errors:
        return
    limit = next(
        (
            error
            for error in errors
            if isinstance(error, PrivacyObservationIncomplete)
            and error.reason == "platform_log_capture_limit_exceeded"
        ),
        None,
    )
    if limit is not None:
        raise limit from BaseExceptionGroup("platform_log_reader_errors", errors)
    raise PrivacyObservationIncomplete("platform_log_capture_incomplete") from BaseExceptionGroup(
        "platform_log_stop_errors", errors
    )


async def _stop_followers(followers: list[_Follower]) -> list[tuple[bytes, bytes]]:
    cleanup = asyncio.create_task(_stop_followers_all(followers))
    return await await_retained(cleanup)


async def _stop_followers_all(followers: list[_Follower]) -> list[tuple[bytes, bytes]]:
    results = await asyncio.gather(
        *(_stop_follower(follower) for follower in followers), return_exceptions=True
    )
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        limit = next(
            (
                error
                for error in errors
                if isinstance(error, PrivacyObservationIncomplete)
                and error.reason == "platform_log_capture_limit_exceeded"
            ),
            None,
        )
        if limit is not None:
            raise limit from BaseExceptionGroup("platform_log_follower_errors", errors)
        raise PrivacyObservationIncomplete(
            "platform_log_capture_incomplete"
        ) from BaseExceptionGroup("platform_log_follower_errors", errors)
    return [result for result in results if not isinstance(result, BaseException)]


async def _cancel_observers(observers: list[asyncio.Task[Literal[True]]]) -> None:
    for task in observers:
        if not task.done():
            task.cancel()
    results = await asyncio.gather(*observers, return_exceptions=True)
    errors = [
        result
        for result in results
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
    ]
    if errors:
        raise PrivacyObservationIncomplete(
            "platform_log_capture_incomplete"
        ) from BaseExceptionGroup("platform_log_observer_errors", errors)


def _has_private_marker(followers: list[_Follower], markers: tuple[bytes, ...]) -> bool:
    return any(
        marker in value
        for follower in followers
        for value in (bytes(follower.stdout_data), bytes(follower.stderr_data))
        for marker in markers
    )


def _has_privacy_witnesses(
    app: _Follower,
    platform: _Follower,
    success: PrivacyTurn,
    rejected: PrivacyRejection,
    api_prefix: str,
) -> bool:
    app_entries = _complete_log_entries(bytes(app.stdout_data))
    platform_entries = _complete_log_entries(bytes(platform.stdout_data))
    worker_marker = f"TETHER_QUALIFICATION_WORKER_STARTED {success.invocation_id}"
    app_seen = any(source == "APP" and worker_marker in message for source, message in app_entries)
    success_seen, rejection_seen = _http_anchors(
        platform_entries,
        success.session_id,
        rejected.session_id,
        api_prefix,
    )
    return app_seen and success_seen and rejection_seen


def _followers_active(followers: list[_Follower]) -> bool:
    return all(
        follower.process.returncode is None
        and not follower.stdout.done()
        and not follower.stderr.done()
        for follower in followers
    )


async def _wait_for_follower_change(followers: list[_Follower]) -> None:
    observers = [asyncio.create_task(follower.stdout_changed.wait()) for follower in followers]
    terminals: set[asyncio.Future[Any]] = set()
    for follower in followers:
        terminals.update((follower.stdout, follower.stderr))
        process_exit = getattr(follower, "process_exit", None)
        if process_exit is not None:
            terminals.add(process_exit)
    primary: BaseException | None = None
    try:
        done, _ = await asyncio.wait((*observers, *terminals), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task in terminals:
                _observe_follower_terminal(task)
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = asyncio.create_task(_cancel_observers(observers))
        try:
            await await_retained(cleanup)
        except BaseException as cleanup_error:
            if primary is not None:
                raise primary from cleanup_error
            raise


def _observe_follower_terminal(task: asyncio.Future[Any]) -> None:
    try:
        task.result()
    except asyncio.CancelledError as error:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        raise PrivacyObservationIncomplete("platform_log_capture_incomplete") from error
    except Exception as error:
        raise PrivacyObservationIncomplete("platform_log_capture_incomplete") from error


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
        if _has_private_marker(followers, private_markers):
            raise RuntimeError("privacy_marker_found_in_platform_logs")
        if _has_privacy_witnesses(app_follower, platform_follower, success, rejected, api_prefix):
            if not _followers_active(followers):
                raise PrivacyObservationIncomplete("platform_log_capture_incomplete")
            return
        if not _followers_active(followers):
            raise PrivacyObservationIncomplete("platform_log_capture_incomplete")
        await _wait_for_follower_change(followers)


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
        except BaseException as cleanup_error:
            if cleanup_error is not error:
                raise error from cleanup_error
        raise
    stopped = await _stop_followers(followers)
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
