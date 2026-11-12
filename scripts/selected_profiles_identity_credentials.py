"""Bounded child-process OAuth acquisition for selected-profile qualification."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_identity_child import credential_child_main as _credential_child_main
from selected_profiles_identity_child import encode_child_result as _encode_child_result
from selected_profiles_process import await_retained, wait_for_process_group_exit

_CHILD_TIMEOUT_SECONDS = 25.0
_MAX_INPUT_BYTES = 16 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CHILD_SCRIPT = Path(__file__).resolve()
_T = TypeVar("_T")
CredentialFactory = Callable[[], Awaitable["FreshCredential"]]


class _CredentialObservationExpired(Exception):
    """The caller-owned child observation bound expired."""


@dataclass(frozen=True)
class FreshCredential:
    host: str
    authorization: str = field(repr=False)


class CredentialRefreshError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.retryable = retryable


@dataclass
class _CredentialChild:
    spawn: asyncio.Task[asyncio.subprocess.Process]
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.Task[Any] | None = None


def workspace_from_credential(credential: FreshCredential) -> WorkspaceClient:
    """Build a non-refreshing PAT client from an already acquired bearer."""
    from urllib.parse import urlsplit

    parsed = urlsplit(credential.host)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("identity_workspace_host_invalid")
    if (
        not credential.authorization.startswith("Bearer ")
        or not credential.authorization.removeprefix("Bearer ").strip()
        or "\r" in credential.authorization
        or "\n" in credential.authorization
    ):
        raise RuntimeError("identity_workspace_credential_invalid")
    config = Config(
        host=credential.host,
        token=credential.authorization.removeprefix("Bearer "),
        auth_type="pat",
        http_timeout_seconds=8,
        retry_timeout_seconds=2,
    )
    return WorkspaceClient(config=config)


def workspace_for_bearer(host: str, bearer_token: str, /) -> WorkspaceClient:
    """Build the same bounded, non-refreshing client from a raw bearer token."""
    return workspace_from_credential(FreshCredential(host, " ".join(("Bearer", bearer_token))))


async def refresh_workspace_credential(
    profile: str,
    config_file: str,
    secret_reference: str,
    expected_host: str,
) -> FreshCredential:
    payload = _credential_request_payload(
        profile=profile,
        config_file=config_file,
        expected_host=expected_host,
        secret_reference=secret_reference,
    )
    return await _acquire_credential(payload, expected_host)


async def refresh_workspace_credential_from_secret(
    profile: str,
    config_file: str,
    secret: str,
    expected_host: str,
) -> FreshCredential:
    """Acquire OAuth in the bounded child with secret material supplied in RAM."""
    payload = _credential_request_payload(
        profile=profile,
        config_file=config_file,
        expected_host=expected_host,
        secret=secret,
    )
    return await _acquire_credential(payload, expected_host)


def _credential_request_payload(
    *,
    profile: str,
    config_file: str,
    expected_host: str,
    secret_reference: str | None = None,
    secret: str | None = None,
) -> bytes:
    if (secret_reference is None) == (secret is None):
        raise RuntimeError("identity_credential_source_invalid")
    if (
        not isinstance(profile, str)
        or not profile
        or len(profile) > 255
        or not isinstance(config_file, str)
        or not Path(config_file).is_absolute()
        or len(config_file) > 4096
        or not isinstance(expected_host, str)
        or not expected_host
        or len(expected_host) > 2048
    ):
        raise RuntimeError("identity_credential_request_invalid")
    request: dict[str, str] = {
        "profile": profile,
        "config_file": config_file,
        "expected_host": expected_host,
        "source": "keychain" if secret_reference is not None else "ram",
    }
    if secret_reference is not None:
        if (
            not isinstance(secret_reference, str)
            or not Path(secret_reference).is_absolute()
            or len(secret_reference) > 4096
        ):
            raise RuntimeError("identity_credential_source_invalid")
        request["secret_reference"] = secret_reference
    elif secret is not None:
        if not isinstance(secret, str) or not secret or len(secret) > 8 * 1024:
            raise RuntimeError("identity_credential_secret_invalid")
        request["secret"] = secret
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_INPUT_BYTES:
        raise RuntimeError("identity_credential_child_input_limit")
    return payload


async def _acquire_credential(payload: bytes, expected_host: str) -> FreshCredential:
    raw = await _run_credential_child(payload)
    return _decode_credential_child(raw, expected_host)


async def _run_credential_child(payload: bytes) -> bytes:
    return await _run_process(
        payload,
        command=(sys.executable, str(_CHILD_SCRIPT), "--internal-credential-child"),
        timeout_seconds=_CHILD_TIMEOUT_SECONDS,
    )


async def _run_process(
    payload: bytes,
    *,
    command: tuple[str, ...],
    timeout_seconds: float,
) -> bytes:
    if not sys.executable:
        raise RuntimeError("identity_credential_child_unavailable")
    child = _CredentialChild(
        asyncio.create_task(
            asyncio.create_subprocess_exec(
                *command,
                cwd=_PROJECT_ROOT,
                env=_child_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            )
        )
    )
    try:
        result = await _observe_credential_child(child, payload, timeout_seconds)
    except BaseException as error:
        cleanup_error = await _finish_credential_child(child)
        if isinstance(error, _CredentialObservationExpired):
            raise LiveQualificationIncomplete(
                "qualification_workspace_identity_not_observed"
            ) from cleanup_error
        if cleanup_error is not None:
            raise error from cleanup_error
        raise
    cleanup_error = await _finish_credential_child(child)
    if isinstance(cleanup_error, asyncio.CancelledError):
        raise cleanup_error
    if cleanup_error is not None:
        raise LiveQualificationIncomplete(
            "qualification_workspace_identity_not_observed"
        ) from cleanup_error
    return result


async def _observe_credential_child(
    child: _CredentialChild, payload: bytes, seconds: float
) -> bytes:
    timeout = asyncio.timeout(seconds)
    try:
        async with timeout:
            process = await asyncio.shield(child.spawn)
            child.process = process
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("identity_credential_child_pipe_unavailable")
            child.reader = asyncio.create_task(_read_child_output(process.stdout))
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            await process.wait()
            reader = child.reader
            if reader is None:
                raise RuntimeError("identity_credential_child_reader_unavailable")
            raw = await asyncio.shield(reader)
            child.reader = None
            if process.returncode is None:
                raise RuntimeError("identity_credential_child_failed")
            return _validate_child_result(raw, process.returncode)
    except TimeoutError as error:
        if timeout.expired():
            raise _CredentialObservationExpired from error
        raise


async def _finish_credential_child(child: _CredentialChild) -> BaseException | None:
    cleanup = asyncio.create_task(_cleanup_credential_acquisition(child))
    try:
        await await_retained(cleanup)
    except BaseException as error:
        return error
    return None


async def _cleanup_credential_acquisition(child: _CredentialChild) -> None:
    process = child.process
    if process is None:
        try:
            process = await asyncio.shield(child.spawn)
        except BaseException:
            return
        child.process = process
    await _cleanup_child(process, child.reader)


def _task_failed(task: asyncio.Task[Any]) -> bool:
    if not task.done():
        return False
    return task.cancelled() or task.exception() is not None


async def _drain_child_output(stream: asyncio.StreamReader) -> None:
    while await stream.read(4096):
        continue


def _child_environment() -> dict[str, str]:
    executable_dir = str(Path(sys.executable).absolute().parent)
    return {
        "HOME": str(Path.home()),
        "PATH": os.pathsep.join((executable_dir, "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
        "PYTHONPATH": str(_PROJECT_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


async def _read_child_output(stream: asyncio.StreamReader) -> bytes:
    output = bytearray()
    overflow = False
    while chunk := await stream.read(4096):
        remaining = _MAX_OUTPUT_BYTES - len(output)
        output.extend(chunk[:remaining])
        overflow = overflow or len(chunk) > remaining
    if overflow:
        raise RuntimeError("identity_credential_child_output_limit")
    return bytes(output)


async def _cleanup_child(
    process: asyncio.subprocess.Process, reader: asyncio.Task[Any] | None
) -> None:
    errors: list[BaseException] = []
    _close_child_stdin(process, errors)
    errors.extend(_signal_child_group(process))
    if reader is None and process.stdout is not None:
        reader = asyncio.create_task(_drain_child_output(process.stdout))
    wait_tasks: list[asyncio.Future[Any]] = [asyncio.create_task(process.wait())]
    if reader is not None:
        wait_tasks.append(reader)
    if isinstance(process, asyncio.subprocess.Process) and os.name == "posix":
        wait_tasks.append(
            asyncio.create_task(asyncio.to_thread(wait_for_process_group_exit, process.pid))
        )
    outcomes = await asyncio.gather(*wait_tasks, return_exceptions=True)
    errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if reader is not None and _task_failed(reader) and process.stdout is not None:
        drain = asyncio.create_task(_drain_child_output(process.stdout))
        outcomes = await asyncio.gather(drain, return_exceptions=True)
        errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if errors:
        raise BaseExceptionGroup("identity_credential_child_cleanup_failed", errors)


def _close_child_stdin(process: asyncio.subprocess.Process, errors: list[BaseException]) -> None:
    if process.stdin is None:
        return
    try:
        process.stdin.close()
    except BaseException as error:
        errors.append(error)


def _signal_child_group(process: asyncio.subprocess.Process) -> list[BaseException]:
    errors: list[BaseException] = []
    if not isinstance(process, asyncio.subprocess.Process):
        errors.extend(_kill_live_child(process))
    elif os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return errors
        except BaseException as error:
            errors.append(error)
    else:
        errors.extend(_kill_live_child(process))
    return errors


def _kill_live_child(process: asyncio.subprocess.Process) -> list[BaseException]:
    if process.returncode is not None:
        return []
    try:
        process.kill()
    except ProcessLookupError:
        return []
    except BaseException as error:
        return [error]
    return []


async def _settle_task(task: asyncio.Task[_T]) -> tuple[_T, bool]:
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


def _validate_child_result(raw: bytes, returncode: int) -> bytes:
    if returncode != 0 or not raw or len(raw) > _MAX_OUTPUT_BYTES or not raw.endswith(b"\n"):
        raise RuntimeError("identity_credential_child_failed")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("identity_credential_child_failed") from None
    if not isinstance(value, dict) or set(value) != {"status", "host", "authorization", "failure"}:
        raise RuntimeError("identity_credential_child_failed")
    if value["status"] == "ok":
        if (
            value["failure"] is not None
            or not isinstance(value["host"], str)
            or not isinstance(value["authorization"], str)
            or len(value["host"]) > 2048
            or len(value["authorization"]) > 60 * 1024
        ):
            raise RuntimeError("identity_credential_child_failed")
        return raw
    if (
        value["status"] != "error"
        or value["host"] is not None
        or value["authorization"] is not None
        or value["failure"]
        not in {
            "transient_auth",
            "transient_network",
            "rejected",
            "host_mismatch",
            "other",
        }
    ):
        raise RuntimeError("identity_credential_child_failed")
    return raw


def _decode_credential_child(raw: bytes, expected_host: str) -> FreshCredential:
    value = json.loads(raw)
    if value["status"] == "error":
        failure = value["failure"]
        if failure in {"transient_auth", "transient_network"}:
            raise CredentialRefreshError(
                "identity_credential_auth_transient"
                if failure == "transient_auth"
                else "identity_credential_network_transient",
                retryable=True,
            )
        if failure == "host_mismatch":
            raise RuntimeError("restored_workspace_host_mismatch")
        raise RuntimeError(
            "identity_credential_rejected"
            if failure == "rejected"
            else "identity_credential_child_failed"
        )
    credential = FreshCredential(value["host"], value["authorization"])
    if credential.host != expected_host:
        raise RuntimeError("restored_workspace_host_mismatch")
    from urllib.parse import urlsplit

    parsed = urlsplit(credential.host)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise RuntimeError("identity_workspace_host_invalid")
    if (
        not credential.authorization.startswith("Bearer ")
        or not credential.authorization.removeprefix("Bearer ").strip()
        or "\r" in credential.authorization
        or "\n" in credential.authorization
    ):
        raise RuntimeError("identity_workspace_credential_invalid")
    return credential


def _main() -> int:
    if sys.argv[1:] != ["--internal-credential-child"]:
        return 2
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        return _credential_child_main(raw)
    except Exception:
        sys.stdout.buffer.write(_encode_child_result(failure="other"))
        sys.stdout.buffer.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "CredentialFactory",
    "CredentialRefreshError",
    "FreshCredential",
    "refresh_workspace_credential",
    "refresh_workspace_credential_from_secret",
    "workspace_for_bearer",
    "workspace_from_credential",
]
