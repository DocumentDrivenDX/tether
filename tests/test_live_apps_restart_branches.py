"""Unknown-effect restart sequencing and recovery over a fabricated admin boundary."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import qualify_live_apps_lifecycle as lifecycle
from qualify_live_apps_incomplete import LiveQualificationIncomplete

SOURCE = {"TETHER_QUALIFICATION_APP": "fabricated-app"}
RUNNING = {("RUNNING", "ACTIVE")}


class _Monitor:
    invocation_id = "invocation-1"

    def __init__(self, report: Any = None) -> None:
        self.report = {"event": "shutdown"} if report is None else report
        self.waits = 0

    async def wait_shutdown_report(self) -> dict[str, Any]:
        self.waits += 1
        if isinstance(self.report, BaseException):
            raise self.report
        return dict(self.report)


class _Admin:
    """Records commands and waits; selected steps can be made to fail."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.states: list[set[tuple[str, str]]] = []
        self.fail_command: tuple[str, BaseException] | None = None
        self.fail_state: tuple[int, BaseException] | None = None

    async def cli(
        self, _profile: str, _config: str | None, _source: dict[str, str], *args: str
    ) -> str:
        self.commands.append(args)
        if self.fail_command is not None and self.fail_command[0] == args[1]:
            raise self.fail_command[1]
        return ""

    async def wait(self, *args: Any) -> None:
        self.states.append(args[-1])
        if self.fail_state is not None and self.fail_state[0] == len(self.states):
            raise self.fail_state[1]


def _install(monkeypatch: pytest.MonkeyPatch, validate: Any = None) -> _Admin:
    admin = _Admin()
    monkeypatch.setattr(lifecycle, "_admin_cli", admin.cli)
    monkeypatch.setattr(lifecycle, "_wait_app_state", admin.wait)

    def accept(_root: object, report: dict[str, Any], *, invocation_id: str) -> None:
        assert (report, invocation_id) == ({"event": "shutdown"}, "invocation-1")
        if validate is not None:
            raise validate

    monkeypatch.setattr(lifecycle, "validate_shutdown_observation", accept)
    return admin


def _restart(monitor: _Monitor) -> dict[str, Any]:
    fake: Any = monitor
    return asyncio.run(lifecycle.restart_for_unknown_effect("p", None, SOURCE, fake))


def test_restart_stops_waits_validates_report_then_starts_and_waits_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch)
    report = _restart(_Monitor())
    assert report == {"event": "shutdown", "worker_invocation_id": "invocation-1"}
    assert [command[:2] for command in admin.commands] == [("apps", "stop"), ("apps", "start")]
    assert all(command[-2:] == ("fabricated-app", "--no-wait") for command in admin.commands)
    assert admin.states[0] == {("STOPPED", "STOPPED"), ("UNAVAILABLE", "STOPPED")}
    assert admin.states[1] == RUNNING


def test_restart_requires_named_app() -> None:
    fake: Any = _Monitor()
    with pytest.raises(RuntimeError, match="qualification_app_name_missing"):
        asyncio.run(lifecycle.restart_for_unknown_effect("p", None, {}, fake))


def test_failure_before_stop_completes_does_not_attempt_recovery_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch)
    admin.fail_command = ("stop", RuntimeError("qualification_admin_cli_failed"))
    with pytest.raises(RuntimeError) as caught:
        _restart(_Monitor())
    assert str(caught.value) == "qualification_restart_stop_command_qualification_admin_cli_failed"
    assert [command[1] for command in admin.commands] == ["stop"]


def test_non_runtime_failure_reports_only_its_type_name(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _install(monkeypatch)
    admin.fail_command = ("stop", KeyError("secret-marker"))
    with pytest.raises(RuntimeError) as caught:
        _restart(_Monitor())
    assert str(caught.value) == "qualification_restart_stop_command_KeyError"


def test_unqualified_shutdown_observation_restarts_app_and_reports_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch, validate=ValueError("fabricated detail"))
    with pytest.raises(RuntimeError) as caught:
        _restart(_Monitor())
    assert str(caught.value) == (
        "qualification_restart_shutdown_report_live_apps_shutdown_observation_not_qualified"
    )
    assert [command[1] for command in admin.commands] == ["stop", "start"]
    assert admin.states[-1] == RUNNING


def test_failed_final_wait_still_attempts_recovery_start(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = _install(monkeypatch)
    admin.fail_state = (2, RuntimeError("qualification_admin_cli_failed"))
    with pytest.raises(RuntimeError) as caught:
        _restart(_Monitor())
    assert str(caught.value) == (
        "qualification_restart_wait_running_qualification_admin_cli_failed"
    )
    assert [command[1] for command in admin.commands] == ["stop", "start", "start"]


def test_recovery_start_failure_after_stopped_state_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch, validate=ValueError("fabricated detail"))
    starts = 0

    async def failing_start(_p: str, _c: str | None, _s: dict[str, str], *args: str) -> str:
        nonlocal starts
        if args[1] == "start":
            starts += 1
            raise RuntimeError("start refused")
        return await admin.cli(_p, _c, _s, *args)

    monkeypatch.setattr(lifecycle, "_admin_cli", failing_start)
    with pytest.raises(RuntimeError) as caught:
        _restart(_Monitor())
    assert str(caught.value) == (
        "qualification_restart_shutdown_report_live_apps_shutdown_observation_not_qualified"
        "_recovery_start_failed"
    )
    assert starts == 1


def test_incomplete_observation_after_stop_restores_app_then_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch)
    incomplete = LiveQualificationIncomplete("live_shutdown_report_not_observed")
    with pytest.raises(LiveQualificationIncomplete) as caught:
        _restart(_Monitor(report=incomplete))
    assert caught.value is incomplete
    assert [command[1] for command in admin.commands] == ["stop", "start"]
    assert admin.states[-1] == RUNNING


def test_incomplete_observation_before_stop_completes_does_not_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch)
    admin.fail_state = (1, LiveQualificationIncomplete("live_app_state_not_observed"))
    with pytest.raises(LiveQualificationIncomplete, match="live_app_state_not_observed"):
        _restart(_Monitor())
    assert [command[1] for command in admin.commands] == ["stop"]


def test_incomplete_observation_with_failed_recovery_is_not_a_behavior_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _install(monkeypatch)
    incomplete = LiveQualificationIncomplete("live_shutdown_report_not_observed")

    async def broken_wait(*args: Any) -> None:
        admin.states.append(args[-1])
        if len(admin.states) > 1:
            raise RuntimeError("wait failed")

    monkeypatch.setattr(lifecycle, "_wait_app_state", broken_wait)
    with pytest.raises(LiveQualificationIncomplete) as caught:
        _restart(_Monitor(report=incomplete))
    assert caught.value.reason == "live_app_state_not_observed"
