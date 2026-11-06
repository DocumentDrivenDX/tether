"""Bounded child-process OAuth acquisition for selected-profile qualification."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config
from selected_profiles_identity_child import credential_child_main as _credential_child_main
from selected_profiles_identity_child import encode_child_result as _encode_child_result
from selected_profiles_process import await_retained

_CHILD_TIMEOUT_SECONDS = 25.0
_MAX_INPUT_BYTES = 16 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CHILD_SCRIPT = Path(__file__).resolve()
_T = TypeVar("_T")
CredentialFactory = Callable[[], Awaitable["FreshCredential"]]


@dataclass(frozen=True)
class FreshCredential:
    host: str
    authorization: str = field(repr=False)


class CredentialRefreshError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.retryable = retryable


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
    process: asyncio.subprocess.Process | None = None
    reader: asyncio.Task[bytes] | None = None
    spawn = asyncio.create_task(
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
    try:
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process, _ = await _settle_task(spawn)
            await await_retained(asyncio.create_task(_cleanup_child(process, None)))
            raise
        async with asyncio.timeout(timeout_seconds):
            if process is None or process.stdin is None or process.stdout is None:
                raise RuntimeError("identity_credential_child_pipe_unavailable")
            child = process
            stdin = child.stdin
            stdout = child.stdout
            if stdin is None or stdout is None:
                raise RuntimeError("identity_credential_child_pipe_unavailable")
            reader = asyncio.create_task(_read_child_output(stdout))
            stdin.write(payload)
            await stdin.drain()
            stdin.close()
            await child.wait()
            raw = await reader
            reader = None
            if child.returncode is None:
                raise RuntimeError("identity_credential_child_failed")
            return _validate_child_result(raw, child.returncode)
    except TimeoutError:
        raise CredentialRefreshError(
            "identity_credential_network_transient", retryable=True
        ) from None
    finally:
        if process is not None:
            await await_retained(asyncio.create_task(_cleanup_child(process, reader)))


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
    while chunk := await stream.read(4096):
        if len(output) + len(chunk) > _MAX_OUTPUT_BYTES:
            raise RuntimeError("identity_credential_child_output_limit")
        output.extend(chunk)
    return bytes(output)


async def _cleanup_child(
    process: asyncio.subprocess.Process, reader: asyncio.Task[bytes] | None
) -> None:
    if process.returncode is None:
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)
    if reader is not None:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


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
