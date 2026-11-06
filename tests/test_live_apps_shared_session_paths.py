"""Shared-session verification and unknown-effect preparation/reopen steps."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import qualify_live_apps as live
from qualify_live_apps_config import LiveSettings

pytestmark = pytest.mark.anyio
_ANY: Any = object()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Approve:
    def __init__(self, disabled_for: int) -> None:
        self._remaining = disabled_for

    @property
    def disabled(self) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return True
        return False


class _Pilot:
    def __init__(self) -> None:
        self.pauses = 0

    async def pause(self) -> None:
        self.pauses += 1


def _install_app(monkeypatch: pytest.MonkeyPatch, *, selected: str, disabled_for: int) -> _Pilot:
    pilot = _Pilot()
    approve = _Approve(disabled_for)

    class FakeApp:
        def __init__(self, client: object, *, session_id: str) -> None:
            self.session_id = selected
            self.requested = session_id

        @asynccontextmanager
        async def run_test(self) -> AsyncIterator[_Pilot]:
            yield pilot

        def query_one(self, selector: str, _kind: object) -> _Approve:
            assert selector == "#approve"
            return approve

    monkeypatch.setattr(live, "ClientApp", FakeApp)
    return pilot


async def test_verify_tui_waits_until_approval_control_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pilot = _install_app(monkeypatch, selected="s1", disabled_for=3)
    await live._verify_tui(_ANY, "s1")
    assert pilot.pauses == 3


async def test_verify_tui_refuses_other_selected_session(monkeypatch: pytest.MonkeyPatch) -> None:
    pilot = _install_app(monkeypatch, selected="other", disabled_for=0)
    with pytest.raises(RuntimeError, match="live_tui_selected_session_mismatch"):
        await live._verify_tui(_ANY, "s1")
    assert pilot.pauses == 0


class _Client:
    def __init__(self, *, view_id: str = "s1", pending: Any = "inv-1") -> None:
        self._view_id = view_id
        self._pending = pending

    async def get(self, _session: str) -> Any:
        return SimpleNamespace(summary=SimpleNamespace(id=self._view_id))

    async def pending(self, _session: str) -> Any:
        if self._pending is None:
            return None
        return SimpleNamespace(invocation_id=self._pending)


@pytest.mark.parametrize(
    ("client", "reason"),
    [
        (_Client(view_id="other"), "live_remote_shared_session_unavailable"),
        (_Client(pending=None), "live_remote_shared_session_unavailable"),
        (_Client(pending="inv-other"), "live_remote_pending_identity_mismatch"),
    ],
)
async def test_shared_session_validation_refuses_before_tui(
    monkeypatch: pytest.MonkeyPatch, client: Any, reason: str
) -> None:
    verified: list[str] = []

    async def verify(_client: object, session_id: str) -> None:
        verified.append(session_id)

    monkeypatch.setattr(live, "_verify_tui", verify)
    with pytest.raises(RuntimeError, match=reason):
        await live._validate_shared_session(client, "s1", "inv-1")
    assert verified == []


async def test_shared_session_validation_verifies_tui_for_matching_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified: list[str] = []

    async def verify(_client: object, session_id: str) -> None:
        verified.append(session_id)

    monkeypatch.setattr(live, "_verify_tui", verify)
    fake: Any = _Client()
    await live._validate_shared_session(fake, "s1", "inv-1")
    assert verified == ["s1"]


class _Monitor:
    def __init__(self) -> None:
        self.started = False

    async def wait_worker_started(self) -> None:
        self.started = True


def _install_prepare(
    monkeypatch: pytest.MonkeyPatch, pending: dict[str, Any], phase: list[str]
) -> Any:
    trail: list[str] = []
    monitor = _Monitor()

    async def read(_root: Path, name: str, *, process: object) -> dict[str, Any]:
        trail.append(f"read:{name}:{phase[0]}")
        return dict(pending) if name == "unknown-pending.json" else {"turn_id": "turn-1"}

    async def start(*args: Any) -> _Monitor:
        trail.append(f"monitor:{args[-1]}")
        return monitor

    async def approve(_root: Path) -> None:
        trail.append(f"approve:{phase[0]}")

    async def started(_client: object, item: dict[str, Any]) -> None:
        trail.append(f"started:{item['turn_id']}")

    monkeypatch.setattr(live, "_read_rendezvous", read)
    monkeypatch.setattr(live, "start_log_monitor", start)
    monkeypatch.setattr(
        live, "qualification_app", lambda source: source["TETHER_QUALIFICATION_APP"]
    )
    monkeypatch.setattr(live, "_approve_unknown_from_browser", approve)
    monkeypatch.setattr(live, "_validate_started_effect", started)
    return trail, monitor


async def test_prepare_unknown_effect_sequences_monitor_approval_and_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    phase = ["pending_rendezvous"]
    trail, monitor = _install_prepare(monkeypatch, {"invocation_id": "inv-1"}, phase)
    pending, returned = await live._prepare_unknown_effect(
        _ANY,
        "admin",
        None,
        {"TETHER_QUALIFICATION_APP": "fabricated-app"},
        _ANY,
        tmp_path,
        phase,
    )
    assert returned is monitor
    assert monitor.started
    assert pending == {"invocation_id": "inv-1", "turn_id": "turn-1"}
    assert phase == ["observe_worker_start"]
    assert trail == [
        "read:unknown-pending.json:pending_rendezvous",
        "monitor:inv-1",
        "approve:approve_effect",
        "read:unknown-accepted.json:accepted_rendezvous",
        "started:turn-1",
    ]


async def test_prepare_unknown_effect_refuses_untyped_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    phase = ["pending_rendezvous"]
    trail, _ = _install_prepare(monkeypatch, {"invocation_id": 7}, phase)
    with pytest.raises(RuntimeError, match="live_unknown_effect_rendezvous_invalid"):
        await live._prepare_unknown_effect(
            _ANY,
            "admin",
            None,
            {"TETHER_QUALIFICATION_APP": "fabricated-app"},
            _ANY,
            tmp_path,
            phase,
        )
    assert trail == ["read:unknown-pending.json:pending_rendezvous"]


async def test_restart_unknown_effect_records_phase_and_returns_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def restart(*args: Any) -> dict[str, Any]:
        return {"args": args[:3]}

    monkeypatch.setattr(live, "restart_for_unknown_effect", restart)
    phase = ["x"]
    monitor: Any = object()
    report = await live._restart_unknown_effect("admin", "config", {"A": "b"}, monitor, phase)
    assert report == {"args": ("admin", "config", {"A": "b"})}
    assert phase == ["restart_app"]


class _Closer:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_reopen_after_unknown_effect_replaces_clients_and_releases_browser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    trail: list[str] = []
    fresh_client, fresh_http = _Closer(), _Closer()

    async def reopen(*_args: Any, **kwargs: Any) -> tuple[_Closer, _Closer]:
        trail.append(f"reopen:{kwargs['service_caller']}")
        return fresh_client, fresh_http

    async def barrier(client: object, item: dict[str, Any]) -> None:
        trail.append(f"barrier:{client is fresh_client}:{item['turn_id']}")

    async def gate(path: Path) -> None:
        trail.append(f"gate:{path.name}")

    async def finish(_process: object, _root: Path) -> None:
        trail.append("finish")

    monkeypatch.setattr(live, "_client_for_restart_readiness", reopen)
    monkeypatch.setattr(live, "_validate_recovered_barrier", barrier)
    monkeypatch.setattr(live, "_write_gate", gate)
    monkeypatch.setattr(live, "_validate_unknown_browser_finish", finish)
    old_client: Any = _Closer()
    old_http: Any = _Closer()
    phase = ["restart_app"]
    settings = LiveSettings("fabricated-app", "https://apps.example", "/prefix", "/api/v1")
    client, http = await live._reopen_after_unknown_effect(
        old_client,
        old_http,
        settings,
        _ANY,
        True,
        {"turn_id": "turn-1"},
        _ANY,
        tmp_path,
        phase,
    )
    assert (client, http) == (fresh_client, fresh_http)
    assert old_client.closed
    assert old_http.closed
    assert phase == ["browser_recovery"]
    assert trail == ["reopen:True", "barrier:True:turn-1", "gate:restart-complete", "finish"]
