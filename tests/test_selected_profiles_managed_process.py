"""Bounded process protocol controls for the selected managed-Postgres proof."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
import selected_profiles_managed_process as managed
import selected_profiles_managed_process_peer as managed_peer
import selected_profiles_process as process_helpers

from tether.client import Actor, Entry, JsonValue, RecoveredTurn


def _entry(
    index: int,
    kind: str,
    *,
    key: str | None = None,
    turn_id: str | None = None,
    **facts: JsonValue,
) -> Entry:
    return Entry(
        index=index,
        at=datetime.now(UTC),
        kind=kind,
        key=key,
        turn_id=turn_id,
        facts=facts,
    )


class _ScriptedPeer:
    def __init__(
        self,
        name: str,
        events: list[dict[str, object]],
        order: list[str],
        before: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.order = order
        self.before = before
        self.process = SimpleNamespace(returncode=None)

    async def command(self, command: str) -> dict[str, object]:
        self.order.append(self.name + ":" + command)
        event = self.events.pop(0)
        if self.before is not None:
            self.before(command, event)
        return event


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b'{"event":"ready"}\n', {"event": "ready"}),
        (b'{"event":"ready","private":"unused"}\n', None),
        (b"not-json\n", None),
        (b"x" * (managed._MAX_EVENT_BYTES + 1) + b"\n", None),
    ],
    ids=["known-event", "unknown-field", "malformed", "over-bound"],
)
def test_managed_peer_event_parser_is_bounded_and_closed(
    line: bytes, expected: dict[str, object] | None
) -> None:
    async def read() -> tuple[dict[str, object] | None, str | None]:
        stream = asyncio.StreamReader(limit=managed._MAX_EVENT_BYTES + 1)
        stream.feed_data(line)
        stream.feed_eof()
        process = cast(asyncio.subprocess.Process, SimpleNamespace(stdout=stream))
        try:
            return await managed._read_event(process), None
        except RuntimeError as error:
            return None, str(error)

    event, error = asyncio.run(read())
    assert event == expected
    if expected is None:
        assert error == "managed_peer_event_invalid"
    else:
        assert error is None


def test_managed_peer_environment_does_not_inherit_profile_or_database_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", "/private/fabricated-config")
    monkeypatch.setenv("PGPASSWORD", "-".join(("fabricated", "value")))
    monkeypatch.setenv("TETHER_PG_HOST", "fabricated-host")
    environment = managed._peer_environment()
    assert environment == {
        "PATH": environment["PATH"],
        "PYTHONPATH": environment["PYTHONPATH"],
        "PYTHONUTF8": "1",
    }
    assert "/examples/databricks-app/src" in environment["PYTHONPATH"]
    assert "/src" in environment["PYTHONPATH"]


def test_managed_user_actor_uses_bounded_static_sdk_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import selected_profiles_static_workspace as static_workspace
    from tether_databricks_example.postgres import LakebaseSettings, WorkspacePostgresClient

    selected: list[tuple[str, str]] = []
    infrastructure = SimpleNamespace(postgres=SimpleNamespace())
    user_client = SimpleNamespace(postgres=SimpleNamespace())

    class Credential:
        async def authorization(self) -> str:
            return " ".join(("Bearer", "fabricated-token"))

    def bounded_factory(host: str, bearer: str, /) -> WorkspacePostgresClient:
        selected.append((host, bearer))
        return cast(WorkspacePostgresClient, user_client)

    monkeypatch.setattr(static_workspace, "workspace_for_bearer", bounded_factory)
    source = static_workspace.managed_connection_source(
        LakebaseSettings(
            workspace_host="https://workspace.example.test",
            endpoint="fabricated-endpoint",
            host="database.example.test",
            database="fabricated-db",
            infra_role="infrastructure-role",
        ),
        database_user=lambda actor: actor.id,
        infrastructure_client=cast(WorkspacePostgresClient, infrastructure),
    )

    client = asyncio.run(source._client_for(Actor(id="database-user", credential=Credential())))
    assert client is user_client
    assert client is not infrastructure
    assert selected == [("https://workspace.example.test", "fabricated-token")]


def test_managed_peer_cleanup_kills_descendant_after_leader_exit_and_drains_eof() -> None:
    async def run() -> int | None:
        read_fd, exit_fd = os.pipe()
        descendant = "import sys; sys.stdin.buffer.read()"
        leader = (
            "import os,subprocess,sys; "
            f"subprocess.Popen([sys.executable, '-c', {descendant!r}], "
            "stdin=sys.stdin, stdout=sys.stdout, stderr=subprocess.DEVNULL); "
            "sys.stdout.write('leader-ready\\n'); sys.stdout.flush(); os._exit(0)"
        )
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                leader,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(exit_fd,),
            )
        finally:
            os.close(exit_fd)
        peer = managed._Peer(process)
        try:
            assert process.stdout is not None
            assert await process.stdout.readline() == b"leader-ready\n"
            # Only the leader inherits this pipe; EOF proves the leader exited
            # while its descendant still owns stdout.
            assert await asyncio.to_thread(os.read, read_fd, 1) == b""
            await peer.kill_and_reap()
            await asyncio.to_thread(process_helpers.wait_for_process_group_exit, process.pid)
            assert process.stdout.at_eof()
            return process.returncode
        finally:
            os.close(read_fd)
            if peer._cleanup_task is None:
                await peer.kill_and_reap()

    assert asyncio.run(run()) == 0


def test_managed_peer_cleanup_survives_repeated_cancellation_after_leader_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        kill_requested = asyncio.Event()
        drain_started = asyncio.Event()
        release_drain = asyncio.Event()
        group_exists = True

        def killpg(pgid: int, sig: int) -> None:
            nonlocal group_exists
            if sig == signal.SIGKILL:
                kill_requested.set()
            elif sig == 0 and not group_exists:
                raise ProcessLookupError

        async def communicate() -> tuple[bytes, bytes]:
            nonlocal group_exists
            drain_started.set()
            await release_drain.wait()
            group_exists = False
            return b"", b""

        process = cast(
            asyncio.subprocess.Process,
            SimpleNamespace(pid=321987, returncode=0, communicate=communicate),
        )
        monkeypatch.setattr(managed.os, "killpg", killpg)
        peer = managed._Peer(process)
        waiter = asyncio.create_task(peer.kill_and_reap())
        await kill_requested.wait()
        await drain_started.wait()

        loop = asyncio.get_running_loop()
        for _ in range(2):
            waiter.cancel()
            turn_complete = asyncio.Event()
            loop.call_soon(turn_complete.set)
            await turn_complete.wait()
            assert not waiter.done()

        release_drain.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert peer._cleanup_task is not None
        assert peer._cleanup_task.done()

    asyncio.run(run())


def test_process_group_permission_error_does_not_claim_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(pgid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(process_helpers.os, "killpg", denied)
    with pytest.raises(RuntimeError, match=r"^managed_peer_group_absence_unconfirmed$"):
        process_helpers.wait_for_process_group_exit(123)


def test_peer_launch_retains_spawn_and_cleans_child_when_canceled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        spawn_started = asyncio.Event()
        release_spawn = asyncio.Event()
        kill_requested = asyncio.Event()
        drain_started = asyncio.Event()
        release_drain = asyncio.Event()
        group_exists = True

        async def spawn(*args: object, **kwargs: object) -> object:
            spawn_started.set()
            await release_spawn.wait()

            async def communicate() -> tuple[bytes, bytes]:
                nonlocal group_exists
                drain_started.set()
                await release_drain.wait()
                group_exists = False
                return b"", b""

            return SimpleNamespace(
                pid=321988,
                returncode=0,
                stdin=None,
                stdout=None,
                communicate=communicate,
            )

        def killpg(pgid: int, sig: int) -> None:
            if sig == signal.SIGKILL:
                kill_requested.set()
            elif sig == 0 and not group_exists:
                raise ProcessLookupError

        monkeypatch.setattr(managed, "_peer_config", lambda *args, **kwargs: {})
        monkeypatch.setattr(managed, "_peer_environment", lambda: {})
        monkeypatch.setattr(managed.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(managed.os, "killpg", killpg)

        task = asyncio.create_task(
            managed._launch(
                cast(managed._Context, object()), mode="hold", session_id="s", turn_id="t"
            )
        )
        await spawn_started.wait()
        task.cancel()
        release_spawn.set()
        await kill_requested.wait()
        await drain_started.wait()
        assert not task.done()
        release_drain.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_managed_peer_prepares_resources_before_ready_and_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        open_started = asyncio.Event()
        release_open = asyncio.Event()
        ready_emitted = asyncio.Event()
        activate = asyncio.Event()
        held = asyncio.Event()
        events: list[str] = []
        resources = cast(managed_peer.PostgresResources, object())
        actor = cast(Actor, object())

        async def open_resources(config: object) -> tuple[object, object]:
            open_started.set()
            await release_open.wait()
            return resources, actor

        async def read_command() -> str:
            await activate.wait()
            return "activate"

        async def hold(config: object, opened: object, caller: object) -> None:
            assert opened is resources
            assert caller is actor
            held.set()

        async def close_resources(opened: object) -> None:
            assert opened is resources

        def emit(event: str, **values: object) -> None:
            events.append(event)
            if event == "ready":
                ready_emitted.set()

        monkeypatch.setattr(managed_peer, "_open_resources", open_resources)
        monkeypatch.setattr(managed_peer, "_read_command", read_command)
        monkeypatch.setattr(managed_peer, "_hold", hold)
        monkeypatch.setattr(managed_peer, "_close_resources", close_resources)
        monkeypatch.setattr(managed_peer, "_emit", emit)

        task = asyncio.create_task(managed_peer._serve({"mode": "hold"}))
        await open_started.wait()
        assert events == []
        release_open.set()
        await ready_emitted.wait()
        assert events == ["ready"]
        assert not held.is_set()
        activate.set()
        await task
        assert held.is_set()

    asyncio.run(run())


def test_natural_expiry_activates_only_after_both_peers_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    holder = _ScriptedPeer(
        "hold",
        [
            {"event": "turn_held"},
            {"event": "ok"},
            {"event": "refused", "command": "append_a", "reason": "lease_lost"},
            {"event": "refused", "command": "heartbeat", "reason": "lease_lost"},
            {"event": "closed"},
        ],
        order,
    )
    contender = _ScriptedPeer(
        "contender",
        [
            {"event": "in_progress"},
            {"event": "taken_over"},
            {"event": "ok"},
            {"event": "ok"},
            {"event": "ok"},
            {"event": "ok"},
            {"event": "finished"},
        ],
        order,
    )
    _install_fencing_doubles(monkeypatch, holder, contender)
    result = asyncio.run(managed.natural_expiry_fencing(cast(managed._Context, object())))
    assert order[:3] == ["hold:ready", "contender:ready", "hold:activate"]
    assert result["initial_competitor_blocked"] is True
    assert "contender:takeover" in order


def _install_fencing_doubles(
    monkeypatch: pytest.MonkeyPatch, holder: _ScriptedPeer, contender: _ScriptedPeer
) -> list[str]:
    ready: list[str] = []
    order = holder.order
    state = {"lease_active": False, "initial_state_observed": False}

    def before_hold(command: str, event: dict[str, object]) -> None:
        if command == "activate":
            assert ready == ["hold", "contender"]
            assert event["event"] == "turn_held"
            state["lease_active"] = True

    def before_contender(command: str, event: dict[str, object]) -> None:
        if command == "probe":
            assert state["lease_active"]
            state["initial_state_observed"] = event.get("event") == "in_progress"
        elif command == "takeover":
            assert state["initial_state_observed"]

    holder.before = before_hold
    contender.before = before_contender

    async def session(*args: object) -> str:
        return "session"

    async def launch(context: object, *, mode: str, session_id: str, turn_id: str) -> _ScriptedPeer:
        ready.append(mode)
        order.append(mode + ":ready")
        return holder if mode == "hold" else contender

    async def verify_projection(*args: object) -> None:
        return None

    async def reap(*args: object) -> None:
        order.append("cleanup")

    monkeypatch.setattr(managed, "_session", session)
    monkeypatch.setattr(managed, "_launch", launch)
    monkeypatch.setattr(managed, "_verify_fencing_projection", verify_projection)
    monkeypatch.setattr(managed, "_reap_peers", reap)
    return ready


def test_natural_expiry_missing_initial_witness_is_inconclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    holder = _ScriptedPeer("hold", [{"event": "turn_held"}, {"event": "ok"}], order)
    contender = _ScriptedPeer("contender", [{"event": "unexpected_acquire"}], order)
    _install_fencing_doubles(monkeypatch, holder, contender)
    with pytest.raises(managed.ManagedPeerInconclusive) as error:
        asyncio.run(managed.natural_expiry_fencing(cast(managed._Context, object())))
    assert str(error.value) == "managed_peer_initial_state_unobserved"
    assert order[:3] == ["hold:ready", "contender:ready", "hold:activate"]
    assert "contender:takeover" not in order


def test_fencing_projection_requires_interrupted_old_turn_and_only_successor_append() -> None:
    valid = (
        _entry(0, "turn_ended", turn_id="old", stop_reason="interrupted"),
        _entry(1, "message", key="current-b"),
    )
    assert managed._fencing_projection_valid(valid, "old")

    invalid = (
        (_entry(0, "turn_ended", turn_id="old", stop_reason="final"), valid[1]),
        (valid[0], _entry(1, "message", key="stale-a"), valid[1]),
        (valid[0],),
        (valid[0], valid[1], _entry(2, "message", key="current-b")),
    )
    assert all(not managed._fencing_projection_valid(entries, "old") for entries in invalid)


@pytest.mark.parametrize(
    ("session_id", "turn_id", "expected"),
    [
        ("session-a", "turn-a", True),
        ("session-b", "turn-a", False),
        ("session-a", "turn-b", False),
    ],
    ids=["exact-owner-turn", "wrong-session", "wrong-turn"],
)
def test_recovery_projection_matches_exact_session_and_turn(
    session_id: str, turn_id: str, expected: bool
) -> None:
    recovered = (RecoveredTurn(session_id="session-a", turn_id="turn-a", resumable=True),)
    assert managed._recovered_exactly(recovered, session_id, turn_id) is expected


def test_killed_recovery_waits_for_expired_lease_state_without_false_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        pause_entered = asyncio.Event()
        release_pause = asyncio.Event()
        calls = 0
        recovered = (RecoveredTurn(session_id="session-a", turn_id="turn-a", resumable=True),)

        async def recover_owner(actor: Actor) -> tuple[RecoveredTurn, ...]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ()
            return recovered

        async def controlled_pause() -> None:
            pause_entered.set()
            await release_pause.wait()

        monkeypatch.setattr(managed, "_recovery_poll_pause", controlled_pause)
        context = cast(
            managed._Context,
            SimpleNamespace(
                resources=SimpleNamespace(recover_owner=recover_owner),
                actor=cast(Actor, object()),
            ),
        )
        task = asyncio.create_task(managed._recover_until_visible(context))
        await pause_entered.wait()
        assert calls == 1
        assert not task.done()
        release_pause.set()
        assert await task == recovered
        assert calls == 2

    asyncio.run(run())


def test_unknown_intent_requires_one_unresolved_matching_invocation() -> None:
    intent = _entry(
        0,
        "tool_call",
        key="intent:qualification-unknown",
        turn_id="killed-turn",
        invocation_id="qualification-unknown",
        effect="effectful",
        status="started",
    )
    assert managed._unknown_intent_valid((intent,), "killed-turn")
    invalid = (
        (intent.model_copy(update={"facts": {**intent.facts, "status": "pending"}}),),
        (
            _entry(
                1,
                "tool_call",
                key="result:qualification-unknown",
                turn_id="killed-turn",
                invocation_id="qualification-unknown",
                effect="effectful",
                status="interrupted",
            ),
            intent,
        ),
        (
            intent,
            _entry(
                1,
                "resolved",
                turn_id="killed-turn",
                invocation_id="qualification-unknown",
                outcome="completed",
            ),
        ),
        (
            intent,
            _entry(
                1,
                "message",
                key="message:result:qualification-unknown",
                turn_id="killed-turn",
                invocation_id="qualification-unknown",
            ),
        ),
        (
            intent,
            _entry(
                1,
                "tool_call",
                key="intent:qualification-unknown",
                turn_id="killed-turn",
                invocation_id="qualification-unknown",
                effect="effectful",
                status="completed",
            ),
        ),
        (intent, intent.model_copy(update={"index": 1})),
        (intent.model_copy(update={"turn_id": "other-turn"}),),
    )
    assert all(not managed._unknown_intent_valid(entries, "killed-turn") for entries in invalid)
