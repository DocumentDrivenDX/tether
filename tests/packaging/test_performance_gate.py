"""Reference performance cannot silently use missing or small smoke budgets."""

import asyncio
import hashlib
import json
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import selected_profiles_process
import web_performance
from benchmark_profiles import DYNAMIC_IDENTITIES, EXTERNAL_PROFILES, MEMORY_ALLOCATIONS, METRICS
from benchmark_ratchet import capture, compare
from context_lifetime_controls import wait_event_or_task
from performance_admission import ActivityObservation, HostBinding, Reservation
from process_lifetime_controls import ShieldCancellationObserver
from web_artifacts import inventory
from web_performance import (
    IDENTITIES,
    PROFILES,
    baselines,
    runtime_versions,
)


class _AdmissionLock:
    def __init__(self) -> None:
        self.exited = False

    def __enter__(self) -> "_AdmissionLock":
        return self

    def __exit__(self, *_args: object) -> None:
        self.exited = True


class _HeldMeasurementIO:
    def __init__(self) -> None:
        self.write_started = asyncio.Event()
        self.write_release = threading.Event()
        self.write_finished = asyncio.Event()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lock = _AdmissionLock()
        self.observer: ShieldCancellationObserver | None = None

    def held_write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        assert self.loop is not None
        self.loop.call_soon_threadsafe(self.write_started.set)
        self.write_release.wait()
        path.write_text("completed after cancellation")
        self.loop.call_soon_threadsafe(self.write_finished.set)

    async def measure(self, *_args: object, ratchet_output: Path) -> None:
        assert self.observer is not None
        self.observer.target = asyncio.current_task()
        await web_performance._retained_io(
            self.held_write, ratchet_output / "held-write-complete.txt"
        )

    async def cancel_run(
        self,
        arguments: tuple[Path, Path, Path],
        options: dict[str, Any],
        observer: ShieldCancellationObserver,
    ) -> None:
        self.loop = asyncio.get_running_loop()
        self.observer = observer
        owner = asyncio.create_task(web_performance.run(*arguments, **options))
        observer.target = None
        try:
            await wait_event_or_task(self.write_started, owner)
            await wait_event_or_task(observer.shield_entries[0], owner)
            observer.additional_targets.add(owner)
            owner.cancel()
            await wait_event_or_task(observer.acknowledged[0], owner)
            owner.cancel()
            await wait_event_or_task(observer.acknowledged[1], owner)
            assert not self.lock.exited
            self.write_release.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert self.write_finished.is_set()
        finally:
            self.write_release.set()
            await asyncio.gather(owner, return_exceptions=True)


def write_baselines(directory):
    for name in (*PROFILES, *EXTERNAL_PROFILES):
        profile = PROFILES[name].profile if name in PROFILES else EXTERNAL_PROFILES[name]
        value = {
            "profile": profile,
            "receipt_sha256": "fabricated-receipt-digest",
            "identity": {"versions": runtime_versions(), **IDENTITIES[name]},
            "metrics": {path: {"observed": 1, "maximum": 1.2} for path in METRICS[name]},
            "tolerance": 1.2,
        }
        if name == "apps":
            value["identity"].update(
                {"workload.list_items_per_page": 0, "http_versions": ["HTTP/2"]}
            )
        (directory / f"{name}-baseline.json").write_text(json.dumps(value))


def test_reference_gate_requires_both_existing_measured_profiles(tmp_path):
    with pytest.raises(FileNotFoundError):
        baselines(tmp_path)
    write_baselines(tmp_path)
    assert set(baselines(tmp_path)) == set(PROFILES)


def test_apps_measurement_is_optional_observation_outside_qualified_nodes(tmp_path):
    measurement = tmp_path / "apps-measurement.json"
    payload = json.dumps(
        {"profile": "apps-public-client-read", "source_sha256": {"source.py": "old"}}
    ).encode()
    measurement.write_bytes(payload)
    output = tmp_path / "output"
    output.mkdir()

    asyncio.run(web_performance._write_apps_observation(measurement, output))

    receipt = json.loads((output / "apps-observation.json").read_text())
    assert receipt["status"] == "observational"
    assert receipt["measurement_sha256"] == hashlib.sha256(payload).hexdigest()
    assert "apps" not in receipt.get("test_results", [])
    assert (output / "apps-measurement.json").read_bytes() == payload
    assert not list(output.glob("*ratchet*"))


@pytest.mark.parametrize(
    ("measurement", "expected_reason"),
    [
        (None, "apps_observation_not_supplied"),
        ("missing", "apps_observation_unavailable"),
        ("malformed", "apps_observation_invalid"),
    ],
    ids=["omitted", "missing-file", "malformed-file"],
)
def test_refused_apps_observation_does_not_veto_six_profile_result(
    tmp_path, monkeypatch, measurement, expected_reason
):
    binding = HostBinding("a" * 64)
    reservation = Reservation(
        "fabricated-reservation",
        "fabricated-owner",
        "exclusive-known-reference-mac",
        binding.profile_sha256,
        True,
        "2099-01-01T00:00:00+00:00",
    )

    class ReservationPort:
        def read(self):
            return reservation

    class BindingObserver:
        def observe(self):
            return binding

    class ActivityObserver:
        def observe(self, phase):
            if phase == "before":
                assert (output / "apps-observation.json").exists()
            return ActivityObservation(
                "fabricated-observer", phase, "quiet", "2026-11-02T00:00:00+00:00", "counts only"
            )

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    async def fake_measure(root, output, apps_measurement, budgets, *, ratchet_output):
        del output, apps_measurement, budgets
        ratchet_output.mkdir(parents=True)
        for name, profile in PROFILES.items():
            receipt = {
                "status": "provisional",
                "profile": profile.profile,
                "source_sha256": inventory(root),
                "metrics": dict.fromkeys(METRICS[name], 1),
            }
            (ratchet_output / f"{name}-ratchet-provisional.json").write_text(json.dumps(receipt))

    monkeypatch.setattr(web_performance, "_run_measurements", fake_measure)
    measurement_path = tmp_path / "optional-apps.json"
    if measurement == "malformed":
        measurement_path.write_bytes(b"not-json")
    apps_input = None if measurement is None else measurement_path
    output = tmp_path / "six-profile-run"
    budgets = tmp_path / "baselines"
    budgets.mkdir()
    write_baselines(budgets)

    asyncio.run(
        web_performance.run(
            tmp_path,
            budgets,
            output,
            apps_measurement=apps_input,
            reservation_port=ReservationPort(),
            binding_observer=BindingObserver(),
            activity_observer=ActivityObserver(),
            lock=Lock(),
            expected_binding=binding,
        )
    )

    observation = json.loads((output / "apps-observation.json").read_text())
    assert observation["status"] == "refused"
    assert observation["reason"] == expected_reason
    assert observation["qualified_nodes"] == []
    qualified = output / "qualified-performance"
    admission = json.loads((qualified / "admission.json").read_text())
    assert admission["qualified_profiles"] == list(PROFILES)
    assert "apps" not in admission["qualified_profiles"]
    assert (
        sum(
            len(json.loads((qualified / f"{name}-ratchet.json").read_text())["test_results"])
            for name in PROFILES
        )
        == 54
    )


def test_failed_new_run_cannot_leave_an_old_passing_report(tmp_path, monkeypatch):
    budget_directory = tmp_path / "budgets"
    budget_directory.mkdir()
    write_baselines(budget_directory)
    output = tmp_path / "old-observation"
    output.mkdir()
    old_receipt = output / "store-ratchet.json"
    old_receipt.write_text("fabricated previous receipt")
    runner = AsyncMock()
    monkeypatch.setattr(web_performance, "run_command", runner)
    with pytest.raises(FileExistsError):
        asyncio.run(web_performance.run(tmp_path, budget_directory, output))
    runner.assert_not_awaited()
    assert old_receipt.read_text() == "fabricated previous receipt"


def test_reference_measurements_refuse_to_start_without_admission_controls(tmp_path, monkeypatch):
    runner = AsyncMock()
    monkeypatch.setattr(web_performance, "run_command", runner)
    output = tmp_path / "unadmitted-run"

    with pytest.raises(RuntimeError, match="performance_admission_required"):
        asyncio.run(
            web_performance.run(
                tmp_path,
                tmp_path / "baselines",
                output,
            )
        )

    runner.assert_not_awaited()
    receipt = json.loads((output / "admission-incomplete.json").read_text())
    assert receipt == {"status": "incomplete", "reason": "performance_admission_required"}


def test_after_observation_failure_keeps_provisional_ratchets_unqualified(tmp_path, monkeypatch):
    binding = HostBinding("a" * 64)
    reservation = Reservation(
        "fabricated-reservation",
        "fabricated-owner",
        "exclusive-known-reference-mac",
        binding.profile_sha256,
        True,
        "2099-01-01T00:00:00+00:00",
    )

    class ReservationPort:
        def read(self):
            return reservation

    class BindingObserver:
        def observe(self):
            return binding

    class ActivityObserver:
        def observe(self, phase):
            state = "interference" if phase == "after" else "quiet"
            return ActivityObservation(
                "fabricated-observer", phase, state, "2026-11-02T00:00:00+00:00", "counts only"
            )

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    async def fake_measure(root, output, apps_measurement, budgets, *, ratchet_output):
        del output, apps_measurement, budgets
        ratchet_output.mkdir(parents=True)
        for name, profile in PROFILES.items():
            receipt = {
                "status": "provisional",
                "profile": profile.profile,
                "source_sha256": inventory(root),
                "metrics": dict.fromkeys(METRICS[name], 1),
            }
            (ratchet_output / f"{name}-ratchet-provisional.json").write_text(json.dumps(receipt))

    monkeypatch.setattr(web_performance, "_run_measurements", fake_measure)
    output = tmp_path / "unqualified-after-observation"
    budgets = tmp_path / "baselines"
    budgets.mkdir()
    write_baselines(budgets)
    with pytest.raises(RuntimeError, match="not_quiet:after:interference"):
        asyncio.run(
            web_performance.run(
                tmp_path,
                budgets,
                output,
                apps_measurement=tmp_path / "fabricated-apps-measurement.json",
                reservation_port=ReservationPort(),
                binding_observer=BindingObserver(),
                activity_observer=ActivityObserver(),
                lock=Lock(),
                expected_binding=binding,
            )
        )

    assert not (output / "qualified-performance").exists()
    assert not list(output.glob("*-ratchet.json"))
    assert (output / ".admission-pending" / "store-ratchet-provisional.json").exists()
    incomplete = json.loads((output / "admission-incomplete.json").read_text())
    assert incomplete["status"] == "incomplete"
    assert incomplete["reason"] == "admission_or_workload_failed"
    assert incomplete["observations"][-1]["phase"] == "after"
    assert incomplete["observations"][-1]["state"] == "interference"


def test_failure_receipt_omits_arbitrary_observer_error_text(tmp_path, monkeypatch):
    binding = HostBinding("a" * 64)
    reservation = Reservation(
        "fabricated-reservation",
        "fabricated-owner",
        "exclusive-known-reference-mac",
        binding.profile_sha256,
        True,
        "2099-01-01T00:00:00+00:00",
    )
    canary = "PRIVATE-OBSERVER-CANARY"

    class ReservationPort:
        def read(self):
            return reservation

    class BindingObserver:
        def observe(self):
            return binding

    class ActivityObserver:
        def observe(self, phase):
            if phase == "through":
                raise RuntimeError(canary)
            return ActivityObservation(
                "fabricated-observer", phase, "quiet", "2026-11-02T00:00:00+00:00", "counts only"
            )

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    async def fake_measure(root, output, apps_measurement, budgets, *, ratchet_output):
        del root, output, apps_measurement, budgets
        ratchet_output.mkdir(parents=True)

    monkeypatch.setattr(web_performance, "_run_measurements", fake_measure)
    output = tmp_path / "sanitized-failure"
    budgets = tmp_path / "baselines"
    budgets.mkdir()
    write_baselines(budgets)
    with pytest.raises(RuntimeError, match=canary):
        asyncio.run(
            web_performance.run(
                tmp_path,
                budgets,
                output,
                reservation_port=ReservationPort(),
                binding_observer=BindingObserver(),
                activity_observer=ActivityObserver(),
                lock=Lock(),
                expected_binding=binding,
            )
        )

    receipt = json.loads((output / "admission-incomplete.json").read_text())
    assert receipt["reason"] == "admission_or_workload_failed"
    assert canary not in json.dumps(receipt)


def test_cancelled_measurement_io_finishes_before_lock_release(tmp_path, monkeypatch):
    binding = HostBinding("a" * 64)
    reservation = Reservation(
        "fabricated-reservation",
        "fabricated-owner",
        "exclusive-known-reference-mac",
        binding.profile_sha256,
        True,
        "2099-01-01T00:00:00+00:00",
    )
    reservation_port = SimpleNamespace(read=lambda: reservation)
    binding_observer = SimpleNamespace(observe=lambda: binding)
    activity_observer = SimpleNamespace(
        observe=lambda phase: ActivityObservation(
            "fabricated-observer", phase, "quiet", "2026-11-02T00:00:00+00:00", "counts only"
        )
    )
    output = tmp_path / "cancelled-io"
    budgets = tmp_path / "baselines"
    budgets.mkdir()
    write_baselines(budgets)
    control = _HeldMeasurementIO()
    monkeypatch.setattr(web_performance, "_run_measurements", control.measure)
    observer = ShieldCancellationObserver(2)
    observer.install(selected_profiles_process, monkeypatch)
    asyncio.run(
        control.cancel_run(
            (tmp_path, budgets, output),
            {
                "reservation_port": reservation_port,
                "binding_observer": binding_observer,
                "activity_observer": activity_observer,
                "lock": control.lock,
                "expected_binding": binding,
            },
            observer,
        )
    )

    assert control.write_finished.is_set()
    assert control.lock.exited
    assert (
        output / ".admission-pending" / "held-write-complete.txt"
    ).read_text() == "completed after cancellation"


def test_quiet_after_observation_publishes_ratchets_with_final_admission(tmp_path, monkeypatch):
    binding = HostBinding("a" * 64)
    reservation = Reservation(
        "fabricated-reservation",
        "fabricated-owner",
        "exclusive-known-reference-mac",
        binding.profile_sha256,
        True,
        "2099-01-01T00:00:00+00:00",
    )

    class ReservationPort:
        def read(self):
            return reservation

    class BindingObserver:
        def observe(self):
            return binding

    class ActivityObserver:
        def observe(self, phase):
            return ActivityObservation(
                "fabricated-observer", phase, "quiet", "2026-11-02T00:00:00+00:00", "counts only"
            )

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    async def fake_measure(root, output, apps_measurement, budgets, *, ratchet_output):
        del output, apps_measurement, budgets
        ratchet_output.mkdir(parents=True)
        for name, profile in PROFILES.items():
            receipt = {
                "status": "provisional",
                "profile": profile.profile,
                "source_sha256": inventory(root),
                "metrics": dict.fromkeys(METRICS[name], 1),
            }
            (ratchet_output / f"{name}-ratchet-provisional.json").write_text(json.dumps(receipt))

    monkeypatch.setattr(web_performance, "_run_measurements", fake_measure)
    output = tmp_path / "qualified-run"
    budgets = tmp_path / "baselines"
    budgets.mkdir()
    write_baselines(budgets)
    asyncio.run(
        web_performance.run(
            tmp_path,
            budgets,
            output,
            apps_measurement=tmp_path / "fabricated-apps-measurement.json",
            reservation_port=ReservationPort(),
            binding_observer=BindingObserver(),
            activity_observer=ActivityObserver(),
            lock=Lock(),
            expected_binding=binding,
        )
    )

    qualified = output / "qualified-performance"
    admission = json.loads((qualified / "admission.json").read_text())
    store_receipt_path = qualified / "store-ratchet.json"
    store_receipt = json.loads(store_receipt_path.read_text())
    assert store_receipt["status"] == "passed"
    assert store_receipt["admission_path"] == "admission.json"
    assert (
        store_receipt["admission_sha256"]
        == hashlib.sha256((qualified / "admission.json").read_bytes()).hexdigest()
    )
    assert store_receipt["source_sha256"] == admission["source_sha256"]
    assert len(
        [
            result
            for name in PROFILES
            for result in json.loads((qualified / f"{name}-ratchet.json").read_text())[
                "test_results"
            ]
        ]
    ) == sum(len(METRICS[name]) for name in PROFILES)
    phases = [item["phase"] for item in admission["observations"]]
    assert phases[0] == "before"
    assert phases[-1] == "after"
    assert "through" in phases
    assert set(phases) <= {"before", "through", "after"}
    assert "owner" not in admission
    assert "reservation_id" not in admission
    assert not (output / "admission-incomplete.json").exists()


def write_measurements(root, directory):
    directory.mkdir()
    for name in (*PROFILES, *EXTERNAL_PROFILES):
        profile = PROFILES[name].profile if name in PROFILES else EXTERNAL_PROFILES[name]
        value = {
            "profile": profile,
            "versions": runtime_versions(),
            "source_sha256": inventory(root),
        }
        for path, selected in {**IDENTITIES[name], **dict.fromkeys(METRICS[name], 1)}.items():
            node = value
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = selected
        if name == "apps":
            value["workload"]["list_items_per_page"] = 0
            value["http_versions"] = ["HTTP/2"]
            value.update(
                dict.fromkeys(
                    (
                        "hardware_client_sha256",
                        "immutable_bundle_sha256",
                        "deployment_binding_sha256",
                    ),
                    "a" * 64,
                )
            )
        if name == "engine":
            for field in (
                "model_bodies",
                "storage_bodies",
                "history_put",
                "history_read",
                "turnlog_entries",
                "turnlog_append",
            ):
                value["observed_boundary_calls"][field] = 100
        if name == "follower-memory":
            for key in MEMORY_ALLOCATIONS:
                value["measurements"][key].update(
                    min_bytes=1, median_bytes=1, p99_bytes=1, max_bytes=1
                )
            payloads = value["measurements"]["serialized_event_payload_bytes"]
            for key, size in (
                ("live_buffer_max_per_sample", 935),
                ("coalesced_event_per_sample", 32950),
            ):
                payloads[key].update(
                    min_bytes=size, median_bytes=size, p99_bytes=size, max_bytes=size
                )
            value["measurements"]["aggregate_1000_followers"].update(
                whole_process_peak_rss_before_bytes=1
            )
        (directory / f"{name}-measurement.json").write_text(json.dumps(value))


def test_first_budget_establishment_is_distinct_from_regression_comparison(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fabricated source identity")
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    budgets = tmp_path / "budgets"
    output = tmp_path / "first-establishment"
    web_performance.establish(root, measurements, budgets, output, 1.2)
    receipt = json.loads((output / "first-baseline-establishment.json").read_text())
    assert receipt["scope"].startswith("first-measured-baseline-established")
    assert "no historical regression" in receipt["scope"]
    assert len(receipt["test_results"]) == len(PROFILES)
    assert set(baselines(budgets)) == set(PROFILES)
    with pytest.raises(ValueError, match="deliberate_review"):
        web_performance.establish(root, measurements, budgets, tmp_path / "replace", 1.2)


def test_changed_source_refuses_first_budget_before_creating_any_baseline(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fabricated source identity")
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    (root / "README.md").write_text("changed source")
    budgets = tmp_path / "budgets"
    with pytest.raises(ValueError, match="measurement_source_changed"):
        web_performance.establish(root, measurements, budgets, tmp_path / "output", 1.2)
    assert not budgets.exists()


@pytest.mark.parametrize("defect", ["unknown", "profile", "workload", "runtime", "metric"])
def test_measurement_validation_refuses_incomplete_full_workload(tmp_path, defect):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fabricated source identity")
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    value = json.loads((measurements / "client-measurement.json").read_text())
    web_performance.validate_measurement("client", value)
    name = "client"
    if defect == "unknown":
        name = "unregistered"
    elif defect == "profile":
        value["profile"] = "different"
    elif defect == "workload":
        value["followers"]["followers"] = 2
    elif defect == "runtime":
        value["versions"]["architecture"] = "other"
    else:
        value["host_open"]["p99_ms"] = True
    with pytest.raises(ValueError, match=r"reference|metric_not_numeric"):
        web_performance.validate_measurement(name, value)


@pytest.mark.parametrize("defect", ["binding", "check-as-integer", "sample-count"])
def test_apps_measurement_requires_bound_complete_actual_samples(tmp_path, defect):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fabricated source identity")
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    value = json.loads((measurements / "apps-measurement.json").read_text())
    web_performance.validate_measurement("apps", value)
    if defect == "binding":
        value["deployment_binding_sha256"] = "not-a-digest"
    elif defect == "check-as-integer":
        value["checks"]["deployment_current_and_exact"] = 1
    else:
        value["measurements"]["capability_handshake"]["sample_count"] = 1
    with pytest.raises(ValueError, match=r"apps_measurement_binding|full_reference_measurement"):
        web_performance.validate_measurement("apps", value)


def test_apps_ratchet_rejects_catalog_or_protocol_identity_changes(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fabricated source identity")
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    value = json.loads((measurements / "apps-measurement.json").read_text())
    baseline = capture(
        value,
        "fabricated-digest",
        sorted(METRICS["apps"]),
        ["versions", *IDENTITIES["apps"], *DYNAMIC_IDENTITIES["apps"]],
        1.2,
    )
    web_performance.validate_budget("apps", baseline)
    for path in DYNAMIC_IDENTITIES["apps"]:
        missing = deepcopy(baseline)
        del missing["identity"][path]
        with pytest.raises(ValueError, match=r"apps_list_cardinality|apps_observed_http"):
            web_performance.validate_budget("apps", missing)
    value["workload"]["list_items_per_page"] = 1
    with pytest.raises(ValueError, match="benchmark_workload_or_version_changed"):
        compare(value, baseline)
    value["workload"]["list_items_per_page"] = 0
    value["http_versions"] = ["HTTP/1.1"]
    with pytest.raises(ValueError, match="benchmark_workload_or_version_changed"):
        compare(value, baseline)


@pytest.mark.parametrize(
    "defect", ["missing-history", "no-model-body", "wrong-attempts", "boolean"]
)
def test_engine_measurement_requires_actual_boundary_counts(tmp_path, defect):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("fabricated source identity")
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    value = json.loads((measurements / "engine-measurement.json").read_text())
    web_performance.validate_measurement("engine", value)
    if defect == "missing-history":
        del value["observed_boundary_calls"]["history_read"]
    elif defect == "no-model-body":
        value["observed_boundary_calls"]["model_bodies"] = 0
    elif defect == "wrong-attempts":
        value["observed_boundary_calls"]["attempt_start"] = 1
    else:
        value["observed_boundary_calls"]["history_read"] = True
    with pytest.raises(ValueError, match=r"engine_boundary_counts|full_reference_measurement"):
        web_performance.validate_measurement("engine", value)


@pytest.mark.parametrize(
    "defect", ["small-sample", "missing-maximum", "tiny-live-payload", "small-coalesced", "rss"]
)
def test_memory_measurement_requires_full_trials_payloads_and_process_envelope(tmp_path, defect):
    root = tmp_path / "repo"
    root.mkdir()
    measurements = tmp_path / "observations"
    write_measurements(root, measurements)
    value = json.loads((measurements / "follower-memory-measurement.json").read_text())
    web_performance.validate_measurement("follower-memory", value)
    data = value["measurements"]
    if defect == "small-sample":
        data[MEMORY_ALLOCATIONS[0]]["sample_count"] = 1
    elif defect == "missing-maximum":
        del data[MEMORY_ALLOCATIONS[1]]["max_bytes"]
    elif defect == "tiny-live-payload":
        data["serialized_event_payload_bytes"]["live_buffer_max_per_sample"]["min_bytes"] = 1
    elif defect == "small-coalesced":
        data["serialized_event_payload_bytes"]["coalesced_event_per_sample"]["min_bytes"] = 760
    else:
        data["aggregate_1000_followers"]["whole_process_peak_rss_before_bytes"] = 2
    with pytest.raises(ValueError, match=r"full_reference_measurement|missing_measurement|memory_"):
        web_performance.validate_measurement("follower-memory", value)


@pytest.mark.parametrize(
    "case", ["smoke", "no-version", "no-metric", "wrong-profile", "other-runtime", "one-metric"]
)
def test_incomplete_profile_cannot_launch_full_reference_work(tmp_path, case):
    write_baselines(tmp_path)
    path = tmp_path / "client-baseline.json"
    value = json.loads(path.read_text())
    if case == "smoke":
        value["identity"]["followers.followers"] = 2
    elif case == "no-version":
        del value["identity"]["versions"]
    elif case == "no-metric":
        value["metrics"] = {}
    elif case == "wrong-profile":
        value["profile"] = "another-runtime-profile"
    elif case == "one-metric":
        value["metrics"] = {"host_open.p99_ms": {"observed": 1, "maximum": 1.2}}
    else:
        value["identity"]["versions"]["architecture"] = "another-reference-machine"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="required"):
        baselines(tmp_path)
