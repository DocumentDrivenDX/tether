"""Fail-closed host admission for explicit reference-performance runs."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import plistlib
import stat
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TypeVar

from performance_activity import (
    SYSTEM_PROCESS_EXCLUSION_POLICY,
    ActivityObservation,
    Phase,
    ProcessTableObserver,
)
from selected_profiles_process import await_retained

T = TypeVar("T")
REFERENCE_MACHINE_PROFILE_SHA256 = (
    "28da12e7879d7af2bf105c081d70843d15b06354176e485c0309522c732a4961"
)
REFERENCE_CONTROL_SCOPE = "exclusive-known-reference-mac"
_MACHINE_FIELDS = (
    "python",
    "os_family",
    "architecture",
    "os_version",
    "os_build",
    "cpu_model",
    "physical_cores",
    "logical_cores",
    "memory_bytes",
    "SolidState",
    "Internal",
    "BusProtocol",
    "FilesystemType",
)
_RESERVATION_FIELDS = {
    "reservation_id",
    "owner",
    "control_scope",
    "binding_sha256",
    "exclusive",
    "expires_at_utc",
}


@dataclass(frozen=True, slots=True)
class HostBinding:
    profile_sha256: str

    @classmethod
    def current(cls) -> HostBinding:
        return cls(_profile_digest(_observe_machine_profile()))

    @classmethod
    def from_reference_file(cls, path: Path) -> HostBinding:
        try:
            metadata = path.lstat()
            payload = path.read_bytes()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022
                or hashlib.sha256(payload).hexdigest() != REFERENCE_MACHINE_PROFILE_SHA256
            ):
                raise RuntimeError("performance_reference_machine_profile_invalid")
            profile_value: object = json.loads(payload)
            profile = _profile_fields(profile_value)
        except (OSError, ValueError, TypeError, KeyError):
            raise RuntimeError("performance_reference_machine_profile_invalid") from None
        return cls(_profile_digest(profile))


class BindingObserver(Protocol):
    def observe(self) -> HostBinding: ...


class SystemBindingObserver:
    def observe(self) -> HostBinding:
        return HostBinding.current()


@dataclass(frozen=True, slots=True)
class Reservation:
    reservation_id: str
    owner: str
    control_scope: str
    binding_sha256: str
    exclusive: bool
    expires_at_utc: str


class ReservationPort(Protocol):
    def read(self) -> Reservation | None: ...


class ActivityObserver(Protocol):
    def observe(self, phase: Phase) -> ActivityObservation: ...


class ExclusiveLock(Protocol):
    def __enter__(self) -> ExclusiveLock: ...

    def __exit__(self, *_: object) -> None: ...


class ReservationFile:
    """Read an owner-controlled reservation document; it is not proof alone."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> Reservation | None:
        try:
            metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o022
            ):
                return None
            value: object = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or any(not isinstance(key, str) for key in value)
                or value.keys() != _RESERVATION_FIELDS
            ):
                return None
            result = Reservation(
                reservation_id=_text(value.get("reservation_id")),
                owner=_text(value.get("owner")),
                control_scope=_text(value.get("control_scope")),
                binding_sha256=_text(value.get("binding_sha256")),
                exclusive=value.get("exclusive") is True,
                expires_at_utc=_text(value.get("expires_at_utc")),
            )
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return None


class NonBlockingFileLock:
    """OS advisory lock shared by cooperating reference-performance owners."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream = None

    def __enter__(self) -> NonBlockingFileLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a+")
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            self._stream.close()
            self._stream = None
            raise RuntimeError("performance_lock_unavailable") from None
        return self

    def __exit__(self, *_: object) -> None:
        if self._stream is not None:
            try:
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            finally:
                self._stream.close()
                self._stream = None


@dataclass(frozen=True, slots=True)
class Admission:
    reservation_id: str
    owner: str
    control_scope: str
    binding: HostBinding
    observations: tuple[ActivityObservation, ...]
    sample_interval_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "control_scope": self.control_scope,
            "binding_sha256": self.binding.profile_sha256,
            "observations": [asdict(item) for item in self.observations],
            "observer_control": {
                "sampling_interval_seconds": self.sample_interval_seconds,
                "measured_observer_overhead_ms": sum(
                    item.measurement_overhead_ms for item in self.observations
                ),
                "maximum_subprocess_call_seconds": 5.0,
                "limits": [
                    "System observations run in retained worker threads with bounded calls.",
                    "Evidence-file writes have no separate timeout; "
                    "ownership remains until they finish.",
                    "Process-table visibility cannot establish provider physical co-tenancy.",
                    SYSTEM_PROCESS_EXCLUSION_POLICY,
                ],
            },
        }


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("performance_reservation_invalid")
    return value.strip()


def _profile_fields(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(value.get("storage"), dict):
        raise ValueError("performance_machine_profile_invalid")
    storage = value["storage"]
    profile = {
        "python": value.get("python"),
        "os_family": value.get("os_family"),
        "architecture": value.get("architecture"),
        "os_version": value.get("os_version"),
        "os_build": value.get("os_build"),
        "cpu_model": value.get("cpu_model"),
        "physical_cores": value.get("physical_cores"),
        "logical_cores": value.get("logical_cores"),
        "memory_bytes": value.get("memory_bytes"),
        "SolidState": storage.get("SolidState"),
        "Internal": storage.get("Internal"),
        "BusProtocol": storage.get("BusProtocol"),
        "FilesystemType": storage.get("FilesystemType"),
    }
    if set(profile) != set(_MACHINE_FIELDS):
        raise ValueError("performance_machine_profile_invalid")
    for key, field in profile.items():
        if key in {"physical_cores", "logical_cores", "memory_bytes"}:
            expected_type = int
        elif key in {"SolidState", "Internal"}:
            expected_type = bool
        else:
            expected_type = str
        if type(field) is not expected_type:
            raise ValueError("performance_machine_profile_invalid")
    return profile


def _profile_digest(profile: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _command_text(arguments: list[str]) -> str:
    result = subprocess.run(  # noqa: S603 - callers pass fixed system information commands
        arguments, check=True, capture_output=True, text=True, timeout=5
    )
    return result.stdout.strip()


def _observe_machine_profile() -> dict[str, object]:
    if platform.system() != "Darwin":
        raise RuntimeError("performance_machine_profile_unknown")
    disk = subprocess.run(
        ["/usr/sbin/diskutil", "info", "-plist", "/"],
        check=True,
        capture_output=True,
        timeout=5,
    )
    storage = plistlib.loads(disk.stdout)
    value = {
        "python": sys.version.split()[0],
        "os_family": platform.system(),
        "architecture": platform.machine(),
        "os_version": platform.mac_ver()[0],
        "os_build": _command_text(["/usr/bin/sw_vers", "-buildVersion"]),
        "cpu_model": _command_text(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"]),
        "physical_cores": int(_command_text(["/usr/sbin/sysctl", "-n", "hw.physicalcpu"])),
        "logical_cores": int(_command_text(["/usr/sbin/sysctl", "-n", "hw.logicalcpu"])),
        "memory_bytes": int(_command_text(["/usr/sbin/sysctl", "-n", "hw.memsize"])),
        "storage": {
            key: storage.get(key)
            for key in ("SolidState", "Internal", "BusProtocol", "FilesystemType")
        },
    }
    return _profile_fields(value)


def _expired(value: str, *, now: datetime | None = None) -> bool:
    try:
        expires = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        return True
    return expires <= (now or datetime.now(UTC))


def validate_reservation(
    reservation: Reservation | None,
    *,
    expected: HostBinding,
    now: datetime | None = None,
) -> Reservation:
    if reservation is None:
        raise RuntimeError("performance_reservation_missing_or_invalid")
    if not reservation.exclusive or reservation.control_scope != REFERENCE_CONTROL_SCOPE:
        raise RuntimeError("performance_reservation_scope_not_exclusive")
    if not reservation.reservation_id or not reservation.owner:
        raise RuntimeError("performance_reservation_identity_missing")
    if reservation.binding_sha256 != expected.profile_sha256:
        raise RuntimeError("performance_reservation_host_binding_mismatch")
    if _expired(reservation.expires_at_utc, now=now):
        raise RuntimeError("performance_reservation_expired")
    return reservation


def require_quiet(observation: ActivityObservation, phase: Phase) -> None:
    if observation.phase != phase or not observation.observer or not observation.evidence:
        raise RuntimeError("performance_activity_observation_invalid:" + phase)
    if observation.state != "quiet":
        raise RuntimeError("performance_activity_not_quiet:" + phase + ":" + observation.state)


async def run_admitted(
    workload: Callable[[], Awaitable[T]],
    *,
    reservation_port: ReservationPort,
    binding_observer: BindingObserver,
    activity_observer: ActivityObserver,
    lock: ExclusiveLock,
    expected_binding: HostBinding,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    sample_interval_seconds: float = 1.0,
) -> tuple[T, Admission]:
    """Run after independent admission; invalidate on any through-run interference."""
    if sample_interval_seconds <= 0:
        raise ValueError("performance_observation_interval_invalid")
    with lock:
        binding = await _retained_io(binding_observer.observe)
        if binding != expected_binding:
            raise RuntimeError("performance_reservation_host_binding_mismatch")
        reservation = validate_reservation(
            await _retained_io(reservation_port.read), expected=expected_binding
        )
        before = await _observe_activity(activity_observer, "before")
        require_quiet(before, "before")
        value, observations = await _run_owned_workload(
            workload,
            reservation_port=reservation_port,
            initial_reservation=reservation,
            expected_binding=expected_binding,
            activity_observer=activity_observer,
            sleep=sleep,
            sample_interval_seconds=sample_interval_seconds,
        )
    return value, Admission(
        reservation.reservation_id,
        reservation.owner,
        reservation.control_scope,
        expected_binding,
        tuple([before, *observations]),
        sample_interval_seconds,
    )


async def _retained_io(function: Callable[..., T], *args: object) -> T:
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    return await await_retained(task)


async def _observe_activity(observer: ActivityObserver, phase: Phase) -> ActivityObservation:
    def observe() -> ActivityObservation:
        started = time.perf_counter()
        value = observer.observe(phase)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return replace(value, measurement_overhead_ms=elapsed_ms)

    return await _retained_io(observe)


async def _confirm_reservation(
    port: ReservationPort, expected: HostBinding, initial: Reservation
) -> None:
    current = validate_reservation(await _retained_io(port.read), expected=expected)
    if current != initial:
        raise RuntimeError("performance_reservation_changed_during_run")


async def _run_owned_workload(
    workload: Callable[[], Awaitable[T]],
    *,
    reservation_port: ReservationPort,
    initial_reservation: Reservation,
    expected_binding: HostBinding,
    activity_observer: ActivityObserver,
    sleep: Callable[[float], Awaitable[None]],
    sample_interval_seconds: float,
) -> tuple[T, list[ActivityObservation]]:
    task: asyncio.Task[T] | None = None
    observations: list[ActivityObservation] = []
    try:
        await _confirm_reservation(reservation_port, expected_binding, initial_reservation)

        async def run_workload() -> T:
            return await workload()

        task = asyncio.create_task(run_workload())
        await asyncio.sleep(0)
        await _confirm_reservation(reservation_port, expected_binding, initial_reservation)
        through = await _observe_activity(activity_observer, "through")
        observations.append(through)
        require_quiet(through, "through")
        while not task.done():
            await sleep(sample_interval_seconds)
            await _confirm_reservation(reservation_port, expected_binding, initial_reservation)
            observation = await _observe_activity(activity_observer, "through")
            observations.append(observation)
            require_quiet(observation, "through")
        value = await task
        await _confirm_reservation(reservation_port, expected_binding, initial_reservation)
        after = await _observe_activity(activity_observer, "after")
        observations.append(after)
        require_quiet(after, "after")
        return value, observations
    except BaseException:
        if task is not None:
            cleanup = asyncio.create_task(_cancel_and_reap(task))
            await await_retained(cleanup)
        raise


async def _cancel_and_reap(task: asyncio.Task[object]) -> None:
    if not task.done():
        task.cancel()
    with contextlib.suppress(BaseException):
        await task


__all__ = [
    "ActivityObservation",
    "ActivityObserver",
    "Admission",
    "BindingObserver",
    "HostBinding",
    "NonBlockingFileLock",
    "ProcessTableObserver",
    "Reservation",
    "ReservationFile",
    "ReservationPort",
    "SystemBindingObserver",
    "run_admitted",
    "validate_reservation",
]
