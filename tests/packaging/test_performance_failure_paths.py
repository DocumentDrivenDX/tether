"""Reference performance runner failure classification, workload execution and publication."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import web_performance as wp
from benchmark_profiles import METRICS, PROFILES
from performance_admission import ActivityObservation, HostBinding, Reservation
from process import Result
from web_artifacts import inventory


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (asyncio.CancelledError(), "cancelled"),
        (TimeoutError("x"), "deadline_exhausted"),
        (FileNotFoundError("x"), "filesystem_or_process_io_failed"),
        (ValueError("x"), "measurement_or_source_validation_failed"),
        (RuntimeError("x"), "admission_or_workload_failed"),
        (KeyError("x"), "unexpected_failure"),
    ],
)
def test_failure_reasons_are_closed_and_ordered(error: BaseException, reason: str) -> None:
    assert wp._failure_reason(error) == reason


def _sources(root: Path) -> Any:
    return inventory(root)


def _provisional(root: Path, name: str, **change: Any) -> dict[str, Any]:
    receipt = {
        "status": "provisional",
        "profile": PROFILES[name].profile,
        "source_sha256": inventory(root),
        "metrics": dict.fromkeys(METRICS[name], 1),
    }
    return receipt | change


def _write_provisional(directory: Path, root: Path, only: set[str] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in PROFILES:
        if only is None or name in only:
            path = directory / f"{name}-ratchet-provisional.json"
            path.write_text(json.dumps(_provisional(root, name)))


def test_finalize_ratchets_promotes_every_profile_and_removes_provisional(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    pending = tmp_path / "pending"
    _write_provisional(pending, root)
    wp._finalize_ratchets(pending, "a" * 64, _sources(root))
    assert not list(pending.glob("*provisional*"))
    receipt = json.loads((pending / "store-ratchet.json").read_text())
    assert receipt["status"] == "passed"
    assert receipt["admission_sha256"] == "a" * 64
    assert receipt["baseline_path"] == "store-baseline.json"
    assert len(receipt["test_results"]) == len(METRICS["store"])


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"status": "passed"}, "complete_provisional_reference_metrics_required:store"),
        ({"profile": "other"}, "complete_provisional_reference_metrics_required:store"),
        ({"metrics": {"only": 1}}, "complete_provisional_reference_metrics_required:store"),
        ({"metrics": []}, "complete_provisional_reference_metrics_required:store"),
        ({"source_sha256": {"changed": "x"}}, "complete_provisional_reference_metrics_required"),
    ],
)
def test_finalize_ratchets_refuses_incomplete_or_foreign_receipts(
    tmp_path: Path, mutation: dict[str, Any], reason: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    pending = tmp_path / "pending"
    _write_provisional(pending, root)
    path = pending / "store-ratchet-provisional.json"
    path.write_text(json.dumps(_provisional(root, "store", **mutation)))
    with pytest.raises(ValueError, match=reason):
        wp._finalize_ratchets(pending, "a" * 64, _sources(root))


def test_finalize_ratchets_refuses_unknown_and_missing_profiles(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    unknown = tmp_path / "unknown"
    _write_provisional(unknown, root)
    (unknown / "bogus-ratchet-provisional.json").write_text("{}")
    with pytest.raises(ValueError, match="unexpected_provisional_reference_profile"):
        wp._finalize_ratchets(unknown, "a" * 64, _sources(root))
    partial = tmp_path / "partial"
    _write_provisional(partial, root, only={"store"})
    with pytest.raises(ValueError, match="all_controlled_reference_profiles_required"):
        wp._finalize_ratchets(partial, "a" * 64, _sources(root))


def test_baseline_hashes_cover_exactly_the_reference_profiles(tmp_path: Path) -> None:
    for name in PROFILES:
        (tmp_path / f"{name}-baseline.json").write_text(name)
    hashes = wp._baseline_hashes(tmp_path)
    assert set(hashes) == {f"{name}-baseline.json" for name in PROFILES}
    (tmp_path / "store-baseline.json").write_text("changed")
    assert wp._baseline_hashes(tmp_path) != hashes


def _stub_measurement_boundaries(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list[Any]:
    calls: list[Any] = []

    async def fake_command(args: list[str], **kwargs: Any) -> Result:
        calls.append((args, kwargs))
        output = Path(args[args.index("--output") + 1])
        await asyncio.to_thread(output.write_text, json.dumps({"profile": "fabricated"}))
        return Result(returncode, "", "")

    monkeypatch.setattr(wp, "run_command", fake_command)
    monkeypatch.setattr(wp, "validate_measurement", lambda _name, _observed: None)
    monkeypatch.setattr(wp, "independent_result", lambda _payload, _budget: {"compared": True})
    monkeypatch.setattr(
        wp, "qualify", lambda _observed, _compared, _root: {"status": "passed", "test_results": [1]}
    )
    return calls


def test_run_measurements_invokes_each_workload_and_records_provisional_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _stub_measurement_boundaries(monkeypatch)
    pending = tmp_path / "pending"
    (tmp_path / "out").mkdir()
    budgets: Any = {name: {} for name in PROFILES}
    asyncio.run(
        wp._run_measurements(tmp_path, tmp_path / "out", None, budgets, ratchet_output=pending)
    )
    assert len(calls) == len(PROFILES)
    first_args, first_kwargs = calls[0]
    assert first_args[0] == sys.executable
    assert first_kwargs["cwd"] == tmp_path
    assert first_kwargs["capture"] is False
    stored = json.loads((pending / "store-ratchet-provisional.json").read_text())
    assert stored["status"] == "provisional"
    assert "test_results" not in stored
    assert capsys.readouterr().out.count("admission is pending") == len(PROFILES)


def test_run_measurements_refuses_failed_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_measurement_boundaries(monkeypatch, returncode=2)
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="reference_workload_failed:store"):
        asyncio.run(
            wp._run_measurements(
                tmp_path, tmp_path / "out", None, {}, ratchet_output=tmp_path / "pending"
            )
        )


class _Ports:
    binding = HostBinding("b" * 64)

    def read(self) -> Reservation:
        return Reservation(
            "fabricated-reservation",
            "fabricated-owner",
            "exclusive-known-reference-mac",
            self.binding.profile_sha256,
            True,
            "2099-01-01T00:00:00+00:00",
        )

    def observe(self, phase: Any) -> ActivityObservation:
        return ActivityObservation(
            "fabricated-observer", phase, "quiet", "2026-11-02T00:00:00+00:00", "counts"
        )

    def __enter__(self) -> _Ports:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class _Binding:
    def observe(self) -> HostBinding:
        return _Ports.binding


def _run_admitted(root: Path, output: Path, budgets: Path) -> None:
    ports: Any = _Ports()
    asyncio.run(
        wp.run(
            root,
            budgets,
            output,
            reservation_port=ports,
            binding_observer=_Binding(),
            activity_observer=ports,
            lock=ports,
            expected_binding=ports.binding,
        )
    )


@pytest.mark.parametrize(
    ("failure", "reason", "message"),
    [
        (ValueError("x"), "measurement_or_source_validation_failed", "x"),
        (OSError("x"), "filesystem_or_process_io_failed", "x"),
    ],
)
def test_failed_admitted_measurement_records_incomplete_reason_and_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception, reason: str, message: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    budgets = tmp_path / "budgets"
    budgets.mkdir()
    monkeypatch.setattr(wp, "baselines", lambda _directory: (_ for _ in ()).throw(failure))
    output = tmp_path / "run"
    with pytest.raises(type(failure), match=message):
        _run_admitted(root, output, budgets)
    incomplete = json.loads((output / "admission-incomplete.json").read_text())
    assert incomplete["status"] == "incomplete"
    assert incomplete["reason"] == reason
    assert not (output / "qualified-performance").exists()


def test_source_change_during_measurement_refuses_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("original")
    budgets = tmp_path / "budgets"
    budgets.mkdir()
    for name in PROFILES:
        (budgets / f"{name}-baseline.json").write_text(name)
    monkeypatch.setattr(wp, "baselines", lambda _directory: {})

    async def mutate(*_args: object, **_kwargs: object) -> None:
        (root / "README.md").write_text("changed while measuring")

    monkeypatch.setattr(wp, "_run_measurements", mutate)
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="source_changed_during_reference_measurement"):
        _run_admitted(root, output, budgets)
    assert not (output / "qualified-performance").exists()
    recorded = (output / "admission-observations.jsonl").read_text().splitlines()
    assert recorded
    reason = json.loads((output / "admission-incomplete.json").read_text())["reason"]
    assert reason == "measurement_or_source_validation_failed"


def test_baseline_change_during_measurement_refuses_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    budgets = tmp_path / "budgets"
    budgets.mkdir()
    for name in PROFILES:
        (budgets / f"{name}-baseline.json").write_text(name)
    monkeypatch.setattr(wp, "baselines", lambda _directory: {})

    async def mutate(*_args: object, **_kwargs: object) -> None:
        (budgets / "store-baseline.json").write_text("loosened")

    monkeypatch.setattr(wp, "_run_measurements", mutate)
    with pytest.raises(ValueError, match="baselines_changed_during_reference_measurement"):
        _run_admitted(root, tmp_path / "run", budgets)


def test_main_dispatches_establish_and_run_with_parsed_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(wp, "establish", lambda *args: seen.setdefault("establish", args))

    async def fake_run(*args: Any, **kwargs: Any) -> None:
        seen["run"] = (args, kwargs)

    monkeypatch.setattr(wp, "run", fake_run)
    monkeypatch.setattr(wp, "SystemBindingObserver", lambda: "binding")
    monkeypatch.setattr(wp, "ProcessTableObserver", lambda: "activity")
    monkeypatch.setattr(
        wp, "HostBinding", SimpleNamespace(from_reference_file=lambda path: ("expected", path))
    )
    monkeypatch.setattr(sys, "argv", [
        "web_performance", "--baselines", str(tmp_path / "b"), "--output", str(tmp_path / "o"),
        "--establish-from", str(tmp_path / "m"), "--tolerance", "1.5",
    ])  # fmt: skip
    wp.main()
    assert seen["establish"][1:] == (tmp_path / "m", tmp_path / "b", tmp_path / "o", 1.5)
    assert "run" not in seen

    monkeypatch.setattr(sys, "argv", [
        "web_performance", "--baselines", str(tmp_path / "b"), "--output", str(tmp_path / "o"),
        "--machine-profile", str(tmp_path / "profile.json"),
    ])  # fmt: skip
    wp.main()
    args, kwargs = seen["run"]
    assert args[1:] == (tmp_path / "b", tmp_path / "o")
    assert kwargs["reservation_port"] is None
    assert kwargs["lock"] is None
    assert kwargs["expected_binding"] == ("expected", tmp_path / "profile.json")
    assert (kwargs["binding_observer"], kwargs["activity_observer"]) == ("binding", "activity")


def test_establish_refuses_missing_sources_stale_measurements_and_existing_baselines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    measurements = tmp_path / "measurements"
    measurements.mkdir()
    with pytest.raises(ValueError, match="first_baseline_source_identity_required"):
        wp.establish(root, measurements, tmp_path / "b", tmp_path / "o", 1.2)
    (root / "README.md").write_text("fabricated")
    for name in PROFILES:
        (measurements / f"{name}-measurement.json").write_text(
            json.dumps({"source_sha256": {"stale": "x"}})
        )
    with pytest.raises(ValueError, match="first_baseline_measurement_source_changed:store"):
        wp.establish(root, measurements, tmp_path / "b", tmp_path / "o", 1.2)
    sources = inventory(root)
    for name in PROFILES:
        (measurements / f"{name}-measurement.json").write_text(
            json.dumps({"source_sha256": sources})
        )
    monkeypatch.setattr(wp, "validate_measurement", lambda _name, _observed: None)
    monkeypatch.setattr(wp, "capture", lambda *_args: {"budget": True})
    monkeypatch.setattr(wp, "validate_budget", lambda _name, _budget: None)
    directory = tmp_path / "b"
    directory.mkdir()
    (directory / "store-baseline.json").write_text("existing")
    with pytest.raises(ValueError, match="existing_measured_baseline_requires_deliberate_review"):
        wp.establish(root, measurements, directory, tmp_path / "o", 1.2)
    assert (directory / "store-baseline.json").read_text() == "existing"
    (directory / "store-baseline.json").unlink()
    wp.establish(root, measurements, directory, tmp_path / "o", 1.2)
    receipt = json.loads((tmp_path / "o" / "first-baseline-establishment.json").read_text())
    assert receipt["status"] == "passed"
    assert len(receipt["test_results"]) == len(PROFILES)
    assert {path.name for path in directory.glob("*-baseline.json")} == {
        f"{name}-baseline.json" for name in PROFILES
    }
