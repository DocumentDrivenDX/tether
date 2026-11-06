"""Protocol controls for the private managed-Postgres process peer (in-process, fabricated)."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_managed_process_peer as peer

from tether.client import Actor, LeaseLost, NewEntry, TurnInProgress

_BASE: dict[str, Any] = {
    "mode": "hold",
    "owner": "owner-a",
    "session_id": "session-1",
    "turn_id": "turn-1",
    "workspace_host": "https://workspace.example.test",
    "workspace_authorization": "Bearer fabricated",
    "endpoint": "endpoint-1",
    "host": "database.example.test",
    "database": "db",
    "schema": "schema1",
    "infra_role": "infra",
    "ca_file": "/fabricated/ca.pem",
    "port": 5432,
    "lease_seconds": 30,
}


def _stdin(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))


def _lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def _frame(**override: object) -> bytes:
    return json.dumps({**_BASE, **override}).encode() + b"\n"


def test_emit_writes_one_compact_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    peer._emit("refused", command="x", reason="protocol")
    assert capsys.readouterr().out == '{"event":"refused","command":"x","reason":"protocol"}\n'


def test_config_accepts_exact_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    _stdin(monkeypatch, _frame())
    assert peer._config() == _BASE


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"x" * (peer._MAX_INPUT_BYTES + 1),
        json.dumps(_BASE).encode(),
        b"[1]\n",
        json.dumps({**_BASE, "extra": "x"}).encode() + b"\n",
        _frame(owner=""),
        _frame(owner=3),
        _frame(mode="other"),
        _frame(port="5432"),
        _frame(port=True),
        _frame(lease_seconds=1.5),
    ],
    ids=[
        "empty",
        "over-bound",
        "no-newline",
        "not-object",
        "extra-key",
        "empty-string",
        "non-string",
        "bad-mode",
        "string-port",
        "bool-port",
        "float-lease",
    ],
)
def test_config_refuses_invalid_frames(monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    _stdin(monkeypatch, data)
    with pytest.raises(ValueError, match="peer_input_invalid"):
        peer._config()


def test_connection_source_binds_static_bearer_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    import selected_profiles_identity_credentials as credentials
    import selected_profiles_static_workspace as static_workspace

    seen: dict[str, Any] = {}

    def fake_workspace(host: str, bearer: str, /) -> object:
        seen["workspace"] = (host, bearer)
        return "infrastructure"

    def fake_source(settings: Any, *, database_user: Any, infrastructure_client: Any) -> object:
        seen["settings"] = settings
        seen["user"] = database_user(SimpleNamespace(id="actor-7"))
        seen["infra"] = infrastructure_client
        return "source"

    monkeypatch.setattr(credentials, "workspace_for_bearer", fake_workspace)
    monkeypatch.setattr(static_workspace, "managed_connection_source", fake_source)
    assert peer._connection_source(_BASE) == "source"
    assert seen["workspace"] == ("https://workspace.example.test", "fabricated")
    assert seen["user"] == "actor-7"
    assert seen["infra"] == "infrastructure"
    assert seen["settings"].endpoint == "endpoint-1"
    assert seen["settings"].infra_role == "infra"


@pytest.mark.parametrize(
    "value", ["Bearer", "Basic abc", "abc", "Bearer "], ids=["bare", "basic", "plain", "blank"]
)
def test_connection_source_refuses_non_bearer_authorization(value: str) -> None:
    with pytest.raises(ValueError, match="peer_input_invalid"):
        peer._connection_source({**_BASE, "workspace_authorization": value})


def test_actor_credential_and_turn_started_entry() -> None:
    actor = peer._actor(_BASE)
    assert actor.id == "owner-a"
    assert asyncio.run(cast(Any, actor.credential).authorization()) == "Bearer fabricated"
    entry = peer._started("turn-9")
    assert (entry.kind, entry.turn_id) == ("turn_started", "turn-9")
    assert entry.facts["spec_id"] == "qualification"


def test_storage_limits_carry_lease() -> None:
    limits = peer._storage_limits(45)
    assert limits.lease_seconds == 45
    assert limits.io_seconds == 20


@pytest.mark.parametrize(
    ("aclose", "wait_closed", "raises"),
    [(True, None, False), (False, True, False), (False, False, True)],
)
def test_close_resources_requires_drain(
    aclose: bool, wait_closed: bool | None, raises: bool
) -> None:
    class Resources:
        async def aclose(self) -> bool:
            return aclose

        async def wait_closed(self) -> bool:
            assert wait_closed is not None
            return wait_closed

    async def run() -> None:
        await peer._close_resources(cast(Any, Resources()))

    if raises:
        with pytest.raises(RuntimeError, match="peer_resources_drain_incomplete"):
            asyncio.run(run())
    else:
        asyncio.run(run())


def test_open_resources_passes_profile_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_open(source: object, **kwargs: Any) -> str:
        captured["source"] = source
        captured.update(kwargs)
        return "resources"

    monkeypatch.setattr(peer, "_connection_source", lambda config: "source")
    monkeypatch.setattr(peer.PostgresResources, "open", fake_open)
    resources, actor = asyncio.run(peer._open_resources(_BASE))
    assert resources == "resources"
    assert actor.id == "owner-a"
    assert captured["source"] == "source"
    assert captured["schema"] == "schema1"
    assert captured["limits"].lease_seconds == 30
    assert captured["durability_profile"].endpoint_id == "endpoint-1"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", None),
        (b'{"command":"stop"}\n', "stop"),
        (b'{"command":"stop"}', "invalid"),
        (b"x" * 1025, "invalid"),
        (b"nope\n", "invalid"),
        (b"[1]\n", "invalid"),
        (b'{"command":"a","x":1}\n', "invalid"),
        (b'{"command":3}\n', "invalid"),
    ],
)
def test_read_command_is_bounded_and_closed(
    monkeypatch: pytest.MonkeyPatch, data: bytes, expected: str | None
) -> None:
    _stdin(monkeypatch, data)
    assert asyncio.run(peer._read_command()) == expected


class _Log:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.appended: list[list[NewEntry]] = []
        self.heartbeats = 0

    async def append(self, entries: list[NewEntry]) -> None:
        if self.fail is not None:
            raise self.fail
        self.appended.append(entries)

    async def heartbeat(self) -> None:
        if self.fail is not None:
            raise self.fail
        self.heartbeats += 1


@pytest.mark.parametrize(("command", "key"), [("append_a", "stale-a"), ("append_b", "current-b")])
def test_perform_operation_appends(
    command: str, key: str, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _Log()
    assert asyncio.run(peer._perform_operation(log, command, "t")) is True
    assert log.appended[0][0].key == key
    assert _lines(capsys) == [{"event": "ok", "command": command}]


def test_perform_operation_heartbeat_and_refusals(capsys: pytest.CaptureFixture[str]) -> None:
    log = _Log()
    assert asyncio.run(peer._perform_operation(log, "heartbeat", "t")) is True
    assert log.heartbeats == 1
    lost = _Log(LeaseLost("gone"))
    assert asyncio.run(peer._perform_operation(lost, "heartbeat", "t")) is True
    other = _Log(RuntimeError("boom"))
    assert asyncio.run(peer._perform_operation(other, "append_a", "t")) is True
    assert asyncio.run(peer._perform_operation(log, "bogus", "t")) is False
    assert _lines(capsys) == [
        {"event": "ok", "command": "heartbeat"},
        {"event": "refused", "command": "heartbeat", "reason": "lease_lost"},
        {"event": "refused", "command": "append_a", "reason": "operation_refused"},
        {"event": "refused", "command": "unknown", "reason": "protocol"},
    ]


def test_execute_command_terminals(capsys: pytest.CaptureFixture[str]) -> None:
    log = _Log()
    assert asyncio.run(peer._execute_command(log, "stop", "t")) is False
    assert asyncio.run(peer._execute_command(log, "close_log", "t")) is False
    assert asyncio.run(peer._execute_command(log, "finish_b", "turn-x")) is False
    ended = log.appended[0][0]
    assert (ended.kind, ended.turn_id, ended.facts) == (
        "turn_ended",
        "turn-x",
        {"stop_reason": "final"},
    )
    assert _lines(capsys) == [{"event": "stopped"}]


def _script_commands(monkeypatch: pytest.MonkeyPatch, commands: list[str | None]) -> None:
    queue = list(commands)

    async def read() -> str | None:
        return queue.pop(0)

    monkeypatch.setattr(peer, "_read_command", read)


@pytest.mark.parametrize(
    ("commands", "expected"),
    [
        ([None], None),
        (["invalid"], None),
        (["heartbeat", "stop"], None),
        (["heartbeat", "close_log"], "close_log"),
        (["finish_b"], "finish_b"),
    ],
)
def test_command_loop_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    commands: list[str | None],
    expected: str | None,
) -> None:
    _script_commands(monkeypatch, list(commands))
    assert asyncio.run(peer._commands(_Log(), "t")) == expected
    capsys.readouterr()


def test_command_loop_reports_protocol_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_commands(monkeypatch, ["invalid"])
    asyncio.run(peer._commands(_Log(), "t"))
    assert _lines(capsys) == [{"event": "refused", "command": "invalid", "reason": "protocol"}]


class _Store:
    def __init__(
        self, log: _Log, *, enter: Exception | None = None, exit_: Exception | None = None
    ):
        self.log = log
        self.enter = enter
        self.exit = exit_
        self.turns: list[tuple[str, str, str]] = []

    def for_actor(self, actor: Actor) -> _Store:
        return self

    @asynccontextmanager
    async def turn(self, session_id: str, owner: str, *, turn_id: str):
        self.turns.append((session_id, owner, turn_id))
        if self.enter is not None:
            raise self.enter
        yield self.log
        if self.exit is not None:
            raise self.exit


def _resources(store: _Store) -> Any:
    return SimpleNamespace(storage=SimpleNamespace(store=store))


_ACTOR = cast(Actor, SimpleNamespace(id="owner-a"))


def test_hold_normal_turn_emits_held_then_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_commands(monkeypatch, ["close_log"])
    log = _Log()
    store = _Store(log)
    asyncio.run(peer._hold(_BASE, _resources(store), _ACTOR))
    assert store.turns == [("session-1", "owner-a", "turn-1")]
    assert log.appended[0][0].kind == "turn_started"
    assert [e["event"] for e in _lines(capsys)] == ["turn_held", "closed"]


def test_hold_unknown_mode_commits_unresolved_intent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_commands(monkeypatch, [None])
    log = _Log()
    asyncio.run(peer._hold({**_BASE, "mode": "unknown"}, _resources(_Store(log)), _ACTOR))
    intent = log.appended[1][0]
    assert intent.kind == "tool_call"
    assert intent.facts["status"] == "started"
    assert intent.facts["invocation_id"] == "qualification-unknown"
    assert [e["event"] for e in _lines(capsys)] == ["intent_committed"]


def test_hold_stale_close_reports_close_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_commands(monkeypatch, ["close_log"])
    store = _Store(_Log(), exit_=LeaseLost("stale"))
    asyncio.run(peer._hold(_BASE, _resources(store), _ACTOR))
    assert _lines(capsys)[-1] == {"event": "close_refused", "reason": "lease_lost"}


def test_hold_lease_loss_without_close_command_propagates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_commands(monkeypatch, ["stop"])
    store = _Store(_Log(), exit_=LeaseLost("stale"))
    with pytest.raises(LeaseLost):
        asyncio.run(peer._hold(_BASE, _resources(store), _ACTOR))
    capsys.readouterr()


def test_probe_once_reports_in_progress_or_unexpected(
    capsys: pytest.CaptureFixture[str],
) -> None:
    busy = _Store(_Log(), enter=TurnInProgress("busy"))
    asyncio.run(peer._probe_once(_BASE, _resources(busy), _ACTOR))
    free = _Store(_Log())
    asyncio.run(peer._probe_once(_BASE, _resources(free), _ACTOR))
    assert [e["event"] for e in _lines(capsys)] == ["in_progress", "unexpected_acquire"]


def test_takeover_polls_until_lease_expires_then_finishes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    attempts = {"n": 0}
    log = _Log()

    class Flaky(_Store):
        @asynccontextmanager
        async def turn(self, session_id: str, owner: str, *, turn_id: str):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise TurnInProgress("busy")
            yield self.log

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(peer.asyncio, "sleep", fake_sleep)
    _script_commands(monkeypatch, ["finish_b"])
    asyncio.run(peer._takeover(_BASE, _resources(Flaky(log)), _ACTOR))
    assert attempts["n"] == 3
    assert sleeps == [0.25, 0.25]
    assert [e["event"] for e in _lines(capsys)] == ["taken_over", "finished"]


def test_takeover_without_finish_emits_no_finished(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_commands(monkeypatch, ["stop"])
    asyncio.run(peer._takeover(_BASE, _resources(_Store(_Log())), _ACTOR))
    assert [e["event"] for e in _lines(capsys)] == ["taken_over", "stopped"]


@pytest.mark.parametrize(
    ("commands", "events"),
    [
        ([None], []),
        (["invalid"], [{"event": "refused", "command": "invalid", "reason": "protocol"}]),
        (["probe", "stop"], [{"event": "in_progress"}, {"event": "stopped"}]),
        (["wat"], [{"event": "refused", "command": "unknown", "reason": "protocol"}]),
        (["takeover"], [{"event": "taken_over"}, {"event": "finished"}]),
    ],
)
def test_contender_loop(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    commands: list[str | None],
    events: list[dict[str, object]],
) -> None:
    queue: list[str | None] = list(commands)
    if commands == ["takeover"]:
        queue = ["takeover", "finish_b"]
    busy_then_free = _Store(_Log(), enter=TurnInProgress("busy"))
    store = busy_then_free if "probe" in commands else _Store(_Log())
    _script_commands(monkeypatch, queue)
    asyncio.run(peer._serve_contender(_BASE, _resources(store), _ACTOR))
    assert _lines(capsys) == events


def test_serve_contender_mode_closes_resources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed: list[object] = []
    resources = _resources(_Store(_Log()))

    async def open_resources(config: object) -> tuple[Any, Any]:
        return resources, _ACTOR

    async def close(value: object) -> None:
        closed.append(value)

    monkeypatch.setattr(peer, "_open_resources", open_resources)
    monkeypatch.setattr(peer, "_close_resources", close)
    _script_commands(monkeypatch, ["stop"])
    asyncio.run(peer._serve({**_BASE, "mode": "contender"}))
    assert closed == [resources]
    assert [e["event"] for e in _lines(capsys)] == ["ready", "stopped"]


def test_serve_refuses_bad_activation_but_still_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed: list[object] = []

    async def open_resources(config: object) -> tuple[Any, Any]:
        return "res", _ACTOR

    async def close(value: object) -> None:
        closed.append(value)

    monkeypatch.setattr(peer, "_open_resources", open_resources)
    monkeypatch.setattr(peer, "_close_resources", close)
    _script_commands(monkeypatch, ["heartbeat"])
    with pytest.raises(ValueError, match="peer_activation_invalid"):
        asyncio.run(peer._serve(_BASE))
    assert closed == ["res"]
    capsys.readouterr()


def test_main_guard_reports_setup_failure_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import runpy

    _stdin(monkeypatch, b"")
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(peer.__file__, run_name="__main__")
    assert exit_info.value.code == 1
    assert _lines(capsys) == [{"event": "failed", "reason": "setup_or_cleanup"}]
