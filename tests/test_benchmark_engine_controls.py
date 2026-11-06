from __future__ import annotations

import asyncio
from typing import cast

import benchmark_engine as benchmark
import pytest
from benchmark_engine import DelayControls, _one_step
from benchmark_engine_intervals import TimedInterval, exclusive_engine_intervals


def _measure(delays: DelayControls):
    return asyncio.run(_one_step(delays))


def _overlap(first: tuple[TimedInterval, ...], second: tuple[TimedInterval, ...]) -> int:
    return sum(
        max(0, min(left.end_ns, right.end_ns) - max(left.start_ns, right.start_ns))
        for left in first
        for right in second
    )


def _engine_spans(step: TimedInterval, timings):
    return exclusive_engine_intervals(step, timings.model, timings.callbacks, timings.storage)


def test_model_and_storage_body_delays_are_removed_but_recorder_gate_is_retained():
    measured = (
        _measure(DelayControls()),
        _measure(DelayControls(model_seconds=0.02)),
        _measure(DelayControls(append_seconds=0.02)),
        _measure(DelayControls(recorder_start_seconds=0.02)),
    )
    for _, _, result in measured:
        assert result.stop_reason == "final"
        assert result.messages[-1].content == "fixed"

    _, model_timings, _ = measured[1]
    _, store_timings, _ = measured[2]
    _, gate_timings, _ = measured[3]
    assert (
        _overlap(_engine_spans(measured[1][0], model_timings), tuple(model_timings.model_delay))
        == 0
    )
    assert (
        _overlap(_engine_spans(measured[2][0], store_timings), tuple(store_timings.storage_delay))
        == 0
    )
    gate_delay_ns = sum(interval.end_ns - interval.start_ns for interval in gate_timings.gate_delay)
    gate_overlap_ns = _overlap(
        _engine_spans(measured[3][0], gate_timings), tuple(gate_timings.gate_delay)
    )
    assert gate_overlap_ns == gate_delay_ns
    assert len(gate_timings.callbacks) >= 3
    for _, timings, _ in measured:
        assert {
            name: timings.calls.get(name, 0)
            for name in ("model_stream", "attempt_reserve", "attempt_start", "attempt_observe")
        } == {
            "model_stream": 1,
            "attempt_reserve": 1,
            "attempt_start": 1,
            "attempt_observe": 1,
        }
        assert timings.calls.get("history_put", 0) > 0
        assert timings.calls.get("history_read", 0) > 0
        assert timings.calls.get("turnlog_entries", 0) > 0
        assert timings.calls.get("turnlog_append", 0) > 0


def test_public_engine_run_forwards_model_and_attempt_ports_once() -> None:
    _, timings, result = _measure(DelayControls())
    assert result.stop_reason == "final"
    assert result.messages[-1].content == "fixed"
    assert timings.calls["model_stream"] == 1
    assert timings.calls["attempt_reserve"] == 1
    assert timings.calls["attempt_start"] == 1
    assert timings.calls["attempt_observe"] == 1
    assert timings.calls["history_put"] > 0
    assert timings.calls["history_read"] > 0
    assert timings.calls["turnlog_append"] > 0


def test_runtime_import_proof_resolves_project_modules() -> None:
    proof = benchmark._runtime_import_proof()
    assert proof["tether"] == "src/tether/__init__.py"
    assert proof["tether.engine"] == "src/tether/engine/__init__.py"
    assert proof["benchmark_engine_intervals"] == "scripts/benchmark_engine_intervals.py"
    assert proof["benchmark_measurements"] == "scripts/benchmark_measurements.py"
    assert proof["selected_profiles_process"] == "scripts/selected_profiles_process.py"


def test_one_step_setup_failure_closes_owned_files(monkeypatch) -> None:
    async def run() -> None:
        actual_memory_files = benchmark.MemoryFiles
        files_created = []

        def memory_files(*args, **kwargs):
            files = actual_memory_files(*args, **kwargs)
            files_created.append(files)
            return files

        async def fail_history_open(cls, files):
            raise ValueError("history_open_primary")

        monkeypatch.setattr(benchmark, "MemoryFiles", memory_files)
        monkeypatch.setattr(benchmark.JsonlHistory, "open", classmethod(fail_history_open))
        with pytest.raises(ValueError, match="history_open_primary"):
            await _one_step()
        assert len(files_created) == 1
        assert cast(benchmark.MemoryFiles, files_created[0])._closed

    asyncio.run(run())


def test_one_step_repeated_cancellation_still_closes_owned_storage(monkeypatch) -> None:
    async def run() -> None:
        actual_memory_files = benchmark.MemoryFiles
        files_created = []

        def memory_files(*args, **kwargs):
            files = actual_memory_files(*args, **kwargs)
            files_created.append(files)
            return files

        monkeypatch.setattr(benchmark, "MemoryFiles", memory_files)
        run_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        original_aclose = benchmark.Engine.aclose

        async def blocked_run_turn(self, *args, **kwargs):
            run_started.set()
            await asyncio.Future()

        async def delayed_aclose(self):
            cleanup_started.set()
            await finish_cleanup.wait()
            return await original_aclose(self)

        monkeypatch.setattr(benchmark.Engine, "run_turn", blocked_run_turn)
        monkeypatch.setattr(benchmark.Engine, "aclose", delayed_aclose)
        task = asyncio.create_task(_one_step())
        await run_started.wait()
        task.cancel()
        await cleanup_started.wait()
        task.cancel()
        finish_cleanup.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled_engine_step_returned")
        assert len(files_created) == 1
        assert cast(benchmark.MemoryFiles, files_created[0])._closed

    asyncio.run(run())
