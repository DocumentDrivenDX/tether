"""Run fixed reference workloads against existing, explicitly measured budgets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from benchmark_profiles import (
    DYNAMIC_IDENTITIES,
    IDENTITIES,
    METRICS,
    PROFILES,
)
from benchmark_ratchet import capture, independent_result, qualify
from performance_admission import (
    ActivityObservation,
    ActivityObserver,
    BindingObserver,
    ExclusiveLock,
    HostBinding,
    NonBlockingFileLock,
    Phase,
    ProcessTableObserver,
    ReservationFile,
    ReservationPort,
    SystemBindingObserver,
    run_admitted,
)
from process import run_command
from selected_profiles_process import await_retained
from web_artifacts import inventory, write_receipt
from web_performance_validation import (
    baselines,
    decode_json_object,
    runtime_versions,
    validate_budget,
    validate_measurement,
)

T = TypeVar("T")


async def _retained_io(function: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Finish owned filesystem work before propagating caller cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    return await await_retained(task)


def _failure_reason(error: BaseException) -> str:
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, TimeoutError):
        return "deadline_exhausted"
    if isinstance(error, OSError):
        return "filesystem_or_process_io_failed"
    if isinstance(error, ValueError):
        return "measurement_or_source_validation_failed"
    if isinstance(error, RuntimeError):
        return "admission_or_workload_failed"
    return "unexpected_failure"


def establish(
    root: Path, measurements: Path, directory: Path, output: Path, tolerance: float
) -> None:
    """Establish the first observed budget; this is not a regression comparison."""
    sources = inventory(root)
    if not sources:
        raise ValueError("first_baseline_source_identity_required")
    values = {}
    for name in PROFILES:
        payload = (measurements / f"{name}-measurement.json").read_bytes()
        observed = decode_json_object(payload)
        if observed.get("source_sha256") != sources:
            raise ValueError("first_baseline_measurement_source_changed:" + name)
        validate_measurement(name, observed)
        budget = capture(
            observed,
            hashlib.sha256(payload).hexdigest(),
            sorted(METRICS[name]),
            ["versions", *IDENTITIES[name], *DYNAMIC_IDENTITIES.get(name, ())],
            tolerance,
        )
        validate_budget(name, budget)
        if (directory / f"{name}-baseline.json").exists():
            raise ValueError("existing_measured_baseline_requires_deliberate_review:" + name)
        values[name] = budget
    if sources != inventory(root):
        raise ValueError("source_changed_during_first_baseline_establishment")
    output.mkdir(parents=True)
    directory.mkdir(parents=True, exist_ok=True)
    for name, budget in values.items():
        with (directory / f"{name}-baseline.json").open("x", encoding="utf-8") as file:
            json.dump(budget, file, indent=2, sort_keys=True)
    write_receipt(
        output / "first-baseline-establishment.json",
        {
            "status": "passed",
            "scope": "first-measured-baseline-established; no historical regression observation",
            "command": "web_performance --establish-from / validate full first measurements",
            "recorded_at": datetime.now(UTC).isoformat(),
            "source_sha256": sources,
            "measured_baselines": values,
            "test_results": [
                {
                    "node_id": f"scripts/web_performance.py::establish-{name}-baseline",
                    "outcome": "passed",
                }
                for name in PROFILES
            ],
            "limits": [
                "Only named metrics/profiles; proposed upstream target changes need disclosure"
            ],
        },
    )


async def run(
    root: Path,
    directory: Path,
    output: Path,
    *,
    apps_measurement: Path | None = None,
    reservation_port: ReservationPort | None = None,
    binding_observer: BindingObserver | None = None,
    activity_observer: ActivityObserver | None = None,
    lock: ExclusiveLock | None = None,
    expected_binding: HostBinding | None = None,
    sample_interval_seconds: float = 1.0,
) -> None:
    # A new run never leaves an older passing report beside a failed observation.
    await _retained_io(output.mkdir, parents=True)
    if (
        reservation_port is None
        or binding_observer is None
        or activity_observer is None
        or lock is None
        or expected_binding is None
    ):
        await _retained_io(
            (output / "admission-incomplete.json").write_text,
            json.dumps(
                {"status": "incomplete", "reason": "performance_admission_required"},
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        raise RuntimeError("performance_admission_required")
    pending = output / ".admission-pending"

    class _RecordingObserver:
        def __init__(self, wrapped: ActivityObserver) -> None:
            self.wrapped = wrapped
            self.values: list[ActivityObservation] = []

        def observe(self, phase: Phase) -> ActivityObservation:
            observation = self.wrapped.observe(phase)
            self.values.append(observation)
            path = output / "admission-observations.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(observation), sort_keys=True) + "\n")
            return observation

    observer = _RecordingObserver(activity_observer)

    async def _measure() -> dict[str, dict[str, str]]:
        budgets = await _retained_io(baselines, directory)
        source_sha256 = await _retained_io(inventory, root)
        baseline_sha256 = await _retained_io(_baseline_hashes, directory)
        await _run_measurements(
            root,
            output,
            apps_measurement,
            budgets,
            ratchet_output=pending,
        )
        if await _retained_io(inventory, root) != source_sha256:
            raise ValueError("source_changed_during_reference_measurement")
        if await _retained_io(_baseline_hashes, directory) != baseline_sha256:
            raise ValueError("baselines_changed_during_reference_measurement")
        return {"source_sha256": source_sha256, "baseline_sha256": baseline_sha256}

    try:
        # Optional Apps provenance is outside the admitted local measurement window.
        await _write_apps_observation(apps_measurement, output)
        source_bindings, admission = await run_admitted(
            _measure,
            reservation_port=reservation_port,
            binding_observer=binding_observer,
            activity_observer=observer,
            lock=lock,
            expected_binding=expected_binding,
            sample_interval_seconds=sample_interval_seconds,
        )
        if await _retained_io(inventory, root) != source_bindings["source_sha256"]:
            raise ValueError("source_changed_before_qualified_publication")
        if await _retained_io(_baseline_hashes, directory) != source_bindings["baseline_sha256"]:
            raise ValueError("baselines_changed_before_qualified_publication")
        final_admission = {
            **admission.as_dict(),
            **source_bindings,
            "status": "admitted",
            "scope": "controlled-local-reference-performance",
            "qualified_profiles": list(PROFILES),
            "runtime_versions": runtime_versions(),
        }
        admission_payload = json.dumps(final_admission, indent=2, sort_keys=True) + "\n"
        admission_sha256 = hashlib.sha256(admission_payload.encode()).hexdigest()
        await _retained_io(
            (pending / "admission.json").write_text,
            admission_payload,
        )
        await _retained_io(
            _finalize_ratchets,
            pending,
            admission_sha256,
            source_bindings["source_sha256"],
        )
        await _retained_io(os.replace, pending, output / "qualified-performance")
    except BaseException as error:
        await _retained_io(
            (output / "admission-incomplete.json").write_text,
            json.dumps(
                {
                    "status": "incomplete",
                    "reason": _failure_reason(error),
                    "observations": [asdict(item) for item in observer.values],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        raise


async def _run_measurements(
    root: Path,
    output: Path,
    apps_measurement: Path | None,
    budgets: dict,
    *,
    ratchet_output: Path,
) -> None:
    del apps_measurement
    await _retained_io(ratchet_output.mkdir, parents=True)
    for name, profile in PROFILES.items():
        measurement = output / f"{name}-measurement.json"
        result = await run_command(
            [
                sys.executable,
                str(root / "scripts" / profile.script),
                *profile.arguments,
                "--output",
                str(measurement),
            ],
            cwd=root,
            capture=False,
            deadline_seconds=profile.deadline_seconds,
        )
        if result.returncode:
            raise ValueError("reference_workload_failed:" + name)
        payload = await _retained_io(measurement.read_bytes)
        observed = decode_json_object(payload)
        validate_measurement(name, observed)
        compared = independent_result(payload, budgets[name])
        receipt = await _retained_io(qualify, observed, compared, root)
        provisional = {**receipt, "status": "provisional"}
        provisional.pop("test_results", None)
        await _retained_io(
            write_receipt, ratchet_output / f"{name}-ratchet-provisional.json", provisional
        )
        print(
            "Provisional reference measurement recorded; admission is pending: " + name,
            flush=True,
        )


async def _write_apps_observation(measurement: Path | None, output: Path) -> None:
    if measurement is None:
        await _write_apps_observation_receipt(
            output,
            {
                "status": "refused",
                "reason": "apps_observation_not_supplied",
                "measurement_sha256": None,
            },
        )
        return
    try:
        payload = await _retained_io(measurement.read_bytes)
    except OSError:
        await _write_apps_observation_receipt(
            output,
            {
                "status": "refused",
                "reason": "apps_observation_unavailable",
                "measurement_sha256": None,
            },
        )
        return
    try:
        decode_json_object(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        await _write_apps_observation_receipt(
            output,
            {
                "status": "refused",
                "reason": "apps_observation_invalid",
                "measurement_sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
        return
    await _retained_io((output / "apps-measurement.json").write_bytes, payload)
    await _write_apps_observation_receipt(
        output,
        {
            "status": "observational",
            "measurement_sha256": hashlib.sha256(payload).hexdigest(),
        },
    )


async def _write_apps_observation_receipt(output: Path, details: dict[str, object]) -> None:
    receipt = {
        **details,
        "scope": "optional-apps-network-observation",
        "qualified_nodes": [],
        "limits": [
            "Apps network latency is excluded from controlled performance pass/fail.",
            "This observation does not qualify a performance node or threshold.",
            "Provider physical co-tenancy and external load remain unknown.",
        ],
    }
    await _retained_io(write_receipt, output / "apps-observation.json", receipt)


def _baseline_hashes(directory: Path) -> dict[str, str]:
    return {
        f"{name}-baseline.json": hashlib.sha256(
            (directory / f"{name}-baseline.json").read_bytes()
        ).hexdigest()
        for name in PROFILES
    }


def _finalize_ratchets(
    directory: Path, admission_sha256: str, expected_source: dict[str, str]
) -> None:
    found: set[str] = set()
    for path in sorted(directory.glob("*-ratchet-provisional.json")):
        profile = path.name.removesuffix("-ratchet-provisional.json")
        if profile not in PROFILES or profile in found:
            raise ValueError("unexpected_provisional_reference_profile")
        found.add(profile)
        receipt = decode_json_object(path.read_bytes())
        metrics = receipt.get("metrics")
        if (
            receipt.get("status") != "provisional"
            or receipt.get("profile") != PROFILES[profile].profile
            or not isinstance(metrics, dict)
            or set(metrics) != METRICS[profile]
            or receipt.get("source_sha256") != expected_source
        ):
            raise ValueError("complete_provisional_reference_metrics_required:" + profile)
        receipt["status"] = "passed"
        receipt["admission_path"] = "admission.json"
        receipt["admission_sha256"] = admission_sha256
        receipt["baseline_path"] = f"{profile}-baseline.json"
        receipt["test_results"] = [
            {
                "node_id": f"scripts/benchmark_ratchet.py::{receipt['profile']}::{metric}",
                "outcome": "passed",
            }
            for metric in sorted(metrics)
        ]
        write_receipt(directory / f"{profile}-ratchet.json", receipt)
        path.unlink()
    if found != set(PROFILES):
        raise ValueError("all_controlled_reference_profiles_required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baselines", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--establish-from", type=Path)
    parser.add_argument("--apps-measurement", type=Path)
    parser.add_argument("--reservation-file", type=Path)
    parser.add_argument(
        "--machine-profile",
        type=Path,
        default=Path("/private/tmp/tether-performance-machine-profile.json"),
    )
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--tolerance", type=float, default=1.2)
    arguments = parser.parse_args()
    if arguments.establish_from is not None:
        establish(
            Path.cwd(),
            arguments.establish_from,
            arguments.baselines,
            arguments.output,
            arguments.tolerance,
        )
        return
    asyncio.run(
        run(
            Path.cwd(),
            arguments.baselines,
            arguments.output,
            apps_measurement=arguments.apps_measurement,
            reservation_port=(
                ReservationFile(arguments.reservation_file)
                if arguments.reservation_file is not None
                else None
            ),
            binding_observer=SystemBindingObserver(),
            activity_observer=ProcessTableObserver(),
            expected_binding=HostBinding.from_reference_file(arguments.machine_profile),
            lock=(
                NonBlockingFileLock(arguments.lock_file)
                if arguments.lock_file is not None
                else None
            ),
        )
    )


if __name__ == "__main__":
    main()
