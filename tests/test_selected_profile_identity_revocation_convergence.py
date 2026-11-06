"""Controls for account and workspace revocation convergence under one deadline."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_identity as identity

HOST = "https://workspace.fabricated.example"
PRINCIPAL = "fabricated-owned-principal"
APPLICATION = "fabricated-owned-application"
TOKEN = " ".join(("Bearer", "fabricated-captured-token"))
_REAL_SLEEP = asyncio.sleep


class _NoTimeout:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _ControlledClock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, duration: float) -> None:
        self.now += duration
        await _REAL_SLEEP(0)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(identity._identity_probe.time, "monotonic", self.monotonic)
        monkeypatch.setattr(identity._identity_probe.asyncio, "sleep", self.sleep)
        monkeypatch.setattr(
            identity._identity_probe.asyncio, "timeout", lambda *_a, **_k: _NoTimeout()
        )
        monkeypatch.setattr(
            identity._identity_probe.asyncio, "timeout_at", lambda *_a, **_k: _NoTimeout()
        )

        async def inline_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
            return function(*args, **kwargs)

        monkeypatch.setattr(identity._identity_probe.asyncio, "to_thread", inline_to_thread)


async def _await_event_or_task(event: asyncio.Event, task: asyncio.Task[Any]) -> None:
    waiter = asyncio.create_task(event.wait())
    done, _pending = await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        task.result()
        raise AssertionError("identity_operation_completed_before_control_barrier")


def _workspace() -> Any:
    return SimpleNamespace(config=SimpleNamespace(host=HOST))


def _install_workspace_wait(monkeypatch: pytest.MonkeyPatch, wait: Any) -> None:
    monkeypatch.setattr(identity, "wait_for_workspace_identity", wait)


async def _inactive_workspace(*_args: Any, **_kwargs: Any) -> str:
    return "inactive"


def test_workspace_and_account_converge_before_protected_denial(monkeypatch) -> None:
    clock = _ControlledClock()
    clock.install(monkeypatch)
    monkeypatch.setattr(identity._identity_probe, "_POLL_INTERVAL_SECONDS", 2.0)
    events: list[str] = []
    deadlines: list[float] = []
    account_reads = 0
    account_inactive = asyncio.Event()

    class Admin:
        def get_service_principal(self, principal_id: str) -> Any:
            nonlocal account_reads
            assert principal_id == PRINCIPAL
            account_reads += 1
            events.append("account-inactive" if account_reads == 2 else "account-active")
            if account_reads == 2:
                account_inactive.set()
            return SimpleNamespace(
                id=PRINCIPAL,
                application_id=APPLICATION,
                active=account_reads != 2,
            )

    async def workspace_wait(
        host: str,
        token: str,
        principal: str,
        *,
        expected_active: bool,
        deadline: float | None = None,
    ) -> str:
        assert (host, token, principal, expected_active) == (HOST, TOKEN, PRINCIPAL, False)
        assert deadline is not None
        deadlines.append(deadline)
        events.append("workspace-wait")
        if len(deadlines) == 1:
            await account_inactive.wait()
        else:
            events.append("workspace-recheck")
        return "inactive"

    async def protected(*_args: Any, **kwargs: Any) -> int:
        assert kwargs["timeout_seconds"] > 0
        events.append("protected-denial")
        return 403

    monkeypatch.setattr(identity, "_principal_status", protected)
    _install_workspace_wait(monkeypatch, workspace_wait)
    result = asyncio.run(
        identity._verify_revocation_denial(
            cast(Any, Admin()),
            _workspace(),
            PRINCIPAL,
            APPLICATION,
            TOKEN,
            int(time.time()) + 3600,
            "https://app.fabricated.example",
            "/api/v1",
            deadline=clock.now + 10,
        )
    )

    assert result is True
    assert account_reads == 2
    assert deadlines[0] == deadlines[1]
    assert events[-2:] == ["workspace-recheck", "protected-denial"]
    assert events.index("account-inactive") < events.index("protected-denial")


def test_stale_account_state_uses_original_shared_deadline(monkeypatch) -> None:
    clock = _ControlledClock()
    clock.install(monkeypatch)
    monkeypatch.setattr(identity._identity_probe, "_POLL_INTERVAL_SECONDS", 0.25)
    account_reads = 0
    protected_calls = 0

    class Admin:
        def get_service_principal(self, principal_id: str) -> Any:
            nonlocal account_reads
            account_reads += 1
            return SimpleNamespace(id=principal_id, application_id=APPLICATION, active=True)

    async def workspace_wait(*_args: Any, **_kwargs: Any) -> str:
        return "inactive"

    async def protected(*_args: Any, **_kwargs: Any) -> int:
        nonlocal protected_calls
        protected_calls += 1
        return 403

    monkeypatch.setattr(identity, "_principal_status", protected)
    _install_workspace_wait(monkeypatch, workspace_wait)
    with pytest.raises(RuntimeError, match="identity_workspace_transition_timeout"):
        asyncio.run(
            identity._verify_revocation_denial(
                cast(Any, Admin()),
                _workspace(),
                PRINCIPAL,
                APPLICATION,
                TOKEN,
                int(time.time()) + 3600,
                "https://app.fabricated.example",
                "/api/v1",
                deadline=clock.now + 2,
            )
        )
    assert clock.now == 102
    assert account_reads >= 2
    assert protected_calls == 0


def test_shared_deadline_failure_still_restores_owned_identity(monkeypatch) -> None:
    clock = _ControlledClock()
    clock.install(monkeypatch)
    monkeypatch.setattr(identity._identity_probe, "_DEADLINE_SECONDS", 4)
    monkeypatch.setattr(
        "selected_profiles_expiry._token_expiry", lambda _token: int(time.time()) + 3600
    )

    class Admin:
        def __init__(self) -> None:
            self.active = True

        def operator(self) -> Any:
            return SimpleNamespace(id="fabricated-admin")

        def get_service_principal(self, principal_id: str) -> Any:
            return SimpleNamespace(id=principal_id, application_id=APPLICATION, active=True)

        def set_service_principal_active(self, principal_id: str, active: bool) -> None:
            assert principal_id == PRINCIPAL
            self.active = active

    admin = Admin()

    async def workspace_wait(*_args: Any, **_kwargs: Any) -> str:
        clock.now = 104
        return "inactive"

    async def fresh_access(*_args: Any) -> Any:
        return SimpleNamespace(host=HOST, authorization=TOKEN)

    async def refresh() -> Any:
        return SimpleNamespace(host=HOST, authorization=TOKEN)

    monkeypatch.setattr(identity, "wait_for_workspace_identity", workspace_wait)
    monkeypatch.setattr(identity, "fresh_workspace_access", fresh_access)

    with pytest.raises(RuntimeError, match="identity_workspace_transition_timeout"):
        asyncio.run(
            identity._deactivate_check_and_restore(
                admin,
                _workspace(),
                PRINCIPAL,
                APPLICATION,
                TOKEN,
                "https://app.fabricated.example",
                "/api/v1",
                [],
                refresh,
            )
        )
    assert admin.active is True


@pytest.mark.parametrize(
    "principal",
    [
        SimpleNamespace(id="fabricated-other-principal", application_id=APPLICATION, active=False),
        SimpleNamespace(id=PRINCIPAL, application_id="fabricated-other-application", active=False),
        SimpleNamespace(id=PRINCIPAL, application_id=APPLICATION, active=0),
        SimpleNamespace(id=PRINCIPAL, application_id=APPLICATION, active=None),
    ],
)
def test_unverified_account_state_never_reaches_protected_route(monkeypatch, principal) -> None:
    protected_calls = 0

    class Admin:
        def get_service_principal(self, _principal_id: str) -> Any:
            return principal

    async def workspace_wait(*_args: Any, **_kwargs: Any) -> str:
        return "inactive"

    async def protected(*_args: Any, **_kwargs: Any) -> int:
        nonlocal protected_calls
        protected_calls += 1
        return 403

    monkeypatch.setattr(identity, "_principal_status", protected)
    _install_workspace_wait(monkeypatch, workspace_wait)
    with pytest.raises(RuntimeError, match="test_identity_account_state_unverified"):
        asyncio.run(
            identity._verify_revocation_denial(
                cast(Any, Admin()),
                _workspace(),
                PRINCIPAL,
                APPLICATION,
                TOKEN,
                int(time.time()) + 3600,
                "https://app.fabricated.example",
                "/api/v1",
                deadline=time.monotonic() + 3600,
            )
        )
    assert protected_calls == 0


@pytest.mark.parametrize("status", [200, 500])
def test_protected_route_must_refuse_captured_token(monkeypatch, status) -> None:
    class Admin:
        def get_service_principal(self, principal_id: str) -> Any:
            return SimpleNamespace(id=principal_id, application_id=APPLICATION, active=False)

    calls = 0

    async def workspace_wait(*_args: Any, **_kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return "inactive"

    async def protected(*_args: Any, **_kwargs: Any) -> int:
        return status

    monkeypatch.setattr(identity, "_principal_status", protected)
    _install_workspace_wait(monkeypatch, workspace_wait)
    with pytest.raises(RuntimeError, match="inactive_identity_still_authorized"):
        asyncio.run(
            identity._verify_revocation_denial(
                cast(Any, Admin()),
                _workspace(),
                PRINCIPAL,
                APPLICATION,
                TOKEN,
                int(time.time()) + 3600,
                "https://app.fabricated.example",
                "/api/v1",
                deadline=time.monotonic() + 3600,
            )
        )
    assert calls == 2


def test_expired_captured_token_cannot_prove_revocation(monkeypatch) -> None:
    class Admin:
        def get_service_principal(self, principal_id: str) -> Any:
            return SimpleNamespace(id=principal_id, application_id=APPLICATION, active=False)

    async def workspace_wait(*_args: Any, **_kwargs: Any) -> str:
        return "inactive"

    protected_calls = 0

    async def protected(*_args: Any, **_kwargs: Any) -> int:
        nonlocal protected_calls
        protected_calls += 1
        return 401

    monkeypatch.setattr(identity, "_principal_status", protected)
    _install_workspace_wait(monkeypatch, workspace_wait)
    with pytest.raises(RuntimeError, match="revocation_token_expired_before_denial"):
        asyncio.run(
            identity._verify_revocation_denial(
                cast(Any, Admin()),
                _workspace(),
                PRINCIPAL,
                APPLICATION,
                TOKEN,
                int(time.time()) - 1,
                "https://app.fabricated.example",
                "/api/v1",
                deadline=time.monotonic() + 3600,
            )
        )
    assert protected_calls == 0


def test_token_must_remain_unexpired_after_protected_refusal(monkeypatch) -> None:
    class Admin:
        def get_service_principal(self, principal_id: str) -> Any:
            return SimpleNamespace(id=principal_id, application_id=APPLICATION, active=False)

    times = iter((100, 200))
    monkeypatch.setattr(identity.time, "time", lambda: next(times))
    monkeypatch.setattr(
        identity,
        "wait_for_workspace_identity",
        _inactive_workspace,
    )

    async def protected(*_args: Any, **_kwargs: Any) -> int:
        return 403

    monkeypatch.setattr(identity, "_principal_status", protected)
    with pytest.raises(RuntimeError, match="revocation_token_expired_at_denial"):
        asyncio.run(
            identity._verify_revocation_denial(
                cast(Any, Admin()),
                _workspace(),
                PRINCIPAL,
                APPLICATION,
                TOKEN,
                150,
                "https://app.fabricated.example",
                "/api/v1",
                deadline=time.monotonic() + 3600,
            )
        )


class _RestoreScenario:
    def __init__(self) -> None:
        self.active = True
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.restore_started = asyncio.Event()
        self.restore_release = asyncio.Event()
        self.wait_calls = 0
        self.service = SimpleNamespace(
            current_user=SimpleNamespace(me=self.current_user),
            config=SimpleNamespace(host=HOST),
        )

    def current_user(self) -> Any:
        return SimpleNamespace(id=PRINCIPAL)

    def operator(self) -> Any:
        return SimpleNamespace(id="fabricated-admin")

    def get_service_principal(self, principal_id: str) -> Any:
        return SimpleNamespace(id=principal_id, application_id=APPLICATION, active=self.active)

    def set_service_principal_active(self, principal_id: str, active: bool) -> None:
        assert principal_id == PRINCIPAL
        self.active = active

    async def validate(self, *_args: Any) -> tuple[str, str]:
        return PRINCIPAL, APPLICATION

    async def authorization(self, _workspace: Any) -> str:
        return TOKEN

    async def workspace_wait(self, *_args: Any, expected_active: bool, **_kwargs: Any) -> str:
        self.wait_calls += 1
        if self.wait_calls == 1:
            self.entered.set()
            await self.release.wait()
        return "inactive" if not expected_active else "active"

    async def protected(self, *_args: Any, **_kwargs: Any) -> int:
        return 403

    async def fresh_access(self, *_args: Any) -> Any:
        self.restore_started.set()
        await self.restore_release.wait()
        return SimpleNamespace(host=HOST, authorization=TOKEN)


async def _cancel_and_restore(scenario: _RestoreScenario, reference: Path) -> None:
    async def refresh() -> Any:
        return SimpleNamespace(host=HOST, authorization=TOKEN)

    task = asyncio.create_task(
        identity.revoke_restore_test_identity(
            identity_admin=cast(Any, scenario),
            service_workspace=cast(Any, scenario.service),
            identity_reference=reference,
            app_principal_id="fabricated-runtime-app",
            app_name="fabricated-app",
            origin="https://app.fabricated.example",
            prefix="/api/v1",
            refresh_credential=refresh,
        )
    )
    await _await_event_or_task(scenario.entered, task)
    task.cancel()
    scenario.release.set()
    await _await_event_or_task(scenario.restore_started, task)
    task.cancel()
    scenario.restore_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scenario.active is True


def test_repeated_cancellation_waits_for_owned_identity_restore(
    monkeypatch, tmp_path: Path
) -> None:
    scenario = _RestoreScenario()
    monkeypatch.setattr(identity, "_validate_revocation_target", scenario.validate)
    monkeypatch.setattr(identity, "_authorization_async", scenario.authorization)
    monkeypatch.setattr(identity, "wait_for_workspace_identity", scenario.workspace_wait)
    monkeypatch.setattr(identity, "_principal_status", scenario.protected)
    monkeypatch.setattr(identity, "fresh_workspace_access", scenario.fresh_access)
    monkeypatch.setattr(
        "selected_profiles_expiry._token_expiry", lambda _token: int(time.time()) + 3600
    )
    reference = tmp_path / "owned-test-identity.json"
    reference.write_text(json.dumps({"id": PRINCIPAL, "application_id": APPLICATION}))
    asyncio.run(_cancel_and_restore(scenario, reference))
