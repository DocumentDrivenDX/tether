"""Controls for bounded restoration credential acquisition and readiness."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import selected_profiles_identity_child as credential_child
import selected_profiles_identity_credentials as credentials
import selected_profiles_identity_probe as probe
from databricks.sdk import WorkspaceClient

HOST = "https://workspace.fabricated.example"
PRINCIPAL = "fabricated-principal"
TOKEN = " ".join(("Bearer", "fabricated-fresh-token"))


def _active_response(principal_id: str = PRINCIPAL) -> httpx.Response:
    return httpx.Response(200, json={"id": principal_id, "active": True})


def _install_responses(monkeypatch, responses: list[httpx.Response]) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        probe,
        "_client",
        lambda host: httpx.AsyncClient(
            base_url=host,
            timeout=httpx.Timeout(10),
            trust_env=False,
            verify=True,
            transport=transport,
        ),
    )
    return requests


async def _no_wait(_duration: float) -> None:
    return None


async def _await_event_or_operation(event: asyncio.Event, operation: asyncio.Task[Any]) -> None:
    event_wait = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({event_wait, operation}, return_when=asyncio.FIRST_COMPLETED)
        if event_wait not in done:
            await operation
            raise AssertionError("operation_terminated_before_expected_event")
    finally:
        if not event_wait.done():
            event_wait.cancel()
            await asyncio.gather(event_wait, return_exceptions=True)


class _ManualDeadline:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, error_type: object, *_args: object) -> bool:
        if error_type is asyncio.CancelledError:
            raise TimeoutError
        return False


def test_fresh_credentials_retry_known_auth_refusal_and_protected_401(monkeypatch) -> None:
    requests = _install_responses(monkeypatch, [_active_response(), _active_response()])
    monkeypatch.setattr(probe.asyncio, "sleep", _no_wait)
    attempts = 0
    statuses = iter((401, 200))

    async def refresh() -> credentials.FreshCredential:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise credentials.CredentialRefreshError("fixed transient", retryable=True)
        return credentials.FreshCredential(HOST, TOKEN)

    def protected_status_for(_token: str):
        async def protected_status() -> int:
            return next(statuses)

        return protected_status

    result = asyncio.run(
        probe.fresh_workspace_access(refresh, HOST, PRINCIPAL, protected_status_for)
    )
    assert attempts == 2
    assert result == credentials.FreshCredential(HOST, TOKEN)
    assert len(requests) == 2
    assert all(request.headers["authorization"] == TOKEN for request in requests)


def test_fresh_access_rejects_refreshed_workspace_host_switch(monkeypatch) -> None:
    probed = False

    async def refresh() -> credentials.FreshCredential:
        return credentials.FreshCredential("https://other.fabricated.example", TOKEN)

    async def protected_status() -> int:
        nonlocal probed
        probed = True
        return 200

    with pytest.raises(RuntimeError, match="restored_workspace_host_mismatch"):
        asyncio.run(
            probe.fresh_workspace_access(refresh, HOST, PRINCIPAL, lambda _token: protected_status)
        )
    assert not probed


def test_fresh_access_deadline_cancels_pending_readiness(monkeypatch) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    monkeypatch.setattr(probe.asyncio, "timeout", lambda *_args, **_kwargs: _ManualDeadline())
    monkeypatch.setattr(probe.asyncio, "timeout_at", lambda *_args, **_kwargs: _ManualDeadline())

    async def handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _active_response()

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        probe,
        "_client",
        lambda host: httpx.AsyncClient(
            base_url=host,
            timeout=httpx.Timeout(10),
            trust_env=False,
            verify=True,
            transport=transport,
        ),
    )

    async def refresh() -> credentials.FreshCredential:
        return credentials.FreshCredential(HOST, TOKEN)

    async def protected_status() -> int:
        return 200

    async def run() -> None:
        task = asyncio.create_task(
            probe.fresh_workspace_access(refresh, HOST, PRINCIPAL, lambda _token: protected_status)
        )
        await _await_event_or_operation(entered, task)
        task.cancel()
        with pytest.raises(RuntimeError, match="identity_workspace_transition_timeout"):
            await task
        assert cancelled.is_set()

    asyncio.run(run())


def test_static_workspace_client_uses_pat_without_sdk_oauth(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def config(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return kwargs

    client = cast(WorkspaceClient, object())
    monkeypatch.setattr(credentials, "Config", config)
    monkeypatch.setattr(credentials, "WorkspaceClient", lambda *, config: client)
    result = credentials.workspace_from_credential(credentials.FreshCredential(HOST, TOKEN))
    assert result is client
    assert captured == {
        "host": HOST,
        "token": TOKEN.removeprefix("Bearer "),
        "auth_type": "pat",
        "http_timeout_seconds": 8,
        "retry_timeout_seconds": 2,
    }
    assert credentials.workspace_for_bearer(HOST, TOKEN.removeprefix("Bearer ")) is client


def test_fresh_credential_repr_omits_authorization() -> None:
    credential = credentials.FreshCredential(HOST, TOKEN)
    assert HOST in repr(credential)
    assert TOKEN not in repr(credential)


def test_child_environment_has_no_inherited_databricks_credentials() -> None:
    env = credentials._child_environment()
    assert not any(key.startswith("DATABRICKS_") for key in env)
    assert "PYTHONDONTWRITEBYTECODE" in env
    assert "client_secret" not in " ".join(env.keys()).lower()


async def _blocked_sdk_request() -> bool:
    accepted = asyncio.Event()

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        del reader
        accepted.set()
        try:
            await asyncio.Event().wait()
        finally:
            writer.close()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/token"
    code = (
        "from databricks.sdk.oauth import retrieve_token; "
        "retrieve_token(None, None, __import__('sys').argv[1], "
        "{'grant_type': 'client_credentials'})"
    )
    task = asyncio.create_task(
        credentials._run_process(
            b"",
            command=(sys.executable, "-c", code, url),
            timeout_seconds=60,
        )
    )
    try:
        await _await_event_before_terminal(accepted, task)
        with pytest.raises(credentials.CredentialRefreshError) as error:
            await task
        assert error.value.retryable is True
        return True
    finally:
        server.close()
        await server.wait_closed()


def test_sdk_oauth_no_timeout_is_killed_and_reaped_in_child_process() -> None:
    assert asyncio.run(_blocked_sdk_request())


def test_repeated_cancellation_kills_and_reaps_child(tmp_path) -> None:
    async def run() -> None:
        accepted = asyncio.Event()
        pid_file = tmp_path / "child-pid"

        async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            del reader
            accepted.set()
            try:
                await asyncio.Event().wait()
            finally:
                writer.close()

        server = await asyncio.start_server(hold, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        url = f"http://127.0.0.1:{port}/token"
        code = (
            "import os,sys; open(sys.argv[1], 'w').write(str(os.getpid())); "
            "from databricks.sdk.oauth import retrieve_token; "
            "retrieve_token(None, None, sys.argv[2], {'grant_type': 'client_credentials'})"
        )
        task = asyncio.create_task(
            credentials._run_process(
                b"",
                command=(sys.executable, "-c", code, str(pid_file), url),
                timeout_seconds=60,
            )
        )
        try:
            await _await_event_before_terminal(accepted, task)
            child_pid = int(pid_file.read_text(encoding="ascii"))
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_child_request_contains_only_profile_metadata(tmp_path) -> None:
    config_file = tmp_path / "fabricated-config"
    reference = tmp_path / "fabricated-reference"
    payload = credentials._credential_request_payload(
        profile="fabricated-profile",
        config_file=str(config_file),
        secret_reference=str(reference),
        expected_host=HOST,
    )
    request = credential_child.parse_credential_child_request(payload)
    assert request.profile == "fabricated-profile"
    assert request.config_file == str(config_file)
    assert request.secret_reference == str(reference)
    assert request.expected_host == HOST
    assert request.source == "keychain"
    assert b"client_secret" not in payload


def test_child_entrypoint_returns_fixed_json_for_malformed_input() -> None:
    raw = asyncio.run(
        credentials._run_process(
            b"{",
            command=(
                sys.executable,
                str(Path(credentials.__file__).resolve()),
                "--internal-credential-child",
            ),
            timeout_seconds=60,
        )
    )
    result = credentials._validate_child_result(raw, 0)
    assert json.loads(result) == {
        "status": "error",
        "host": None,
        "authorization": None,
        "failure": "rejected",
    }


async def _await_event_before_terminal(
    event: asyncio.Event, operation: asyncio.Task[object]
) -> None:
    watcher = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait({watcher, operation}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done and event.is_set():
            return
        await operation
        raise AssertionError("operation completed before the expected request event")
    finally:
        if not watcher.done():
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
