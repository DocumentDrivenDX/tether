"""Offline controls for performance admission and its fail-closed boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import performance_admission
import pytest
from performance_activity import ActivityState
from performance_admission import (
    ActivityObservation,
    HostBinding,
    NonBlockingFileLock,
    Phase,
    ProcessTableObserver,
    Reservation,
    ReservationFile,
    run_admitted,
    validate_reservation,
)


def _binding() -> HostBinding:
    return HostBinding("a" * 64)


def _reservation(*, binding: HostBinding | None = None, expires: str | None = None) -> Reservation:
    return Reservation(
        reservation_id="reservation-1",
        owner="operator-1",
        control_scope="exclusive-known-reference-mac",
        binding_sha256=(binding or _binding()).profile_sha256,
        exclusive=True,
        expires_at_utc=expires or (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


class _ReservationPort:
    def __init__(self, reservation: Reservation | None) -> None:
        self.reservation = reservation

    def read(self) -> Reservation | None:
        return self.reservation


class _BindingObserver:
    def __init__(self, binding: HostBinding | None = None) -> None:
        self.binding = binding or _binding()

    def observe(self) -> HostBinding:
        return self.binding


class _Observer:
    def __init__(self, states: list[ActivityState]) -> None:
        self.states = list(states)
        self.phases: list[Phase] = []
        self.thread_ids: list[int] = []

    def observe(self, phase: Phase) -> ActivityObservation:
        self.phases.append(phase)
        self.thread_ids.append(threading.get_ident())
        state = self.states.pop(0)
        return ActivityObservation(
            "fabricated-independent-observer",
            phase,
            state,
            "2026-11-02T00:00:00+00:00",
            "fabricated_observation",
        )


class _Lock:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_: object) -> None:
        self.exited = True


def test_reservation_requires_exact_host_exclusive_scope_and_current_expiry() -> None:
    with pytest.raises(RuntimeError, match="host_binding_mismatch"):
        validate_reservation(_reservation(), expected=HostBinding("b" * 64))
    with pytest.raises(RuntimeError, match="scope_not_exclusive"):
        validate_reservation(
            Reservation(
                "r",
                "owner",
                "",
                _binding().profile_sha256,
                True,
                _reservation().expires_at_utc,
            ),
            expected=_binding(),
        )


def test_machine_binding_uses_a_pinned_reference_profile_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = {
        "python": "3.11.17",
        "os_family": "Darwin",
        "architecture": "arm64",
        "os_version": "fabricated-version",
        "os_build": "fabricated-build",
        "cpu_model": "fabricated-cpu",
        "physical_cores": 8,
        "logical_cores": 10,
        "memory_bytes": 1_000_000,
        "storage": {
            "SolidState": True,
            "Internal": True,
            "BusProtocol": "fabricated-bus",
            "FilesystemType": "apfs",
        },
        "profile_scope": "fabricated-machine-scope",
        "machine_class": "fabricated-machine-class",
    }
    path = tmp_path / "machine-profile.json"
    payload = json.dumps(profile, sort_keys=True).encode()
    path.write_bytes(payload)
    monkeypatch.setattr(
        performance_admission,
        "REFERENCE_MACHINE_PROFILE_SHA256",
        hashlib.sha256(payload).hexdigest(),
    )

    binding = HostBinding.from_reference_file(path)

    assert len(binding.profile_sha256) == 64
    path.write_bytes(payload + b" ")
    with pytest.raises(RuntimeError, match="reference_machine_profile_invalid"):
        HostBinding.from_reference_file(path)
    with pytest.raises(RuntimeError, match="expired"):
        validate_reservation(_reservation(expires="2020-01-01T00:00:00+00:00"), expected=_binding())


def test_reservation_file_requires_complete_non_group_writable_attestation(tmp_path: Path) -> None:
    path = tmp_path / "reservation.json"
    path.write_text("{}", encoding="utf-8")
    assert ReservationFile(path).read() is None
    value = {
        "reservation_id": "reservation-1",
        "owner": "operator-1",
        "control_scope": "exclusive-known-reference-mac",
        "binding_sha256": _binding().profile_sha256,
        "exclusive": True,
        "expires_at_utc": "2099-01-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    assert ReservationFile(path).read() == _reservation(expires=value["expires_at_utc"])


def test_process_observer_reports_aggregate_activity_without_process_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_ps(*args, **kwargs):
        del kwargs
        commands.append(args[0])
        return SimpleNamespace(stdout="501 100 1 S\n501 101 100 S\n501 200 1 R\n")

    monkeypatch.setattr("performance_activity.platform.system", lambda: "Darwin")
    monkeypatch.setattr("performance_activity.os.getpid", lambda: 100)
    monkeypatch.setattr("performance_activity.os.getuid", lambda: 501)
    monkeypatch.setattr("performance_activity.subprocess.run", fake_ps)

    observation = ProcessTableObserver().observe("before")

    assert observation.state == "interference"
    assert observation.evidence == "same_uid_external_count=1;other_uid_external_count=0"
    assert commands == [["/bin/ps", "-axo", "uid=,pid=,ppid=,state="]]
    assert "100" not in observation.evidence
    assert "200" not in observation.evidence


def test_process_observer_fails_closed_on_malformed_or_missing_self_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("performance_activity.platform.system", lambda: "Darwin")
    monkeypatch.setattr("performance_activity.os.getpid", lambda: 100)
    monkeypatch.setattr(
        "performance_activity.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="bad row\n"),
    )

    observation = ProcessTableObserver().observe("before")

    assert observation.state == "unknown"
    assert observation.evidence == "process_table_incomplete"


def test_process_observer_applies_explicit_system_process_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("performance_activity.platform.system", lambda: "Darwin")
    monkeypatch.setattr("performance_activity.os.getpid", lambda: 100)
    monkeypatch.setattr("performance_activity.os.getuid", lambda: 501)
    monkeypatch.setattr(
        "performance_activity.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="501 100 1 S\n501 101 100 S\n0 300 1 I\n"),
    )

    sleeping_system = ProcessTableObserver().observe("before")

    assert sleeping_system.state == "quiet"
    assert sleeping_system.evidence == "same_uid_external_count=0;other_uid_external_count=0"

    monkeypatch.setattr(
        "performance_activity.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="501 100 1 S\n501 101 100 S\n0 300 1 R\n"),
    )
    active_system = ProcessTableObserver().observe("before")

    assert active_system.state == "unknown"
    assert active_system.evidence == "same_uid_external_count=0;other_uid_external_count=1"

    monkeypatch.setattr(
        "performance_activity.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="501 100 1 S\n501 101 100 S\n0 300 1 U\n"),
    )
    uninterruptible_io = ProcessTableObserver().observe("before")

    assert uninterruptible_io.state == "unknown"
    assert uninterruptible_io.evidence == "same_uid_external_count=0;other_uid_external_count=1"


def test_activity_interference_before_run_prevents_workload_and_releases_lock() -> None:
    observer = _Observer(["interference"])
    binding_observer = _BindingObserver()
    lock = _Lock()
    called = False

    async def workload() -> None:
        nonlocal called
        called = True

    with pytest.raises(RuntimeError, match="not_quiet:before"):
        asyncio.run(
            run_admitted(
                workload,
                reservation_port=_ReservationPort(_reservation()),
                binding_observer=binding_observer,
                activity_observer=observer,
                lock=lock,
                expected_binding=_binding(),
            )
        )
    assert not called
    assert lock.entered
    assert lock.exited
    assert observer.phases == ["before"]


def test_loss_of_admission_through_run_cancels_work_and_releases_lock() -> None:
    observer = _Observer(["quiet", "quiet", "interference"])
    binding_observer = _BindingObserver()
    lock = _Lock()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    sleeping: list[float] = []

    async def workload() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def immediate_sleep(seconds: float) -> None:
        sleeping.append(seconds)
        await started.wait()

    async def run() -> None:
        with pytest.raises(RuntimeError, match="not_quiet:through"):
            await run_admitted(
                workload,
                reservation_port=_ReservationPort(_reservation()),
                binding_observer=binding_observer,
                activity_observer=observer,
                lock=lock,
                expected_binding=_binding(),
                sleep=immediate_sleep,
            )

    asyncio.run(run())
    assert cancelled.is_set()
    assert lock.entered
    assert lock.exited
    assert observer.phases == ["before", "through", "through"]
    assert sleeping == [1.0]


def test_through_observer_exception_cancels_started_work_and_releases_lock() -> None:
    class RaisingObserver:
        def observe(self, phase: Phase) -> ActivityObservation:
            if phase == "through":
                raise RuntimeError("fabricated_observer_failure")
            return ActivityObservation(
                "fabricated-independent-observer",
                phase,
                "quiet",
                "2026-11-02T00:00:00+00:00",
                "fabricated_observation",
            )

    lock = _Lock()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def workload() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(RuntimeError, match="fabricated_observer_failure"):
        asyncio.run(
            run_admitted(
                workload,
                reservation_port=_ReservationPort(_reservation()),
                binding_observer=_BindingObserver(),
                activity_observer=RaisingObserver(),
                lock=lock,
                expected_binding=_binding(),
            )
        )
    assert started.is_set()
    assert cancelled.is_set()
    assert lock.exited


def test_reservation_port_exception_before_task_creation_releases_lock() -> None:
    class FailingReservation:
        def __init__(self) -> None:
            self.reads = 0

        def read(self) -> Reservation:
            self.reads += 1
            if self.reads == 2:
                raise RuntimeError("fabricated_reservation_read_failure")
            return _reservation()

    observer = _Observer(["quiet"])
    lock = _Lock()
    workload_started = False

    async def workload() -> None:
        nonlocal workload_started
        workload_started = True

    with pytest.raises(RuntimeError, match="fabricated_reservation_read_failure"):
        asyncio.run(
            run_admitted(
                workload,
                reservation_port=FailingReservation(),
                binding_observer=_BindingObserver(),
                activity_observer=observer,
                lock=lock,
                expected_binding=_binding(),
            )
        )
    assert not workload_started
    assert lock.exited


def test_repeated_cancellation_retains_lock_until_started_work_is_reaped() -> None:
    class RaisingObserver:
        def observe(self, phase: Phase) -> ActivityObservation:
            if phase == "through":
                raise RuntimeError("fabricated_observer_failure")
            return ActivityObservation(
                "fabricated-independent-observer",
                phase,
                "quiet",
                "2026-11-02T00:00:00+00:00",
                "fabricated_observation",
            )

    async def run() -> None:
        lock = _Lock()
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()

        async def workload() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await finish_cleanup.wait()

        owner = asyncio.create_task(
            run_admitted(
                workload,
                reservation_port=_ReservationPort(_reservation()),
                binding_observer=_BindingObserver(),
                activity_observer=RaisingObserver(),
                lock=lock,
                expected_binding=_binding(),
            )
        )
        await cleanup_started.wait()
        assert lock.entered
        assert not lock.exited
        owner.cancel()
        owner.cancel()
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert lock.exited

    asyncio.run(run())


def test_reservation_change_during_run_cancels_work_and_releases_lock() -> None:
    current = _reservation()
    changed = Reservation(
        "replacement-reservation",
        current.owner,
        current.control_scope,
        current.binding_sha256,
        True,
        current.expires_at_utc,
    )

    class ChangingReservation:
        def __init__(self) -> None:
            self.reads = 0

        def read(self) -> Reservation:
            self.reads += 1
            return changed if self.reads >= 4 else current

    observer = _Observer(["quiet", "quiet"])
    lock = _Lock()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def workload() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def immediate_sleep(seconds: float) -> None:
        assert seconds == 1.0
        await started.wait()

    with pytest.raises(RuntimeError, match="reservation_changed_during_run"):
        asyncio.run(
            run_admitted(
                workload,
                reservation_port=ChangingReservation(),
                binding_observer=_BindingObserver(),
                activity_observer=observer,
                lock=lock,
                expected_binding=_binding(),
                sleep=immediate_sleep,
            )
        )
    assert cancelled.is_set()
    assert lock.entered
    assert lock.exited


def test_quiet_admission_samples_all_three_phases_and_keeps_lock_until_return() -> None:
    observer = _Observer(["quiet", "quiet", "quiet"])
    binding_observer = _BindingObserver()
    lock = _Lock()

    async def workload() -> str:
        assert lock.entered
        assert not lock.exited
        return "fabricated result"

    value, admission = asyncio.run(
        run_admitted(
            workload,
            reservation_port=_ReservationPort(_reservation()),
            binding_observer=binding_observer,
            activity_observer=observer,
            lock=lock,
            expected_binding=_binding(),
        )
    )
    assert value == "fabricated result"
    assert [item.phase for item in admission.observations] == ["before", "through", "after"]
    assert lock.exited
    assert set(admission.as_dict()) == {
        "control_scope",
        "binding_sha256",
        "observations",
        "observer_control",
    }
    control = admission.as_dict()["observer_control"]
    assert isinstance(control, dict)
    assert control["sampling_interval_seconds"] == 1.0


def test_admission_io_runs_off_the_event_loop_thread() -> None:
    loop_thread = threading.get_ident()
    observer = _Observer(["quiet", "quiet", "quiet"])
    binding_threads: list[int] = []
    reservation_threads: list[int] = []

    class RecordingBinding(_BindingObserver):
        def observe(self) -> HostBinding:
            binding_threads.append(threading.get_ident())
            return super().observe()

    class RecordingReservation(_ReservationPort):
        def read(self) -> Reservation:
            reservation_threads.append(threading.get_ident())
            reservation = super().read()
            assert reservation is not None
            return reservation

    async def workload() -> str:
        return "completed"

    value, _admission = asyncio.run(
        run_admitted(
            workload,
            reservation_port=RecordingReservation(_reservation()),
            binding_observer=RecordingBinding(),
            activity_observer=observer,
            lock=_Lock(),
            expected_binding=_binding(),
        )
    )

    assert value == "completed"
    assert binding_threads
    assert all(thread_id != loop_thread for thread_id in binding_threads)
    assert reservation_threads
    assert all(thread_id != loop_thread for thread_id in reservation_threads)
    assert observer.thread_ids
    assert all(thread_id != loop_thread for thread_id in observer.thread_ids)


def test_lock_is_nonblocking_and_same_owner_cannot_overlap(tmp_path: Path) -> None:
    path = tmp_path / "bench.lock"
    with (
        NonBlockingFileLock(path),
        pytest.raises(RuntimeError, match="performance_lock_unavailable"),
        NonBlockingFileLock(path),
    ):
        pytest.fail("a second benchmark owner acquired the lock")
