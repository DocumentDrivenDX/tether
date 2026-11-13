"""Unit controls for the bounded credential child launcher (fabricated values, local children)."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import selected_profiles_identity_credentials as credentials
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_identity_credentials import CredentialRefreshError, FreshCredential

_HOST = "https://workspace.fabricated.example"
_TOKEN = "-".join(("fabricated", "token"))
_AUTH = " ".join(("Bearer", _TOKEN))
_FAKE = "".join(("fabricated", "-material"))
_CONFIG = str(Path.cwd() / "fabricated-config")
_REFERENCE = str(Path.cwd() / "fabricated-reference")


def _result(**override: object) -> bytes:
    value = {
        "status": "ok",
        "host": _HOST,
        "authorization": _AUTH,
        "failure": None,
        **override,
    }
    return json.dumps(value).encode() + b"\n"


class _ControlledStdin:
    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _ControlledProcess:
    pid = 1
    returncode = 0

    def __init__(self, output: bytes, *, eof: bool = True, raw_timeout: bool = False) -> None:
        self.stdin = _ControlledStdin()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(output)
        if eof:
            self.stdout.feed_eof()
        self.raw_timeout = raw_timeout
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        if self.raw_timeout and self.wait_calls == 1:
            raise TimeoutError("producer_wait_timeout")
        return 0


def _error(failure: object) -> bytes:
    return _result(status="error", host=None, authorization=None, failure=failure)


def test_workspace_client_is_built_from_bearer_with_bounded_pat_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def config(**kwargs: Any) -> str:
        seen.update(kwargs)
        return "config"

    monkeypatch.setattr(credentials, "Config", config)
    monkeypatch.setattr(credentials, "WorkspaceClient", lambda config: ("client", config))
    assert credentials.workspace_for_bearer(_HOST, _TOKEN) == ("client", "config")
    assert seen == {
        "host": _HOST,
        "token": _TOKEN,
        "auth_type": "pat",
        "http_timeout_seconds": 8,
        "retry_timeout_seconds": 2,
    }


@pytest.mark.parametrize(
    "host",
    [
        "http://workspace.fabricated.example",
        "https://",
        "https://user@workspace.fabricated.example",
        "https://user:pw@workspace.fabricated.example",
        "https://workspace.fabricated.example/path",
        "https://workspace.fabricated.example?q=1",
        "https://workspace.fabricated.example#frag",
    ],
    ids=[
        "insecure-scheme",
        "empty-host",
        "userinfo",
        "userinfo-secret",
        "path",
        "query",
        "fragment",
    ],
)
def test_workspace_from_credential_refuses_unsafe_hosts(host: str) -> None:
    with pytest.raises(RuntimeError, match="identity_workspace_host_invalid"):
        credentials.workspace_from_credential(FreshCredential(host, _AUTH))


@pytest.mark.parametrize(
    "authorization",
    [_TOKEN, "Bearer ", "Bearer a\nb", "Bearer a\rb"],
    ids=["bare-token", "blank", "newline", "carriage-return"],
)
def test_workspace_from_credential_refuses_malformed_authorization(authorization: str) -> None:
    with pytest.raises(RuntimeError, match="identity_workspace_credential_invalid"):
        credentials.workspace_from_credential(FreshCredential(_HOST, authorization))


def test_fresh_credential_repr_hides_authorization() -> None:
    assert _TOKEN not in repr(FreshCredential(_HOST, _AUTH))


def _payload(**override: Any) -> bytes:
    arguments: dict[str, Any] = {
        "profile": "profile-1",
        "config_file": _CONFIG,
        "expected_host": _HOST,
        "secret_reference": _REFERENCE,
    } | override
    return credentials._credential_request_payload(**arguments)


def test_payload_discriminates_keychain_and_ram_sources() -> None:
    keychain = json.loads(_payload())
    assert keychain["source"] == "keychain"
    assert keychain["secret_reference"] == _REFERENCE
    ram = json.loads(_payload(secret_reference=None, **{"secret": _FAKE}))
    assert ram["source"] == "ram"
    assert ram["secret"] == _FAKE
    assert "secret_reference" not in ram


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"secret": "s"}, "identity_credential_source_invalid"),
        ({"secret_reference": None}, "identity_credential_source_invalid"),
        ({"profile": ""}, "identity_credential_request_invalid"),
        ({"profile": "p" * 256}, "identity_credential_request_invalid"),
        ({"config_file": "relative"}, "identity_credential_request_invalid"),
        ({"config_file": "/" + "c" * 4096}, "identity_credential_request_invalid"),
        ({"expected_host": ""}, "identity_credential_request_invalid"),
        ({"expected_host": "h" * 2049}, "identity_credential_request_invalid"),
        ({"secret_reference": "relative"}, "identity_credential_source_invalid"),
        ({"secret_reference": "/" + "r" * 4096}, "identity_credential_source_invalid"),
        ({"secret_reference": None, "secret": ""}, "identity_credential_secret_invalid"),
        (
            {"secret_reference": None, "secret": "s" * (8 * 1024 + 1)},
            "identity_credential_secret_invalid",
        ),
    ],
)
def test_payload_refuses_invalid_requests(override: dict[str, Any], code: str) -> None:
    with pytest.raises(RuntimeError, match=code):
        _payload(**override)


def test_payload_enforces_total_input_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_MAX_INPUT_BYTES", 20)
    with pytest.raises(RuntimeError, match="identity_credential_child_input_limit"):
        _payload()


@pytest.mark.parametrize(
    ("function", "extra"),
    [
        (credentials.refresh_workspace_credential, {"secret_reference": _REFERENCE}),
        (credentials.refresh_workspace_credential_from_secret, {"secret": "s"}),
    ],
)
def test_refresh_entrypoints_send_discriminated_payload_and_decode(
    monkeypatch: pytest.MonkeyPatch, function: Any, extra: dict[str, str]
) -> None:
    seen: list[bytes] = []

    async def run_child(payload: bytes) -> bytes:
        seen.append(payload)
        return _result()

    monkeypatch.setattr(credentials, "_run_credential_child", run_child)
    (value,) = extra.values()
    credential = asyncio.run(function("profile-1", _CONFIG, value, _HOST))
    assert credential == FreshCredential(_HOST, _AUTH)
    assert json.loads(seen[0])["profile"] == "profile-1"


@pytest.mark.parametrize(
    ("raw", "returncode"),
    [
        (_result(), 1),
        (b"", 0),
        (_result().rstrip(b"\n"), 0),
        (b"\xff\n", 0),
        (b"[]\n", 0),
        (b'{"status":"ok"}\n', 0),
        (_result(failure="x"), 0),
        (_result(host=5), 0),
        (_result(authorization=None), 0),
        (_result(host="h" * 2049), 0),
        (_result(authorization="A" * (60 * 1024 + 1)), 0),
        (_error("unknown"), 0),
        (_result(status="weird"), 0),
        (_result(status="error", host=_HOST, authorization=None, failure="other"), 0),
        (_result(status="error", host=None, authorization=_AUTH, failure="other"), 0),
        (b"x" * (credentials._MAX_OUTPUT_BYTES + 1) + b"\n", 0),
    ],
    ids=[f"report-{n}" for n in range(16)],
)
def test_validate_child_result_refuses_malformed_reports(raw: bytes, returncode: int) -> None:
    with pytest.raises(RuntimeError, match="identity_credential_child_failed"):
        credentials._validate_child_result(raw, returncode)


@pytest.mark.parametrize(
    "raw",
    [_result(), _error("rejected"), _error("transient_auth"), _error("other")],
    ids=["success", "rejected", "transient-auth", "other"],
)
def test_validate_child_result_accepts_closed_reports(raw: bytes) -> None:
    assert credentials._validate_child_result(raw, 0) == raw


@pytest.mark.parametrize(
    ("failure", "retryable", "code"),
    [
        ("transient_auth", True, "identity_credential_auth_transient"),
        ("transient_network", True, "identity_credential_network_transient"),
    ],
)
def test_decode_maps_transient_failures_to_retryable_errors(
    failure: str, retryable: bool, code: str
) -> None:
    with pytest.raises(CredentialRefreshError, match=code) as info:
        credentials._decode_credential_child(_error(failure), _HOST)
    assert info.value.retryable is retryable


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("host_mismatch", "restored_workspace_host_mismatch"),
        ("rejected", "identity_credential_rejected"),
        ("other", "identity_credential_child_failed"),
    ],
)
def test_decode_maps_terminal_failures(failure: str, code: str) -> None:
    with pytest.raises(RuntimeError, match=code) as info:
        credentials._decode_credential_child(_error(failure), _HOST)
    assert not isinstance(info.value, CredentialRefreshError)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (_result(host="https://other.example"), "restored_workspace_host_mismatch"),
        (_result(authorization=_TOKEN), "identity_workspace_credential_invalid"),
        (_result(authorization="Bearer  "), "identity_workspace_credential_invalid"),
        (_result(authorization="Bearer a\nb"), "identity_workspace_credential_invalid"),
    ],
    ids=["host-mismatch", "bare-token", "blank", "newline"],
)
def test_decode_refuses_mismatched_or_malformed_success(raw: bytes, code: str) -> None:
    with pytest.raises(RuntimeError, match=code):
        credentials._decode_credential_child(raw, _HOST)


@pytest.mark.parametrize(
    "host",
    ["http://h.example", "https://u@h.example", "https://u:p@h.example"],
    ids=["insecure-scheme", "userinfo", "userinfo-secret"],
)
def test_decode_refuses_unsafe_expected_host(host: str) -> None:
    raw = _result(host=host)
    with pytest.raises(RuntimeError, match="identity_workspace_host_invalid"):
        credentials._decode_credential_child(raw, host)


def test_child_environment_is_minimal_and_rooted() -> None:
    environment = credentials._child_environment()
    assert set(environment) == {"HOME", "PATH", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    assert environment["PYTHONPATH"] == str(credentials._PROJECT_ROOT)


def test_read_child_output_enforces_bound() -> None:
    async def read(data: bytes) -> bytes:
        stream = asyncio.StreamReader()
        stream.feed_data(data)
        stream.feed_eof()
        return await credentials._read_child_output(stream)

    limit = credentials._MAX_OUTPUT_BYTES
    assert len(asyncio.run(read(b"x" * limit))) == limit
    with pytest.raises(RuntimeError, match="identity_credential_child_output_limit"):
        asyncio.run(read(b"x" * (limit + 1)))


def test_settle_task_waits_through_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        release = asyncio.Event()
        cancelled = asyncio.Event()
        entered = asyncio.Event()
        shield = asyncio.shield

        async def observed_shield(task: asyncio.Future[Any]) -> Any:
            entered.set()
            try:
                return await shield(task)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr(credentials.asyncio, "shield", observed_shield)

        async def work() -> str:
            await release.wait()
            return "value"

        task = asyncio.create_task(work())
        settler = asyncio.create_task(credentials._settle_task(task))
        await entered.wait()
        settler.cancel()
        await cancelled.wait()
        assert not settler.done()
        assert not task.done()
        release.set()
        assert await settler == ("value", True)
        assert await credentials._settle_task(asyncio.create_task(work())) == ("value", False)

    asyncio.run(run())


_BLOCK = "import os; r, w = os.pipe(); os.read(r, 1)"


def _run(payload: bytes, code: str, timeout: float = 20.0) -> bytes:
    return asyncio.run(
        credentials._run_process(
            payload, command=(sys.executable, "-c", code), timeout_seconds=timeout
        )
    )


def test_run_process_feeds_stdin_and_returns_validated_report() -> None:
    code = f"import sys; sys.stdin.buffer.read(); sys.stdout.write({_result().decode()!r})"
    assert _run(b"{}", code) == _result()


def test_run_process_refuses_nonzero_exit() -> None:
    with pytest.raises(RuntimeError, match="identity_credential_child_failed"):
        _run(b"{}", "raise SystemExit(3)")


def test_run_process_refuses_invalid_output() -> None:
    with pytest.raises(RuntimeError, match="identity_credential_child_failed"):
        _run(b"{}", "print('not json')")


def test_run_process_deadline_kills_child_as_incomplete_observation() -> None:
    with pytest.raises(LiveQualificationIncomplete) as info:
        _run(b"{}", _BLOCK, timeout=0.2)
    assert info.value.reason == "qualification_workspace_identity_not_observed"


def test_run_process_timeout_drains_descendant_after_leader_exit() -> None:
    code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print('leader-exited', flush=True)"
    )
    with pytest.raises(LiveQualificationIncomplete) as info:
        _run(b"{}", code, timeout=0.2)
    assert info.value.reason == "qualification_workspace_identity_not_observed"


def test_run_process_preserves_unrelated_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    processes: list[_ControlledProcess] = []

    async def spawn(*_args: Any, **_kwargs: Any) -> _ControlledProcess:
        process = _ControlledProcess(_result(), raw_timeout=True)
        processes.append(process)
        return process

    monkeypatch.setattr(credentials.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(TimeoutError, match="producer_wait_timeout") as info:
        _run(b"{}", "pass", timeout=20)
    assert not isinstance(info.value, LiveQualificationIncomplete)
    assert processes[0].wait_calls == 2


def test_run_process_cancellation_retains_held_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        reader_waiting = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        process = _ControlledProcess(_result(), eof=False)
        readers: list[asyncio.Task[Any]] = []
        original_reader = credentials._read_child_output
        original_cleanup = credentials._cleanup_child

        async def spawn(*_args: Any, **_kwargs: Any) -> _ControlledProcess:
            return process

        async def observed_reader(output: asyncio.StreamReader) -> bytes:
            task = asyncio.current_task()
            assert task is not None
            readers.append(task)
            reader_waiting.set()
            return await original_reader(output)

        async def held_cleanup(
            child: asyncio.subprocess.Process, reader: asyncio.Task[Any] | None
        ) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            await original_cleanup(child, reader)

        monkeypatch.setattr(credentials.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(credentials, "_read_child_output", observed_reader)
        monkeypatch.setattr(credentials, "_cleanup_child", held_cleanup)
        operation = asyncio.create_task(
            credentials._run_process(
                b"{}",
                command=(sys.executable, "-c", "pass"),
                timeout_seconds=20,
            )
        )
        await reader_waiting.wait()
        operation.cancel()
        await cleanup_started.wait()
        assert not readers[0].cancelled()
        release_cleanup.set()
        process.stdout.feed_eof()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert readers[0].done()
        assert not readers[0].cancelled()
        assert readers[0].result() == _result()

    asyncio.run(run())


def test_run_process_cancellation_during_success_cleanup_remains_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        process = _ControlledProcess(_result())
        original_cleanup = credentials._cleanup_child

        async def spawn(*_args: Any, **_kwargs: Any) -> _ControlledProcess:
            return process

        async def held_cleanup(
            process: asyncio.subprocess.Process, reader: asyncio.Task[Any] | None
        ) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            await original_cleanup(process, reader)

        monkeypatch.setattr(credentials.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(credentials, "_cleanup_child", held_cleanup)
        code = f"import sys;sys.stdout.buffer.write({_result()!r})"
        operation = asyncio.create_task(
            credentials._run_process(
                b"{}", command=(sys.executable, "-c", code), timeout_seconds=20
            )
        )
        await cleanup_started.wait()
        operation.cancel()
        assert not operation.done()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert process.wait_calls == 2

    asyncio.run(run())


def test_run_process_refuses_when_no_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.sys, "executable", "")
    with pytest.raises(RuntimeError, match="identity_credential_child_unavailable"):
        _run(b"{}", "pass")


def test_run_process_refuses_unavailable_pipes_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 1
        stdin = None
        stdout = None
        returncode = 0

        async def wait(self) -> int:
            return 0

    async def spawn(*_args: object, **_kwargs: object) -> Process:
        return Process()

    monkeypatch.setattr(credentials.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="identity_credential_child_pipe_unavailable"):
        _run(b"{}", "pass")


def test_run_process_cancellation_during_spawn_reaps_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []

    async def run() -> None:
        entered = asyncio.Event()

        async def slow_spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            entered.set()
            process = await real(*args, **kwargs)
            spawned.append(process)
            return process

        monkeypatch.setattr(credentials.asyncio, "create_subprocess_exec", slow_spawn)
        task = asyncio.create_task(
            credentials._run_process(
                b"{}", command=(sys.executable, "-c", _BLOCK), timeout_seconds=20.0
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(spawned) == 1
        assert spawned[0].returncode is not None

    asyncio.run(run())


def test_cleanup_child_reaps_live_group_and_drains_reader() -> None:
    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _BLOCK,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        reader = asyncio.create_task(credentials._read_child_output(process.stdout))
        await credentials._cleanup_child(process, reader)
        assert process.returncode is not None
        assert await reader == b""
        # Exited children are only awaited, never signalled.
        await credentials._cleanup_child(process, None)

    asyncio.run(run())


def test_run_credential_child_uses_fixed_script_and_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def run_process(
        payload: bytes, *, command: tuple[str, ...], timeout_seconds: float
    ) -> bytes:
        seen.update(payload=payload, command=command, timeout=timeout_seconds)
        return b"ok"

    monkeypatch.setattr(credentials, "_run_process", run_process)
    assert asyncio.run(credentials._run_credential_child(b"p")) == b"ok"
    assert seen["command"][1:] == (str(credentials._CHILD_SCRIPT), "--internal-credential-child")
    assert seen["timeout"] == credentials._CHILD_TIMEOUT_SECONDS


def test_main_rejects_unknown_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", "--other"])
    assert credentials._main() == 2


def test_main_delegates_to_child_entry_with_bounded_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []
    monkeypatch.setattr(sys, "argv", ["prog", "--internal-credential-child"])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"request")))
    monkeypatch.setattr(credentials, "_credential_child_main", lambda raw: seen.append(raw) or 0)
    assert credentials._main() == 0
    assert seen == [b"request"]


def test_main_collapses_unexpected_child_failure_to_other(monkeypatch: pytest.MonkeyPatch) -> None:
    out = io.BytesIO()

    def boom(_raw: bytes) -> int:
        raise RuntimeError("private detail")

    monkeypatch.setattr(sys, "argv", ["prog", "--internal-credential-child"])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(b"")))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=out))
    monkeypatch.setattr(credentials, "_credential_child_main", boom)
    assert credentials._main() == 0
    report = json.loads(out.getvalue())
    assert report["status"] == "error"
    assert report["failure"] == "other"
    assert b"private" not in out.getvalue()
