"""Controls for the bounded RAM-secret credential child."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import selected_profiles_identity_child as child_module
import selected_profiles_identity_credentials as credentials
from selected_profiles_process import await_retained

_HOST = "https://workspace.fabricated.example"
_CONFIG_FILE = str(Path.cwd() / "fabricated-config")
_REFERENCE_FILE = str(Path.cwd() / "fabricated-reference")
_SECRET = "".join(("fabricated", "-client-secret"))
_AUTHORIZATION = " ".join(("Bearer", "fabricated-access-token"))


async def _wait_event_or_operation(
    event: asyncio.Event, operation: asyncio.Task[Any], failure: str
) -> None:
    event_wait = asyncio.create_task(event.wait())
    finish_cleanup = asyncio.Event()

    async def settle() -> None:
        await finish_cleanup.wait()
        if not event_wait.done():
            event_wait.cancel()
        await asyncio.gather(event_wait, return_exceptions=True)

    cleanup = asyncio.create_task(settle())
    try:
        done, _ = await asyncio.wait({event_wait, operation}, return_when=asyncio.FIRST_COMPLETED)
        if event_wait not in done:
            await operation
            raise AssertionError(failure)
    finally:
        finish_cleanup.set()
        await await_retained(cleanup)


def _held_oauth_handler(
    accepted: asyncio.Event,
    request_shape: dict[str, bool],
    *,
    disconnect_token: bool = False,
) -> tuple[
    list[str],
    Callable[[asyncio.StreamReader, asyncio.StreamWriter], Coroutine[Any, Any, None]],
    set[asyncio.Task[Any]],
]:
    host_ref: list[str] = []
    handlers: set[asyncio.Task[Any]] = set()

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            handlers.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            if headers.startswith(b"GET "):
                host = host_ref[0]
                request_shape["discovery"] = True
                metadata = json.dumps(
                    {
                        "issuer": host,
                        "authorization_endpoint": f"{host}/authorize",
                        "token_endpoint": f"{host}/token",
                        "jwks_uri": f"{host}/jwks",
                    },
                    separators=(",", ":"),
                ).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                    + str(len(metadata)).encode()
                    + b"\r\nconnection: close\r\n\r\n"
                    + metadata
                )
                await writer.drain()
                return
            if not headers.startswith(b"POST "):
                method = headers.split(b" ", 1)[0]
                raise RuntimeError(f"oauth_request_method_unexpected:{method!r}")
            request_shape.update(
                post=True,
                credential_transport=b"authorization: basic " in headers.lower(),
            )
            accepted.set()
            if disconnect_token:
                writer.transport.abort()
                return
            await reader.read()
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        finally:
            writer.close()
            await writer.wait_closed()
            if task is not None:
                handlers.discard(task)

    return host_ref, hold, handlers


def _ram_payload(secret: str = _SECRET) -> bytes:
    return credentials._credential_request_payload(
        profile="fabricated-profile",
        config_file=_CONFIG_FILE,
        expected_host=_HOST,
        secret=secret,
    )


def test_ram_factory_sends_secret_only_in_child_stdin_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    raw = (
        json.dumps(
            {
                "status": "ok",
                "host": _HOST,
                "authorization": _AUTHORIZATION,
                "failure": None,
            },
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )

    async def child(payload: bytes, *, command: tuple[str, ...], timeout_seconds: float) -> bytes:
        seen["payload"] = payload
        seen["command"] = command
        seen["timeout_seconds"] = timeout_seconds
        return raw

    monkeypatch.setattr(credentials, "_run_process", child)
    result = asyncio.run(
        credentials.refresh_workspace_credential_from_secret(
            "fabricated-profile", _CONFIG_FILE, _SECRET, _HOST
        )
    )
    request = json.loads(seen["payload"])
    assert request == {
        "profile": "fabricated-profile",
        "config_file": _CONFIG_FILE,
        "expected_host": _HOST,
        "source": "ram",
        "secret": _SECRET,
    }
    command = seen["command"]
    assert command == (
        credentials.sys.executable,
        str(credentials._CHILD_SCRIPT),
        "--internal-credential-child",
    )
    assert seen["timeout_seconds"] == credentials._CHILD_TIMEOUT_SECONDS
    assert all(_SECRET not in argument for argument in command)
    assert all(_SECRET not in value for value in credentials._child_environment().values())
    assert result == credentials.FreshCredential(_HOST, _AUTHORIZATION)
    assert _SECRET not in repr(result)
    request_object = child_module.CredentialChildRequest(
        "fabricated-profile", _CONFIG_FILE, _HOST, "ram", secret=_SECRET
    )
    assert _SECRET not in repr(request_object)
    assert callable(credentials.CredentialFactory)


def test_child_ram_mode_never_reads_keychain_and_uses_sdk_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_values: dict[str, Any] = {}

    class FakeConfig:
        def __init__(self, **kwargs: Any) -> None:
            config_values.update(kwargs)
            self.host = _HOST

        def authenticate(self) -> dict[str, str]:
            return {"Authorization": _AUTHORIZATION}

    class FakeWorkspace:
        def __init__(self, *, config: FakeConfig) -> None:
            self.config = config

    def keychain_forbidden(_reference: str) -> str:
        raise AssertionError("RAM mode must not access Keychain")

    monkeypatch.setattr(child_module, "Config", FakeConfig)
    monkeypatch.setattr(child_module, "WorkspaceClient", FakeWorkspace)
    monkeypatch.setattr(child_module, "keychain_secret", keychain_forbidden)
    output = bytearray()
    monkeypatch.setattr(credentials.sys, "stdout", SimpleNamespace(buffer=_Buffer(output)))

    code = child_module.credential_child_main(_ram_payload())
    result = json.loads(output)
    assert code == 0
    assert result == {
        "status": "ok",
        "host": _HOST,
        "authorization": _AUTHORIZATION,
        "failure": None,
    }
    assert config_values == {
        "profile": "fabricated-profile",
        "config_file": _CONFIG_FILE,
        "client_secret": _SECRET,
        "auth_type": "oauth-m2m",
        "http_timeout_seconds": 8,
        "retry_timeout_seconds": 2,
    }
    assert _SECRET.encode() not in output


class _Buffer:
    def __init__(self, storage: bytearray) -> None:
        self.storage = storage

    def write(self, value: bytes) -> int:
        self.storage.extend(value)
        return len(value)

    def flush(self) -> None:
        return None


def test_child_request_enforces_exactly_one_discriminated_secret_source() -> None:
    valid = json.loads(_ram_payload())
    assert child_module.parse_credential_child_request(_ram_payload()).source == "ram"

    for malformed in (
        {**valid, "secret_reference": _REFERENCE_FILE},
        {key: value for key, value in valid.items() if key != "secret"},
        {**valid, "source": "keychain"},
        {**valid, "unexpected": "field"},
    ):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            child_module.parse_credential_child_request(
                json.dumps(malformed, separators=(",", ":")).encode()
            )


def test_real_sdk_ram_secret_reaches_oauth_endpoint_and_is_bounded_by_child_timeout(
    tmp_path: Path,
) -> None:
    asyncio.run(_exercise_sdk_ram_secret_oauth(tmp_path))


async def _exercise_sdk_ram_secret_oauth(tmp_path: Path) -> None:
    accepted = asyncio.Event()
    request_shape = {"discovery": False, "post": False, "credential_transport": False}
    host_ref, handler, handlers = _held_oauth_handler(accepted, request_shape)
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    host_ref.append(host)
    config_file = tmp_path / "sdk-profile.ini"
    _write_oauth_profile(config_file, host)
    task = asyncio.create_task(
        credentials.refresh_workspace_credential_from_secret(
            "fabricated-profile", str(config_file), _SECRET, host
        )
    )
    try:
        await _wait_event_or_operation(
            accepted, task, "credential_operation_ended_before_oauth_post"
        )
        assert request_shape == {
            "discovery": True,
            "post": True,
            "credential_transport": True,
        }
        with pytest.raises(credentials.LiveQualificationIncomplete) as error:
            await task
        assert error.value.reason == "qualification_workspace_identity_not_observed"
    finally:

        async def settle() -> None:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            server.close()
            await server.wait_closed()
            await asyncio.gather(*handlers, return_exceptions=True)

        await await_retained(asyncio.create_task(settle()))


def test_real_sdk_connection_failure_remains_retryable(tmp_path: Path) -> None:
    asyncio.run(_exercise_sdk_connection_failure(tmp_path))


async def _exercise_sdk_connection_failure(tmp_path: Path) -> None:
    accepted = asyncio.Event()
    request_shape = {"discovery": False, "post": False, "credential_transport": False}
    host_ref, handler, handlers = _held_oauth_handler(
        accepted, request_shape, disconnect_token=True
    )
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    host = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    host_ref.append(host)
    config_file = tmp_path / "sdk-failure-profile.ini"
    _write_oauth_profile(config_file, host)
    task = asyncio.create_task(
        credentials.refresh_workspace_credential_from_secret(
            "fabricated-profile", str(config_file), _SECRET, host
        )
    )
    try:
        await _wait_event_or_operation(accepted, task, "sdk_failed_before_token_request")
        assert request_shape == {
            "discovery": True,
            "post": True,
            "credential_transport": True,
        }
        with pytest.raises(credentials.CredentialRefreshError) as info:
            await task
        assert info.value.retryable is True
        assert str(info.value) == "identity_credential_network_transient"
    finally:

        async def settle() -> None:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            server.close()
            await server.wait_closed()
            await asyncio.gather(*handlers, return_exceptions=True)

        await await_retained(asyncio.create_task(settle()))


def _write_oauth_profile(config_file: Path, host: str) -> None:
    config_file.write_text(
        f"[fabricated-profile]\nhost = {host}\nclient_id = fabricated-client\n"
        f"discovery_url = {host}/.well-known/oauth-authorization-server\n",
        encoding="utf-8",
    )


def test_ram_secret_limits_and_wrong_host_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="identity_credential_secret_invalid"):
        _ram_payload("")
    with pytest.raises(RuntimeError, match="identity_credential_secret_invalid"):
        _ram_payload("x" * (8 * 1024 + 1))

    async def child(_payload: bytes) -> bytes:
        return (
            json.dumps(
                {
                    "status": "ok",
                    "host": "https://other.example.test",
                    "authorization": _AUTHORIZATION,
                    "failure": None,
                },
                separators=(",", ":"),
            ).encode()
            + b"\n"
        )

    monkeypatch.setattr(credentials, "_run_credential_child", child)
    with pytest.raises(RuntimeError, match="restored_workspace_host_mismatch"):
        asyncio.run(
            credentials.refresh_workspace_credential_from_secret(
                "fabricated-profile", _CONFIG_FILE, _SECRET, _HOST
            )
        )


def test_keychain_and_ram_requests_are_disjoint() -> None:
    keychain = credentials._credential_request_payload(
        profile="fabricated-profile",
        config_file=_CONFIG_FILE,
        expected_host=_HOST,
        secret_reference=_REFERENCE_FILE,
    )
    ram = _ram_payload()
    keychain_value = json.loads(keychain)
    ram_value = json.loads(ram)
    assert keychain_value["source"] == "keychain"
    assert "secret" not in keychain_value
    assert "secret_reference" not in ram_value
    with pytest.raises(RuntimeError, match="identity_credential_source_invalid"):
        credentials._credential_request_payload(
            profile="fabricated-profile",
            config_file=_CONFIG_FILE,
            expected_host=_HOST,
        )
    with pytest.raises(RuntimeError, match="identity_credential_source_invalid"):
        credentials._credential_request_payload(
            profile="fabricated-profile",
            config_file=_CONFIG_FILE,
            expected_host=_HOST,
            secret_reference=_REFERENCE_FILE,
            secret=_SECRET,
        )
