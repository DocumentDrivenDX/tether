"""Isolated orchestration and ownership tests for the Apps driver."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from qualify_live_apps import (
    _qualify_unknown_effect,
    _race_approval,
    _run,
    _run_case,
    _validate_browser_approval,
    _validate_recovered_barrier,
    _validate_started_effect,
    _validate_unknown_browser_finish,
    _wait_result,
    _write_gate,
    main,
)
from qualify_live_apps_browser import LiveBrowserFailure
from qualify_live_apps_config import LiveSettings
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_live_apps_observation import LiveObservationPolicy, use_live_observation_policy
from qualify_live_apps_transport import LiveBrowserChild

from tether.client import ExecutionDenied
from tether.client.remote import RemoteProblem


def _fabricated_authorization() -> str:
    return "".join(("Be", "arer ", "fabricated", "-marker"))


def _settings() -> LiveSettings:
    return LiveSettings("fabricated-app", "https://apps.example", "/prefix", "/api/v1")


class _Process:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode
        self.stdout = _EofReader()
        self.stderr = _EofReader()

    async def wait(self) -> int | None:
        return self.returncode


class _EofReader:
    async def read(self, _size: int) -> bytes:
        return b""


class _Closing:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _BrowserProcess(_Process):
    def __init__(self, *, returncode: int = 0) -> None:
        super().__init__(returncode)
        self.waited = False

    async def wait(self) -> int | None:
        self.waited = True
        return await super().wait()


def _tool_result() -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason="final",
        messages=[
            SimpleNamespace(
                role="tool", content="Fabricated Tether qualification effect completed."
            )
        ],
    )


class _SpawnControlProcess:
    pid = 7319

    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode: int | None = None
        self.reaped = asyncio.Event()

    async def wait(self) -> int:
        await self.reaped.wait()
        return self.returncode or 0


class _BrowserSpawnControl:
    def __init__(self) -> None:
        self.process: _SpawnControlProcess | None = None
        self.spawn_entered = asyncio.Event()
        self.spawn_release = asyncio.Event()
        self.kill_sent = asyncio.Event()
        self.signals: list[tuple[int, int]] = []

    async def create_process(self, *_args: object, **_kwargs: object) -> _SpawnControlProcess:
        self.spawn_entered.set()
        await self.spawn_release.wait()
        assert self.process is not None
        return self.process

    def kill_group(self, pgid: int, selected_signal: int) -> None:
        assert self.process is not None
        self.signals.append((pgid, selected_signal))
        if selected_signal == signal.SIGTERM:
            self.process.returncode = -15
            self.process.reaped.set()
        elif selected_signal == signal.SIGKILL:
            self.kill_sent.set()


def test_race_approval_handles_remote_winner_and_browser_winner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    gates: list[Path] = []

    async def write_gate(path: Path) -> None:
        gates.append(path)

    async def read(_path: Path, name: str, *, process: object) -> dict[str, object]:
        del process
        return {"turn_id": "browser-turn"} if name == "browser-accepted.json" else {}

    async def wait_result(_client: object, session: str, turn: str) -> SimpleNamespace:
        assert (session, turn) == ("session", "browser-turn")
        return _tool_result()

    monkeypatch.setattr(live, "_write_gate", write_gate)
    monkeypatch.setattr(live, "_read_rendezvous", read)
    monkeypatch.setattr(live, "_wait_result", wait_result)

    class Winner:
        async def result(self) -> SimpleNamespace:
            return _tool_result()

    class Client:
        def __init__(self, winner: object | None) -> None:
            self.winner = winner

        async def confirm(self, *_args: object, **_kwargs: object) -> object | None:
            return self.winner

    remote = asyncio.run(
        _race_approval(
            cast(Any, Client(Winner())), "session", "invocation", tmp_path, cast(Any, object())
        )
    )
    browser = asyncio.run(
        _race_approval(
            cast(Any, Client(None)), "session", "invocation", tmp_path, cast(Any, object())
        )
    )
    assert remote is True
    assert browser is False
    assert gates == [
        tmp_path / "race-approval",
        tmp_path / "release-browser-approval",
        tmp_path / "race-approval",
        tmp_path / "release-browser-approval",
    ]


def test_race_approval_refuses_empty_browser_turn_identifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    async def read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"turn_id": ""}

    monkeypatch.setattr(live, "_write_gate", no_op)
    monkeypatch.setattr(live, "_read_rendezvous", read)

    class Client:
        async def confirm(self, *_args: object, **_kwargs: object) -> None:
            return None

    async def run() -> None:
        with pytest.raises(RuntimeError, match="live_browser_turn_id_missing"):
            await _race_approval(cast(Any, Client()), "s", "i", tmp_path, cast(Any, object()))

    asyncio.run(run())


def test_race_approval_refuses_nonfinal_or_duplicate_tool_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    async def no_op(*_args: object, **_kwargs: object) -> None:
        return None

    async def read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(live, "_write_gate", no_op)
    monkeypatch.setattr(live, "_read_rendezvous", read)

    class Handle:
        def __init__(self, result: object) -> None:
            self.value = result

        async def result(self) -> object:
            return self.value

    class Client:
        def __init__(self, result: object) -> None:
            self.value = result

        async def confirm(self, *_args: object, **_kwargs: object) -> Handle:
            return Handle(self.value)

    invalid = [
        (
            SimpleNamespace(stop_reason="cancelled", messages=[]),
            "live_competing_approval_not_final",
        ),
        (
            SimpleNamespace(stop_reason="final", messages=[]),
            "live_competing_approval_effect_count_invalid",
        ),
        (
            SimpleNamespace(
                stop_reason="final",
                messages=[SimpleNamespace(role="tool", content="unexpected")],
            ),
            "live_competing_approval_result_invalid",
        ),
    ]

    async def run(result: object, expected: str) -> None:
        with pytest.raises(RuntimeError, match=expected):
            await _race_approval(
                cast(Any, Client(result)), "s", "i", tmp_path, cast(Any, _BrowserProcess())
            )

    for result, expected in invalid:
        asyncio.run(run(result, expected))


def test_validate_started_effect_checks_durable_intent_and_pending_result() -> None:
    class Client:
        async def effects(self, session_id: str) -> SimpleNamespace:
            assert session_id == "s"
            return SimpleNamespace(items=[SimpleNamespace(invocation_id="i")])

        async def turn_result(self, session_id: str, turn_id: str) -> None:
            assert (session_id, turn_id) == ("s", "t")

    asyncio.run(
        _validate_started_effect(
            cast(Any, Client()), {"session_id": "s", "invocation_id": "i", "turn_id": "t"}
        )
    )


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"session_id": "s", "invocation_id": "i"}, "live_unknown_effect_rendezvous_invalid"),
        (
            {"session_id": "s", "invocation_id": "i", "turn_id": "t"},
            "live_unknown_effect_completed_before_restart",
        ),
    ],
)
def test_validate_started_effect_rejects_invalid_or_already_terminal_turn(
    item: dict[str, str], expected: str
) -> None:
    class Client:
        async def effects(self, _session_id: str) -> SimpleNamespace:
            return SimpleNamespace(items=[SimpleNamespace(invocation_id="i")])

        async def turn_result(self, _session_id: str, _turn_id: str) -> object:
            return object()

    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(_validate_started_effect(cast(Any, Client()), item))


def test_validate_started_effect_times_out_when_intent_never_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class Client:
        async def effects(self, _session_id: str) -> SimpleNamespace:
            return SimpleNamespace(items=[])

    async def run() -> None:
        with (
            use_live_observation_policy(LiveObservationPolicy(0)),
            pytest.raises(RuntimeError, match="live_unknown_effect_intent_not_durable"),
        ):
            await _validate_started_effect(
                cast(Any, Client()),
                {"session_id": "s", "invocation_id": "i", "turn_id": "t"},
            )

    asyncio.run(run())


@pytest.mark.parametrize("stop_reason", ["cancelled", "interrupted"])
def test_validate_recovered_barrier_accepts_unknown_effect_and_denies_close(
    stop_reason: str,
) -> None:
    class Client:
        async def get(self, _session_id: str) -> SimpleNamespace:
            return SimpleNamespace(summary=SimpleNamespace(id="s"))

        async def turn_result(self, _session_id: str, _turn_id: str) -> SimpleNamespace:
            return SimpleNamespace(stop_reason=stop_reason)

        async def effects(self, _session_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                items=[SimpleNamespace(invocation_id="i", reason="outcome_unknown")]
            )

        async def close(self, _session_id: str) -> None:
            raise ExecutionDenied("denied")

    asyncio.run(
        _validate_recovered_barrier(
            cast(Any, Client()), {"session_id": "s", "invocation_id": "i", "turn_id": "t"}
        )
    )


@pytest.mark.parametrize(
    ("stop_reason", "reason", "expected"),
    [
        (None, "outcome_unknown", "live_unknown_effect_recovery_not_visible"),
        ("final", "outcome_unknown", "live_unknown_effect_recovery_not_visible"),
        ("confirmation_required", "outcome_unknown", "live_unknown_effect_recovery_not_visible"),
        ("error", "outcome_unknown", "live_unknown_effect_recovery_not_visible"),
        ("interrupted", "completed", "live_unknown_effect_recovery_not_visible"),
        ("cancelled", "not_completed", "live_unknown_effect_recovery_not_visible"),
        ("interrupted", "outcome_unknown", "live_unknown_effect_barrier_not_enforced"),
    ],
)
def test_validate_recovered_barrier_rejects_false_recovery_or_allowed_close(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str | None, reason: str, expected: str
) -> None:

    class Client:
        turn_reads = 0
        effect_reads = 0
        close_calls = 0

        async def get(self, _session_id: str) -> SimpleNamespace:
            return SimpleNamespace(summary=SimpleNamespace(id="s"))

        async def turn_result(self, _session_id: str, _turn_id: str) -> SimpleNamespace | None:
            self.turn_reads += 1
            if stop_reason is None:
                return None
            return SimpleNamespace(stop_reason=stop_reason)

        async def effects(self, _session_id: str) -> SimpleNamespace:
            self.effect_reads += 1
            return SimpleNamespace(items=[SimpleNamespace(invocation_id="i", reason=reason)])

        async def close(self, _session_id: str) -> None:
            self.close_calls += 1

    async def run() -> None:
        client = Client()
        policy = (
            LiveObservationPolicy(0)
            if expected != "live_unknown_effect_barrier_not_enforced"
            else LiveObservationPolicy()
        )
        with use_live_observation_policy(policy), pytest.raises(RuntimeError, match=expected):
            await _validate_recovered_barrier(
                cast(Any, client),
                {"session_id": "s", "invocation_id": "i", "turn_id": "t"},
            )
        assert client.turn_reads == 1
        assert client.effect_reads == 1
        assert client.close_calls == (
            1 if expected == "live_unknown_effect_barrier_not_enforced" else 0
        )

    asyncio.run(run())


@pytest.mark.parametrize(
    "items",
    [
        [],
        [SimpleNamespace(invocation_id="other", reason="outcome_unknown")],
        [
            SimpleNamespace(invocation_id="i", reason="outcome_unknown"),
            SimpleNamespace(invocation_id="i", reason="outcome_unknown"),
        ],
    ],
    ids=["missing-effect", "wrong-invocation", "duplicate-effect"],
)
def test_validate_recovered_barrier_requires_exactly_one_matching_unknown_effect(
    monkeypatch: pytest.MonkeyPatch, items: list[SimpleNamespace]
) -> None:

    class Client:
        turn_reads = 0
        effect_reads = 0

        async def get(self, _session_id: str) -> SimpleNamespace:
            return SimpleNamespace(summary=SimpleNamespace(id="s"))

        async def turn_result(self, _session_id: str, _turn_id: str) -> SimpleNamespace:
            self.turn_reads += 1
            return SimpleNamespace(stop_reason="cancelled")

        async def effects(self, _session_id: str) -> SimpleNamespace:
            self.effect_reads += 1
            return SimpleNamespace(items=items)

        async def close(self, _session_id: str) -> None:
            raise AssertionError("close must not be attempted without the matching barrier")

    async def run() -> None:
        client = Client()
        with (
            use_live_observation_policy(LiveObservationPolicy(0)),
            pytest.raises(RuntimeError, match="live_unknown_effect_recovery_not_visible"),
        ):
            await _validate_recovered_barrier(
                cast(Any, client),
                {"session_id": "s", "invocation_id": "i", "turn_id": "t"},
            )
        assert client.turn_reads == 1
        assert client.effect_reads == 1

    asyncio.run(run())


@pytest.mark.parametrize("stop_reason", ["cancelled", "interrupted"])
def test_browser_validation_and_gates_preserve_graceful_or_crash_terminal(
    tmp_path: Path, stop_reason: str
) -> None:
    process = _BrowserProcess()

    async def read(_path: Path, name: str, *, process: object) -> dict[str, object]:
        del process
        return (
            {"approval_status": 202}
            if name == "browser-finished.json"
            else {"stop_reason": stop_reason}
        )

    import qualify_live_apps as live

    original_read = live._read_rendezvous
    live._read_rendezvous = read
    try:
        assert asyncio.run(_validate_browser_approval(tmp_path, cast(Any, process))) == 202
        asyncio.run(_validate_unknown_browser_finish(cast(Any, process), tmp_path))
    finally:
        live._read_rendezvous = original_read
    assert process.waited
    gate = tmp_path / "approval-gate"
    asyncio.run(_write_gate(gate))
    assert gate.read_text() == "go\n"
    assert gate.stat().st_mode & 0o777 == 0o600


def test_browser_finish_rejects_unexpected_recovery_or_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    async def read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"stop_reason": "final"}

    monkeypatch.setattr(live, "_read_rendezvous", read)

    async def run() -> None:
        with pytest.raises(RuntimeError, match="live_browser_recovery_terminal_status_invalid"):
            await _validate_unknown_browser_finish(cast(Any, _BrowserProcess()), tmp_path)

    asyncio.run(run())


def test_browser_finish_rejects_nonzero_child_exit_after_valid_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    async def read(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"stop_reason": "cancelled"}

    monkeypatch.setattr(live, "_read_rendezvous", read)

    async def run() -> None:
        process = _BrowserProcess(returncode=1)
        with pytest.raises(RuntimeError, match="live_browser_case_failed"):
            await _validate_unknown_browser_finish(cast(Any, process), tmp_path)
        assert process.waited

    asyncio.run(run())


def test_wait_result_returns_terminal_result_after_pending_poll() -> None:
    class Client:
        count = 0

        async def turn_result(self, _session_id: str, _turn_id: str) -> object | None:
            self.count += 1
            return None if self.count == 1 else "terminal"

    client = Client()
    assert asyncio.run(_wait_result(client, "s", "t")) == "terminal"
    assert client.count == 2


def test_run_uses_explicit_settings_and_selected_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live

    output = tmp_path / "new-receipt.json"
    seen: list[object] = []
    monkeypatch.setattr(live, "validate_receipt_output", lambda path: seen.append(path))

    async def run_case(*args: object) -> None:
        seen.extend(args)

    monkeypatch.setattr(live, "_run_case", run_case)
    source = {
        "TETHER_QUALIFICATION_APP": "fabricated-app",
        "TETHER_QUALIFICATION_APP_URL": "https://apps.example",
    }
    asyncio.run(_run("caller", "admin", None, source, output, False))
    assert seen[0] == output
    assert seen[1:5] == ["caller", "admin", None, source]
    assert seen[5] == LiveSettings("fabricated-app", "https://apps.example", "", "/api/v1")


def test_run_case_success_writes_receipt_and_closes_owned_handles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live
    import qualify_live_apps_cleanup as cleanup

    client, http, monitor = _Closing(), _Closing(), _Closing()
    browser = _Process(returncode=0)
    wrote: list[tuple[object, ...]] = []
    stopped: list[object] = []

    async def profile(_name: str) -> tuple[object, str]:
        return object(), _fabricated_authorization()

    async def open_client(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return client, http

    async def launch(*_args: object) -> LiveBrowserChild:
        return LiveBrowserChild(cast(asyncio.subprocess.Process, browser), ())

    async def shared(*_args: object) -> None:
        return None

    async def unknown(*_args: object) -> tuple[object, object, object, dict[str, object]]:
        return client, http, monitor, {"complete": False}

    async def receipt(*args: object, **_kwargs: object) -> None:
        wrote.append(args)

    async def stop(process: object, _output_tasks: object = ()) -> None:
        stopped.append(process)

    monkeypatch.setattr(live, "inventory", lambda _root: {"current": "digest"})
    monkeypatch.setattr(live, "_profile_credential", profile)
    monkeypatch.setattr(live, "_client_for", open_client)
    monkeypatch.setattr(live, "_launch_browser", launch)
    monkeypatch.setattr(cleanup, "_stop_browser_group", stop)
    monkeypatch.setattr(live, "_qualify_shared_session", shared)
    monkeypatch.setattr(live, "_qualify_unknown_effect", unknown)
    monkeypatch.setattr(live, "write_live_receipt", receipt)

    asyncio.run(
        _run_case("caller", "admin", None, {}, _settings(), tmp_path / "receipt.json", False)
    )
    assert len(wrote) == 1
    assert client.closed
    assert http.closed
    assert monitor.closed
    assert stopped == [browser]


def test_run_case_stops_nonzero_browser_group_after_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live
    import qualify_live_apps_cleanup as cleanup

    class Client:
        async def aclose(self) -> None:
            return None

    client, http = Client(), Client()
    browser = _Process(returncode=1)
    stopped: list[object] = []

    async def profile(_name: str) -> tuple[object, str]:
        return object(), _fabricated_authorization()

    async def open_client(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return client, http

    async def launch(*_args: object) -> LiveBrowserChild:
        return LiveBrowserChild(cast(asyncio.subprocess.Process, browser), ())

    async def shared(*_args: object) -> None:
        raise RuntimeError("browser scenario failed")

    async def stop(process: object, _output_tasks: object = ()) -> None:
        stopped.append(process)

    monkeypatch.setattr(live, "inventory", lambda _root: {"current": "digest"})
    monkeypatch.setattr(live, "_profile_credential", profile)
    monkeypatch.setattr(live, "_client_for", open_client)
    monkeypatch.setattr(live, "_launch_browser", launch)
    monkeypatch.setattr(live, "_qualify_shared_session", shared)
    monkeypatch.setattr(cleanup, "_stop_browser_group", stop)

    async def run() -> None:
        with pytest.raises(LiveBrowserFailure) as caught:
            await _run_case(
                "caller", "admin", None, {}, _settings(), tmp_path / "receipt.json", False
            )
        assert caught.value.summary["exit_code"] == 1
        assert caught.value.summary["error_category"] == "report_missing"

    asyncio.run(run())
    assert stopped == [browser]


def test_run_case_preserves_incomplete_after_browser_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps as live
    import qualify_live_apps_cleanup as cleanup

    client, http = _Closing(), _Closing()
    browser = _Process(returncode=0)
    stopped: list[object] = []

    async def profile(_name: str) -> tuple[object, str]:
        return object(), _fabricated_authorization()

    async def open_client(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return client, http

    async def launch(*_args: object) -> LiveBrowserChild:
        return LiveBrowserChild(cast(asyncio.subprocess.Process, browser), ())

    async def shared(*_args: object) -> None:
        raise LiveQualificationIncomplete("live_browser_rendezvous_not_observed")

    async def stop(process: object, _output_tasks: object = ()) -> None:
        stopped.append(process)

    monkeypatch.setattr(live, "inventory", lambda _root: {"current": "digest"})
    monkeypatch.setattr(live, "_profile_credential", profile)
    monkeypatch.setattr(live, "_client_for", open_client)
    monkeypatch.setattr(live, "_launch_browser", launch)
    monkeypatch.setattr(live, "_qualify_shared_session", shared)
    monkeypatch.setattr(cleanup, "_stop_browser_group", stop)

    async def run() -> None:
        with pytest.raises(LiveQualificationIncomplete) as caught:
            await _run_case(
                "caller", "admin", None, {}, _settings(), tmp_path / "receipt.json", False
            )
        assert caught.value.reason == "live_browser_rendezvous_not_observed"

    asyncio.run(run())
    assert client.closed
    assert http.closed
    assert stopped == [browser]


def test_browser_spawn_cancellation_retains_group_and_stream_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import qualify_live_apps_transport as transport

    control = _BrowserSpawnControl()
    monkeypatch.setattr(transport.shutil, "which", lambda _name: "/fake/npm")
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", control.create_process)
    monkeypatch.setattr(transport.os, "killpg", control.kill_group)

    async def run() -> None:
        control.process = _SpawnControlProcess()
        launch = asyncio.create_task(transport._launch_browser(tmp_path, {}))
        await control.spawn_entered.wait()
        launch.cancel()
        await asyncio.sleep(0)
        launch.cancel()
        control.spawn_release.set()
        await control.kill_sent.wait()
        launch.cancel()
        assert not launch.done()
        assert control.process is not None
        control.process.stdout.feed_eof()
        control.process.stderr.feed_eof()
        with pytest.raises(asyncio.CancelledError):
            await launch

    asyncio.run(run())
    assert control.signals == [
        (7319, signal.SIGTERM),
        (7319, signal.SIGKILL),
    ]
    assert control.process is not None
    assert control.process.returncode == -15


def test_main_handles_typed_remote_problem_without_sensitive_detail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    import qualify_live_apps as live

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "qualify_live_apps",
            "--profile",
            "p",
            "--admin-profile",
            "a",
            "--output",
            str(tmp_path / "out"),
        ],
    )

    async def rejected(*_args: object, **_kwargs: object) -> None:
        raise RemoteProblem(403, "permission_denied", locator={"id": "private"})

    monkeypatch.setattr(live, "_run", rejected)
    assert main() == 1
    assert capsys.readouterr().err.strip() == "Live Apps check failed: HTTP 403 permission_denied"


def test_qualify_unknown_effect_preserves_typed_problem_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps as live

    client, http, monitor = _Closing(), _Closing(), _Closing()

    async def prepare(*_args: object) -> tuple[dict[str, Any], object]:
        return {"session_id": "s", "invocation_id": "i"}, monitor

    async def restart(*_args: object) -> dict[str, Any]:
        return {}

    async def reopen(*_args: object) -> tuple[object, object]:
        raise RemoteProblem(403, "permission_denied")

    monkeypatch.setattr(live, "_prepare_unknown_effect", prepare)
    monkeypatch.setattr(live, "_restart_unknown_effect", restart)
    monkeypatch.setattr(live, "_reopen_after_unknown_effect", reopen)

    async def run() -> None:
        with pytest.raises(RemoteProblem, match="permission_denied"):
            await _qualify_unknown_effect(
                cast(Any, client),
                cast(Any, http),
                cast(Any, object()),
                "admin",
                None,
                {},
                _settings(),
                False,
                cast(Any, object()),
                cast(Any, object()),
            )

    asyncio.run(run())
    assert client.closed
    assert http.closed
    assert monitor.closed
