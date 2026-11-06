"""Orchestration controls for the managed-Postgres process proof (fakes at process/DB edges)."""

from __future__ import annotations

import asyncio
import json
import signal
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_managed_process as managed
import selected_profiles_static_workspace as static_workspace
from databricks.sdk import WorkspaceClient
from selected_profiles_managed_observation import ObservationWatchdogExpired

from tether.client import Entry, RecoveredTurn


def _entry(index: int, kind: str, **fields: Any) -> Entry:
    key = fields.pop("key", None)
    turn_id = fields.pop("turn_id", None)
    return Entry(
        index=index, at=datetime.now(UTC), kind=kind, key=key, turn_id=turn_id, facts=fields
    )


class _Stdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None


class _Proc:
    """Fake subprocess whose stdout reader is built lazily inside the running loop."""

    pid = 999_991

    def __init__(self, lines: tuple[bytes, ...], with_stdin: bool, with_stdout: bool) -> None:
        self.returncode: int | None = None
        self.stdin = _Stdin() if with_stdin else None
        self._lines = lines
        self._with_stdout = with_stdout
        self._stdout: asyncio.StreamReader | None = None

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        if not self._with_stdout:
            return None
        if self._stdout is None:
            self._stdout = asyncio.StreamReader(limit=managed._MAX_EVENT_BYTES)
            for line in self._lines:
                self._stdout.feed_data(line)
            self._stdout.feed_eof()
        return self._stdout


def _process(*lines: bytes, with_stdin: bool = True, with_stdout: bool = True) -> Any:
    return _Proc(lines, with_stdin, with_stdout)


def _event(**values: object) -> bytes:
    return json.dumps(values).encode() + b"\n"


# ---------------------------------------------------------------- peer protocol


def test_peer_command_writes_one_bounded_frame_and_reads_event() -> None:
    async def run() -> None:
        process = _process(_event(event="ok", command="heartbeat"))
        peer = managed._Peer(process)
        assert await peer.command("heartbeat") == {"event": "ok", "command": "heartbeat"}
        assert process.stdin.writes == [b'{"command":"heartbeat"}\n']

    asyncio.run(run())


def test_peer_command_refuses_missing_stdin_and_oversize_command() -> None:
    async def run() -> None:
        with pytest.raises(RuntimeError, match="managed_peer_stdin_unavailable"):
            await managed._Peer(_process(with_stdin=False)).command("x")
        with pytest.raises(RuntimeError, match="managed_peer_command_invalid"):
            await managed._Peer(_process()).command("c" * 1100)

    asyncio.run(run())


@pytest.mark.parametrize(
    "line",
    [
        b"",
        b"[1]\n",
        _event(event=3),
        _event(event="ok", command="x" * 65),
        _event(event="ok", extra="x"),
        _event(event="ok", command=3),
    ],
)
def test_read_event_refuses_invalid_events(line: bytes) -> None:
    async def run() -> None:
        await managed._read_event(_process(line))

    with pytest.raises(RuntimeError, match="managed_peer_event_invalid"):
        asyncio.run(run())


def test_read_event_refuses_missing_stdout() -> None:
    with pytest.raises(RuntimeError, match="managed_peer_stdout_unavailable"):
        asyncio.run(managed._read_event(_process(with_stdout=False)))


def test_reap_peers_skips_absent_and_reports_cleanup_failure() -> None:
    reaped: list[str] = []

    class Peer:
        def __init__(self, name: str, fail: bool) -> None:
            self.name, self.fail = name, fail

        async def kill_and_reap(self) -> None:
            reaped.append(self.name)
            if self.fail:
                raise RuntimeError("secret detail")

    async def run() -> None:
        await managed._reap_peers(None, cast(Any, Peer("a", False)), None)
        with pytest.raises(RuntimeError, match=r"^managed_peer_cleanup_failed$"):
            await managed._reap_peers(cast(Any, Peer("b", True)), cast(Any, Peer("c", False)))

    asyncio.run(run())
    assert sorted(reaped) == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("aclose", "wait", "ok"), [(True, None, True), (False, True, True), (False, False, False)]
)
def test_close_resources_requires_complete_drain(aclose: bool, wait: bool | None, ok: bool) -> None:
    class Resources:
        async def aclose(self) -> bool:
            return aclose

        async def wait_closed(self) -> bool:
            return bool(wait)

    run = managed._close_resources(cast(Any, Resources()))
    if ok:
        asyncio.run(run)
    else:
        with pytest.raises(RuntimeError, match="managed_resources_drain_incomplete"):
            asyncio.run(run)


def test_storage_limits_are_fixed_and_bounded() -> None:
    limits = managed._storage_limits()
    assert (limits.lease_seconds, limits.io_seconds, limits.shutdown_seconds) == (30, 20, 5)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({"Authorization": "Bearer t"}, "Bearer t"), ({"authorization": "Bearer u"}, "Bearer u")],
    ids=["title-case", "lower-case"],
)
def test_workspace_authorization_accepts_bearer_in_either_case(
    headers: dict[str, str], expected: str
) -> None:
    workspace = SimpleNamespace(config=SimpleNamespace(authenticate=lambda: headers))
    assert managed._workspace_authorization(cast(WorkspaceClient, workspace)) == expected


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic x"}, {"Authorization": 5}])
def test_workspace_authorization_refuses_non_bearer(headers: dict[str, object]) -> None:
    workspace = SimpleNamespace(config=SimpleNamespace(authenticate=lambda: headers))
    with pytest.raises(RuntimeError, match="managed_process_credential_unavailable"):
        managed._workspace_authorization(cast(WorkspaceClient, workspace))


_PROFILE = SimpleNamespace(
    pg_endpoint="endpoint-1",
    pg_host="database.example.test",
    pg_database="db",
    pg_schema="schema1",
    ca_file="/fabricated/ca.pem",
)


def _context(**override: Any) -> Any:
    values: dict[str, Any] = {
        "workspace": SimpleNamespace(config=SimpleNamespace(host="https://w.example.test")),
        "profile": _PROFILE,
        "owner": "owner-a",
        "infra_role": "infra",
        "port": 5432,
        "authorization": "Bearer t",
        "actor": SimpleNamespace(id="owner-a"),
    }
    return SimpleNamespace(**(values | override))


def test_peer_config_carries_exact_closed_key_set() -> None:
    config = managed._peer_config(_context(), mode="hold", session_id="s", turn_id="t")
    assert len(config) == 14
    assert {"mode", "owner", "session_id", "turn_id", "endpoint"} <= set(config)
    assert config["workspace_authorization"] == "Bearer t"
    assert config["lease_seconds"] == 30


# ---------------------------------------------------------------- _launch


def _patch_launch(monkeypatch: pytest.MonkeyPatch, process: Any) -> list[str]:
    reaped: list[str] = []

    async def spawn(*_args: object, **_kwargs: object) -> Any:
        return process

    async def kill_and_reap(self: Any) -> None:
        reaped.append("reaped")

    monkeypatch.setattr(managed, "_peer_config", lambda *_a, **_k: {"mode": "hold"})
    monkeypatch.setattr(managed, "_peer_environment", lambda: {})
    monkeypatch.setattr(managed.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(managed._Peer, "kill_and_reap", kill_and_reap)
    return reaped


def _launch() -> Any:
    return managed._launch(cast(Any, object()), mode="hold", session_id="s", turn_id="t")


def test_launch_sends_config_frame_and_returns_ready_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _process(_event(event="ready"))
    reaped = _patch_launch(monkeypatch, process)
    peer = asyncio.run(_launch())
    assert peer.process is process
    assert process.stdin.writes == [b'{"mode":"hold"}\n']
    assert reaped == []


def test_launch_refuses_and_reaps_when_peer_does_not_report_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaped = _patch_launch(monkeypatch, _process(_event(event="failed", reason="x")))
    with pytest.raises(RuntimeError, match="managed_peer_start_refused"):
        asyncio.run(_launch())
    assert reaped == ["reaped"]


def test_launch_reaps_when_first_event_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    reaped = _patch_launch(monkeypatch, _process(b""))
    with pytest.raises(RuntimeError, match="managed_peer_event_invalid"):
        asyncio.run(_launch())
    assert reaped == ["reaped"]


def test_launch_reaps_when_stdin_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    reaped = _patch_launch(monkeypatch, _process(with_stdin=False))
    with pytest.raises(RuntimeError, match="managed_peer_stdin_unavailable"):
        asyncio.run(_launch())
    assert reaped == ["reaped"]


def test_launch_refuses_oversize_config_before_spawning(monkeypatch: pytest.MonkeyPatch) -> None:
    reaped = _patch_launch(monkeypatch, _process())
    monkeypatch.setattr(managed, "_MAX_INPUT_BYTES", 4)
    with pytest.raises(RuntimeError, match="managed_peer_config_too_large"):
        asyncio.run(_launch())
    assert reaped == []


# ---------------------------------------------------------------- _managed_context


class _Resources:
    def __init__(self) -> None:
        self.closed = 0
        self.storage = SimpleNamespace(
            store=SimpleNamespace(for_actor=lambda actor: ("store", actor))
        )

    async def aclose(self) -> bool:
        self.closed += 1
        return True


def _workspace(user: str | None = "owner-a") -> Any:
    return SimpleNamespace(
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name=user)),
        config=SimpleNamespace(
            host="https://w.example.test", authenticate=lambda: {"Authorization": "Bearer t"}
        ),
    )


_VALUES = {
    "TETHER_SERVICE_CALLER_DATABASE_ROLE": "owner-a",
    "TETHER_PG_INFRA_ROLE": "infra",
    "TETHER_PG_PORT": "6543",
}


def _patch_context(monkeypatch: pytest.MonkeyPatch, resources: _Resources) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def open_(source: Any, **kwargs: Any) -> _Resources:
        seen["open"] = (source, kwargs)
        return resources

    def source(settings: Any, *, database_user: Any, infrastructure_client: Any) -> str:
        seen["settings"] = settings
        seen["user"] = database_user(SimpleNamespace(id="u-1"))
        seen["infra"] = infrastructure_client
        return "source"

    monkeypatch.setattr(managed, "profile_from_environment", lambda values: _PROFILE)
    monkeypatch.setattr(managed.PostgresResources, "open", open_)
    monkeypatch.setattr(static_workspace, "managed_connection_source", source)
    return seen


def test_managed_context_binds_owner_actor_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = _Resources()
    seen = _patch_context(monkeypatch, resources)
    workspace = _workspace()

    async def run() -> None:
        async with managed._managed_context(workspace, dict(_VALUES)) as context:
            assert context.owner == "owner-a"
            assert context.port == 6543
            assert context.authorization == "Bearer t"
            assert context.store == ("store", context.actor)
            assert resources.closed == 0
        assert resources.closed == 1

    asyncio.run(run())
    assert seen["settings"].infra_role == "infra"
    assert seen["settings"].port == 6543
    assert seen["user"] == "u-1"
    assert seen["infra"] is workspace
    assert seen["open"][1]["durability_profile"].endpoint_id == "endpoint-1"
    assert seen["open"][1]["schema"] == "schema1"


def test_managed_context_closes_resources_when_body_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    resources = _Resources()
    _patch_context(monkeypatch, resources)

    async def run() -> None:
        async with managed._managed_context(_workspace(), dict(_VALUES)):
            raise ValueError("body")

    with pytest.raises(ValueError, match="body"):
        asyncio.run(run())
    assert resources.closed == 1


@pytest.mark.parametrize(
    ("values", "user", "code"),
    [
        ({"TETHER_PG_INFRA_ROLE": "infra"}, "owner-a", "managed_process_caller_invalid"),
        ({"TETHER_SERVICE_CALLER_DATABASE_ROLE": "o"}, "o", "managed_process_caller_invalid"),
        (
            {"TETHER_SERVICE_CALLER_DATABASE_ROLE": "same", "TETHER_PG_INFRA_ROLE": "same"},
            "same",
            "managed_process_caller_invalid",
        ),
        (dict(_VALUES), "someone-else", "managed_process_caller_mismatch"),
        (dict(_VALUES), None, "managed_process_caller_mismatch"),
    ],
)
def test_managed_context_refuses_caller_drift_before_opening(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], user: str | None, code: str
) -> None:
    resources = _Resources()
    seen = _patch_context(monkeypatch, resources)

    async def run() -> None:
        async with managed._managed_context(_workspace(user), values):
            raise AssertionError("must not enter")

    with pytest.raises(RuntimeError, match=code):
        asyncio.run(run())
    assert "open" not in seen


# ---------------------------------------------------------------- verification flows


class _Scripted:
    def __init__(self, *events: dict[str, object], returncode: int | None = None) -> None:
        self.events = list(events)
        self.commands: list[str] = []
        self.process = SimpleNamespace(returncode=returncode)
        self.reaped = 0

    async def command(self, command: str) -> dict[str, object]:
        self.commands.append(command)
        return self.events.pop(0)

    async def kill_and_reap(self) -> None:
        self.reaped += 1
        self.process.returncode = -signal.SIGKILL


_LOST: dict[str, object] = {"event": "refused", "command": "", "reason": "lease_lost"}


def _lost(command: str) -> dict[str, object]:
    return {**_LOST, "command": command}


def test_verify_stale_owner_requires_both_fenced_operations_and_successor_liveness() -> None:
    original = _Scripted(_lost("append_a"), _lost("heartbeat"), {"event": "closed"})
    contender = _Scripted({"event": "ok"}, {"event": "ok"})
    asyncio.run(managed._verify_stale_owner(cast(Any, original), cast(Any, contender)))
    assert original.commands == ["append_a", "heartbeat", "close_log"]
    assert contender.commands == ["heartbeat", "heartbeat"]


@pytest.mark.parametrize(
    ("original", "contender", "code"),
    [
        ([{"event": "ok"}], [], "managed_peer_stale_write_not_fenced"),
        ([_lost("append_a")], [{"event": "refused"}], "managed_peer_successor_heartbeat_failed"),
        (
            [_lost("append_a"), {"event": "ok"}],
            [{"event": "ok"}],
            "managed_peer_stale_write_not_fenced",
        ),
        (
            [_lost("append_a"), _lost("heartbeat")],
            [{"event": "ok"}, {"event": "refused"}],
            "managed_peer_successor_heartbeat_failed",
        ),
        (
            [_lost("append_a"), _lost("heartbeat"), {"event": "weird"}],
            [{"event": "ok"}, {"event": "ok"}],
            "managed_peer_stale_release_failed",
        ),
    ],
)
def test_verify_stale_owner_refusals(
    original: list[dict[str, object]], contender: list[dict[str, object]], code: str
) -> None:
    with pytest.raises(RuntimeError, match=code):
        asyncio.run(
            managed._verify_stale_owner(
                cast(Any, _Scripted(*original)), cast(Any, _Scripted(*contender))
            )
        )


def _install_fencing(
    monkeypatch: pytest.MonkeyPatch, original: _Scripted, contender: _Scripted
) -> list[str]:
    reaped: list[str] = []

    async def session(*_a: object) -> str:
        return "s"

    async def launch(_c: object, *, mode: str, **_k: object) -> _Scripted:
        return original if mode == "hold" else contender

    async def noop(*_a: object) -> None:
        return None

    async def reap(*peers: Any) -> None:
        reaped.append("reaped")

    monkeypatch.setattr(managed, "_session", session)
    monkeypatch.setattr(managed, "_launch", launch)
    monkeypatch.setattr(managed, "_verify_stale_owner", noop)
    monkeypatch.setattr(managed, "_verify_fencing_projection", noop)
    monkeypatch.setattr(managed, "_reap_peers", reap)
    return reaped


@pytest.mark.parametrize(
    ("original", "contender", "exception", "code"),
    [
        ([{"event": "x"}], [], RuntimeError, "managed_peer_hold_activation_failed"),
        (
            [{"event": "turn_held"}, {"event": "refused"}],
            [],
            RuntimeError,
            "managed_peer_initial_heartbeat_failed",
        ),
        (
            [{"event": "turn_held"}, {"event": "ok"}],
            [{"event": "unexpected_acquire"}],
            managed.ManagedPeerInconclusive,
            "managed_peer_initial_state_unobserved",
        ),
        (
            [{"event": "turn_held"}, {"event": "ok"}],
            [{"event": "refused"}],
            RuntimeError,
            "managed_peer_initial_state_invalid",
        ),
        (
            [{"event": "turn_held"}, {"event": "ok"}],
            [{"event": "in_progress"}, {"event": "refused"}],
            RuntimeError,
            "managed_peer_natural_takeover_failed",
        ),
        (
            [{"event": "turn_held"}, {"event": "ok"}],
            [
                {"event": "in_progress"},
                {"event": "taken_over"},
                {"event": "ok"},
                {"event": "refused"},
            ],
            RuntimeError,
            "managed_peer_current_owner_lost",
        ),
        (
            [{"event": "turn_held"}, {"event": "ok"}],
            [
                {"event": "in_progress"},
                {"event": "taken_over"},
                {"event": "ok"},
                {"event": "ok"},
                {"event": "refused"},
            ],
            RuntimeError,
            "managed_peer_current_finish_failed",
        ),
    ],
)
def test_natural_expiry_fencing_refusals_always_reap_both_peers(
    monkeypatch: pytest.MonkeyPatch,
    original: list[dict[str, object]],
    contender: list[dict[str, object]],
    exception: type[Exception],
    code: str,
) -> None:
    reaped = _install_fencing(monkeypatch, _Scripted(*original), _Scripted(*contender))
    with pytest.raises(exception, match=code):
        asyncio.run(managed.natural_expiry_fencing(cast(Any, SimpleNamespace(owner="o"))))
    assert reaped == ["reaped"]


def test_verify_fencing_projection_uses_public_store_page(monkeypatch: pytest.MonkeyPatch) -> None:
    valid = (
        _entry(0, "turn_ended", turn_id="old", stop_reason="interrupted"),
        _entry(1, "message", key="current-b"),
    )

    def context(items: tuple[Entry, ...]) -> Any:
        async def entries(session_id: str, owner: str, *, start: int, limit: int) -> Any:
            assert (session_id, owner, start, limit) == ("s", "owner-a", 0, 100)
            return SimpleNamespace(items=items)

        return SimpleNamespace(owner="owner-a", store=SimpleNamespace(entries=entries))

    asyncio.run(managed._verify_fencing_projection(context(valid), "s", "old"))
    with pytest.raises(RuntimeError, match="managed_peer_public_projection_invalid"):
        asyncio.run(managed._verify_fencing_projection(context(valid[:1]), "s", "old"))


def test_session_creates_uniquely_referenced_session() -> None:
    created: list[Any] = []

    async def create(owner: str, init: Any) -> Any:
        created.append((owner, init.ref))
        return SimpleNamespace(id="session-9")

    context = SimpleNamespace(owner="owner-a", store=SimpleNamespace(create=create))
    assert asyncio.run(managed._session(cast(Any, context), "fencing")) == "session-9"
    assert created[0][0] == "owner-a"
    assert created[0][1].startswith("managed-process-fencing-")


def _install_killed(monkeypatch: pytest.MonkeyPatch, peer: _Scripted, verified: list[str]) -> None:
    async def session(*_a: object) -> str:
        return "s"

    async def launch(*_a: object, **_k: object) -> _Scripted:
        return peer

    async def verify(*_a: object) -> None:
        verified.append("verified")

    monkeypatch.setattr(managed, "_session", session)
    monkeypatch.setattr(managed, "_launch", launch)
    monkeypatch.setattr(managed, "_verify_killed_recovery", verify)


def test_killed_worker_recovery_kills_peer_then_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    peer = _Scripted({"event": "intent_committed"})
    verified: list[str] = []
    _install_killed(monkeypatch, peer, verified)
    result = asyncio.run(managed.killed_worker_recovery(cast(Any, SimpleNamespace())))
    assert result["child_killed"] is True
    assert result["repeat_recovery_empty"] is True
    assert (peer.commands, peer.reaped, verified) == (["activate"], 1, ["verified"])


def test_killed_worker_recovery_refuses_bad_activation_and_still_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _Scripted({"event": "turn_held"})
    _install_killed(monkeypatch, peer, [])
    with pytest.raises(RuntimeError, match="managed_peer_unknown_activation_failed"):
        asyncio.run(managed.killed_worker_recovery(cast(Any, SimpleNamespace())))
    assert peer.reaped == 1


def test_killed_worker_recovery_refuses_peer_that_exited_before_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _Scripted({"event": "intent_committed"}, returncode=0)
    _install_killed(monkeypatch, peer, [])
    with pytest.raises(RuntimeError, match="managed_peer_exited_before_kill"):
        asyncio.run(managed.killed_worker_recovery(cast(Any, SimpleNamespace())))


def test_killed_worker_recovery_requires_observed_sigkill(monkeypatch: pytest.MonkeyPatch) -> None:
    peer = _Scripted({"event": "intent_committed"})

    async def reap_with_other_status() -> None:
        peer.process.returncode = 0

    monkeypatch.setattr(peer, "kill_and_reap", reap_with_other_status)
    _install_killed(monkeypatch, peer, [])
    with pytest.raises(RuntimeError, match="managed_peer_sigkill_not_observed"):
        asyncio.run(managed.killed_worker_recovery(cast(Any, SimpleNamespace())))


# ---------------------------------------------------------------- recovery verification


_INTENT = dict(
    key="intent:qualification-unknown",
    turn_id="turn-a",
    invocation_id="qualification-unknown",
    effect="effectful",
    status="started",
)


def _recovery_context(
    *,
    recovered: tuple[Any, ...] | None = None,
    summary: Any = None,
    effects: tuple[Any, ...] | None = None,
    entries: tuple[Entry, ...] | None = None,
    second_recovery: tuple[Any, ...] = (),
) -> Any:
    first = (RecoveredTurn(session_id="s", turn_id="turn-a", resumable=True),)
    batches = [first if recovered is None else recovered, second_recovery]
    view_summary = summary or SimpleNamespace(status="interrupted", unresolved_effect_count=1)
    effect = SimpleNamespace(reason="outcome_unknown", invocation_id="qualification-unknown")

    async def recover_owner(actor: Any) -> Any:
        return batches.pop(0)

    async def get(session_id: str, owner: str) -> Any:
        return None if summary == "missing" else SimpleNamespace(summary=view_summary)

    async def list_effects(session_id: str, owner: str, *, limit: int) -> Any:
        return SimpleNamespace(items=(effect,) if effects is None else effects)

    async def list_entries(session_id: str, owner: str, *, start: int, limit: int) -> Any:
        default = (_entry(0, "tool_call", **_INTENT),)
        return SimpleNamespace(items=default if entries is None else entries)

    return SimpleNamespace(
        owner="owner-a",
        actor=object(),
        resources=SimpleNamespace(recover_owner=recover_owner),
        store=SimpleNamespace(get=get, effects=list_effects, entries=list_entries),
    )


def test_verify_killed_recovery_accepts_exact_unknown_effect_state() -> None:
    asyncio.run(managed._verify_killed_recovery(_recovery_context(), "s", "turn-a"))


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        (
            {"recovered": (RecoveredTurn(session_id="x", turn_id="y", resumable=True),)},
            "managed_peer_recovery_missing",
        ),
        ({"summary": "missing"}, "managed_peer_recovery_projection_invalid"),
        (
            {"summary": SimpleNamespace(status="idle", unresolved_effect_count=1)},
            "managed_peer_recovery_projection_invalid",
        ),
        (
            {"summary": SimpleNamespace(status="interrupted", unresolved_effect_count=2)},
            "managed_peer_recovery_projection_invalid",
        ),
        ({"effects": ()}, "managed_peer_unknown_effect_missing"),
        (
            {"effects": (SimpleNamespace(reason="other", invocation_id="qualification-unknown"),)},
            "managed_peer_unknown_effect_missing",
        ),
        (
            {"effects": (SimpleNamespace(reason="outcome_unknown", invocation_id="nope"),)},
            "managed_peer_unknown_effect_missing",
        ),
        ({"entries": ()}, "managed_peer_inferred_effect_result"),
        (
            {"second_recovery": (RecoveredTurn(session_id="s", turn_id="turn-a", resumable=True),)},
            "managed_peer_recovery_not_idempotent",
        ),
    ],
)
def test_verify_killed_recovery_refusals(kwargs: dict[str, Any], code: str) -> None:
    with pytest.raises(RuntimeError, match=code):
        asyncio.run(managed._verify_killed_recovery(_recovery_context(**kwargs), "s", "turn-a"))


def test_recovery_poll_pause_yields_without_error(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(managed.asyncio, "sleep", fake_sleep)
    asyncio.run(managed._recovery_poll_pause())
    assert slept == [0.25]


# ---------------------------------------------------------------- top-level qualification


def _install_qualify(monkeypatch: pytest.MonkeyPatch, order: list[str]) -> None:
    class Context:
        async def __aenter__(self) -> str:
            order.append("enter")
            return "context"

        async def __aexit__(self, *_args: object) -> None:
            order.append("exit")

    async def database(context: object) -> dict[str, object]:
        order.append("database")
        return {"db": True}

    async def fencing(context: object) -> dict[str, object]:
        order.append("fencing")
        return {"f": True}

    async def recovery(context: object) -> dict[str, object]:
        order.append("recovery")
        return {"r": True}

    monkeypatch.setattr(managed, "_managed_context", lambda *_a: Context())
    monkeypatch.setattr(managed, "_database_observation", database)
    monkeypatch.setattr(managed, "natural_expiry_fencing", fencing)
    monkeypatch.setattr(managed, "killed_worker_recovery", recovery)


def test_qualify_runs_database_fencing_then_recovery_inside_one_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    _install_qualify(monkeypatch, order)
    result = asyncio.run(managed.qualify_managed_processes(cast(Any, object()), {}))
    assert result == {
        "database": {"db": True},
        "natural_expiry_fencing": {"f": True},
        "killed_worker_recovery": {"r": True},
    }
    assert order == ["enter", "database", "fencing", "recovery", "exit"]


def test_qualify_maps_watchdog_expiry_to_inconclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_qualify(monkeypatch, [])

    async def expired(seconds: float | None, operation: Any) -> Any:
        raise ObservationWatchdogExpired("x")

    monkeypatch.setattr(managed, "run_with_observation_watchdog", expired)
    with pytest.raises(managed.ManagedPeerInconclusive, match="managed_peer_observation_watchdog"):
        asyncio.run(
            managed.qualify_managed_processes(
                cast(Any, object()), {}, observation_timeout_seconds=1.0
            )
        )


def test_credential_wrapper_returns_fixed_authorization() -> None:
    assert asyncio.run(managed._Credential("Bearer t").authorization()) == "Bearer t"
