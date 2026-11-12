"""Parent-observation controls for the isolated asyncpg oracle process."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

import pytest
import selected_profiles_pg_oracle as oracle
from pg_oracle_unit_support import _REQUEST, _VERSION, _report
from qualify_live_apps_incomplete import LiveQualificationIncomplete


class _Stdin:
    def __init__(self) -> None:
        self.payload = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.payload += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _Child:
    pid = 4242

    def __init__(
        self,
        output: bytes,
        returncode: int | None,
        *,
        pipes: bool = True,
        eof: bool = True,
    ) -> None:
        self.stdin = _Stdin() if pipes else None
        self.stdout = asyncio.StreamReader() if pipes else None
        if self.stdout is not None:
            self.stdout.feed_data(output)
            if eof:
                self.stdout.feed_eof()
        self._final = returncode
        self.returncode: int | None = None if returncode is None else returncode

    async def wait(self) -> int:
        return self._final if self._final is not None else 0


def _probe(**override: Any) -> Any:
    arguments = {k: v for k, v in _REQUEST.items()} | override
    return oracle.probe_database(**arguments)


def _install_spawn(
    monkeypatch: pytest.MonkeyPatch, make: Callable[[], _Child]
) -> tuple[list[Any], list[_Child]]:
    spawned: list[Any] = []
    children: list[_Child] = []

    async def spawn(*args: Any, **kwargs: Any) -> _Child:
        spawned.append((args, kwargs))
        children.append(make())
        return children[-1]

    monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
    return spawned, children


def test_probe_database_sends_request_and_decodes_child_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned, children = _install_spawn(
        monkeypatch,
        lambda: _Child(_report(kind="auth_refused", current_user=None, sqlstate="28P01"), 0),
    )
    result = asyncio.run(_probe())
    assert result == oracle.OracleResult("auth_refused", sqlstate="28P01")
    stdin = children[0].stdin
    assert stdin is not None
    assert json.loads(stdin.payload) == _REQUEST
    assert stdin.closed
    assert spawned[0][0][-1] == "scripts.selected_profiles_pg_oracle"
    assert spawned[0][1]["env"] == oracle._child_environment()


def test_probe_database_refuses_oversize_input(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned, _children = _install_spawn(monkeypatch, lambda: _Child(b"", 0))
    monkeypatch.setattr(oracle, "MAX_INPUT_BYTES", 10)
    with pytest.raises(RuntimeError, match="database_oracle_input_limit"):
        asyncio.run(_probe())
    assert spawned == []


def test_probe_database_refuses_missing_pipes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_spawn(monkeypatch, lambda: _Child(b"", 0, pipes=False))
    with pytest.raises(RuntimeError, match="database_oracle_pipe_unavailable"):
        asyncio.run(_probe())


def test_probe_database_refuses_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_spawn(monkeypatch, lambda: _Child(_report(), 3))
    with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
        asyncio.run(_probe())


def test_probe_database_refuses_child_without_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Unfinished(_Child):
        async def wait(self) -> int:
            # Report completion but leave returncode unset; mark exited at cleanup time.
            self.returncode = None
            return 0

    children: list[_Child] = []

    def make() -> _Child:
        children.append(Unfinished(_report(), 0))
        children[0].returncode = None
        return children[0]

    _install_spawn(monkeypatch, make)

    async def run() -> None:
        try:
            await _probe()
        finally:
            children[0].returncode = 0

    with pytest.raises(RuntimeError, match="database_oracle_child_failed"):
        asyncio.run(run())


def test_probe_database_preserves_unrelated_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RawTimeout(_Child):
        def __init__(self) -> None:
            super().__init__(_report(), 0)
            self.wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise TimeoutError("producer_wait_timeout")
            return await super().wait()

    children: list[RawTimeout] = []

    def make() -> RawTimeout:
        child = RawTimeout()
        children.append(child)
        return child

    _install_spawn(monkeypatch, make)
    with pytest.raises(TimeoutError, match="producer_wait_timeout") as info:
        asyncio.run(_probe())
    assert not isinstance(info.value, LiveQualificationIncomplete)
    assert children[0].wait_calls == 2


def test_probe_database_cancellation_retains_held_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        reader_waiting = asyncio.Event()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        processes: list[Any] = []
        readers: list[asyncio.Task[Any]] = []
        original_reader = oracle._read_child_output
        original_cleanup = oracle._cleanup_child
        child = _Child(_report(), 0, eof=False)

        async def spawn(*_args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            del kwargs
            processes.append(child)
            return child  # type: ignore[return-value]

        async def held_cleanup(
            process: asyncio.subprocess.Process, reader: asyncio.Task[Any] | None
        ) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            await original_cleanup(process, reader)

        async def observed_reader(output: asyncio.StreamReader) -> bytes:
            task = asyncio.current_task()
            assert task is not None
            readers.append(task)
            reader_waiting.set()
            return await original_reader(output)

        monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(oracle, "_read_child_output", observed_reader)
        monkeypatch.setattr(oracle, "_cleanup_child", held_cleanup)
        operation = asyncio.create_task(_probe())
        await reader_waiting.wait()
        operation.cancel()
        await cleanup_started.wait()
        assert not readers[0].cancelled()
        assert child.stdout is not None
        child.stdout.feed_eof()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert readers[0].done()
        assert not readers[0].cancelled()
        assert readers[0].result() == _report()
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_probe_database_cancellation_during_success_cleanup_remains_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        processes: list[asyncio.subprocess.Process] = []
        original_spawn = asyncio.create_subprocess_exec
        original_cleanup = oracle._cleanup_child

        async def spawn(*_args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            process = await original_spawn(
                sys.executable,
                "-c",
                f"import sys;sys.stdout.buffer.write({_report()!r})",
                **kwargs,
            )
            processes.append(process)
            return process

        async def held_cleanup(
            process: asyncio.subprocess.Process, reader: asyncio.Task[Any] | None
        ) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            await original_cleanup(process, reader)

        monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(oracle, "_cleanup_child", held_cleanup)
        operation = asyncio.create_task(_probe())
        await cleanup_started.wait()
        operation.cancel()
        assert not operation.done()
        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_probe_database_maps_deadline_to_incomplete_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(oracle, "CHILD_TIMEOUT_SECONDS", 0.01)
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*_args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            return await real_spawn(
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.read(); time.sleep(60)",
                **kwargs,
            )

        monkeypatch.setattr(oracle.asyncio, "create_subprocess_exec", spawn)
        with pytest.raises(LiveQualificationIncomplete) as info:
            await _probe()
        assert info.value.reason == "qualification_database_credential_not_observed"

    asyncio.run(run())


@pytest.mark.parametrize(
    ("result", "ok"),
    [
        (oracle.OracleResult("auth_refused", sqlstate="28P01"), True),
        (oracle.OracleResult("connected", current_user="caller"), False),
        (oracle.OracleResult("other_failure"), False),
    ],
)
def test_require_authentication_refusal_returns_only_classified_refusals(
    monkeypatch: pytest.MonkeyPatch, result: oracle.OracleResult, ok: bool
) -> None:
    async def probe(**_kwargs: object) -> oracle.OracleResult:
        return result

    monkeypatch.setattr(oracle, "probe_database", probe)
    if ok:
        assert asyncio.run(oracle.require_authentication_refusal(**_REQUEST)) == (
            "28P01",
            _VERSION,
        )
    else:
        with pytest.raises(RuntimeError, match="database_auth_refusal_unclassified"):
            asyncio.run(oracle.require_authentication_refusal(**_REQUEST))
