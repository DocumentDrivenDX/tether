"""Public-port engine interval benchmark with explicit exclusive-wall accounting."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from benchmark_engine_intervals import TimedInterval, exclusive_engine_duration_ns
from benchmark_measurements import distribution, runtime_import_proof, runtime_versions
from selected_profiles_process import await_retained
from web_artifacts import inventory

from tether.client import (
    Actor,
    AgentSpec,
    AttemptIdentity,
    AttemptRecorder,
    ContextConfig,
    Entry,
    ExecutionLimits,
    HistoryRef,
    JsonValue,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    NewEntry,
    ResponseReceipt,
    SessionInit,
    SessionState,
    StreamItem,
    TokenEstimate,
    TokenUsage,
    TurnResult,
    WorkerLifetime,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model
from tether.engine import Engine
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.ports import HistoryProvider, ModelPort
from tether.stores.jsonl.store import JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types.history import ExchangeBinding, ExchangeRefs, HistoryRecord, HistoryValue
from tether.types.storage import SessionTurnLog, StorageLimits

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_REQUIRED_PORT_CALLS = ("model_stream", "attempt_reserve", "attempt_start", "attempt_observe")


@dataclass
class Timings:
    model: list[TimedInterval] = field(default_factory=list)
    callbacks: list[TimedInterval] = field(default_factory=list)
    storage: list[TimedInterval] = field(default_factory=list)
    model_delay: list[TimedInterval] = field(default_factory=list)
    gate_delay: list[TimedInterval] = field(default_factory=list)
    storage_delay: list[TimedInterval] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)


def _call(timings: Timings, name: str) -> None:
    timings.calls[name] = timings.calls.get(name, 0) + 1


@dataclass(frozen=True)
class DelayControls:
    model_seconds: float = 0
    recorder_start_seconds: float = 0
    append_seconds: float = 0


def _interval(target: list[TimedInterval], started: int) -> None:
    target.append(TimedInterval(started, time.perf_counter_ns()))


class _Recorder:
    def __init__(self, wrapped: AttemptRecorder, timings: Timings, delays: DelayControls) -> None:
        self._wrapped = wrapped
        self._timings = timings
        self._delays = delays

    async def reserve(self) -> AttemptIdentity:
        _call(self._timings, "attempt_reserve")
        started = time.perf_counter_ns()
        try:
            return await self._wrapped.reserve()
        finally:
            _interval(self._timings.callbacks, started)

    async def start(
        self,
        identity: AttemptIdentity,
        *,
        request_digest: str,
        wire_request: JsonValue,
        codec: str = "model-port-v1",
    ) -> None:
        _call(self._timings, "attempt_start")
        started = time.perf_counter_ns()
        try:
            if self._delays.recorder_start_seconds:
                delay_started = time.perf_counter_ns()
                await asyncio.sleep(self._delays.recorder_start_seconds)
                _interval(self._timings.gate_delay, delay_started)
            await self._wrapped.start(
                identity,
                request_digest=request_digest,
                wire_request=wire_request,
                codec=codec,
            )
        finally:
            _interval(self._timings.callbacks, started)

    async def observe(
        self,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        *,
        wire_response: JsonValue | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        _call(self._timings, "attempt_observe")
        started = time.perf_counter_ns()
        try:
            await self._wrapped.observe(
                identity,
                receipt,
                wire_response=wire_response,
                usage=usage,
            )
        finally:
            _interval(self._timings.callbacks, started)


class _Model:
    provider = "scripted-benchmark"

    def __init__(self, wrapped: ModelPort, timings: Timings, delays: DelayControls) -> None:
        self._wrapped = wrapped
        self._timings = timings
        self._delays = delays

    def stream(self, request: ModelRequest) -> AsyncIterator[StreamItem]:
        _call(self._timings, "model_stream")
        attempts = request.attempts
        selected = (
            request.model_copy(
                update={"attempts": _Recorder(attempts, self._timings, self._delays)}
            )
            if attempts is not None
            else request
        )
        started = time.perf_counter_ns()
        try:
            source = self._wrapped.stream(selected)
        finally:
            _interval(self._timings.model, started)

        async def measured() -> AsyncIterator[StreamItem]:
            try:
                while True:
                    started = time.perf_counter_ns()
                    try:
                        if self._delays.model_seconds:
                            delay_started = time.perf_counter_ns()
                            await asyncio.sleep(self._delays.model_seconds)
                            _interval(self._timings.model_delay, delay_started)
                        item = await anext(source)
                    except StopAsyncIteration:
                        return
                    finally:
                        _interval(self._timings.model, started)
                    yield item
            finally:
                close = getattr(source, "aclose", None)
                if close is not None:
                    started = time.perf_counter_ns()
                    try:
                        await close()
                    finally:
                        _interval(self._timings.model, started)

        return measured()


class _History:
    def __init__(self, wrapped: HistoryProvider, timings: Timings) -> None:
        self._wrapped = wrapped
        self._timings = timings
        self.id = wrapped.id
        self.capabilities = wrapped.capabilities

    async def put(
        self, *, actor: Actor, session_id: str, key: str, record: HistoryRecord
    ) -> HistoryRef:
        _call(self._timings, "history_put")
        return await _measured(
            self._timings.storage,
            self._wrapped.put(actor=actor, session_id=session_id, key=key, record=record),
        )

    async def read(self, refs: Sequence[HistoryRef], *, actor: Actor) -> tuple[HistoryValue, ...]:
        _call(self._timings, "history_read")
        return await _measured(self._timings.storage, self._wrapped.read(refs, actor=actor))

    async def bind_exchange(self, *, actor: Actor, binding: ExchangeBinding) -> ExchangeRefs:
        _call(self._timings, "history_bind_exchange")
        return await _measured(
            self._timings.storage, self._wrapped.bind_exchange(actor=actor, binding=binding)
        )


T = TypeVar("T")


async def _measured(target: list[TimedInterval], operation: Awaitable[T]) -> T:
    started = time.perf_counter_ns()
    try:
        return await operation
    finally:
        _interval(target, started)


class _TurnLog:
    def __init__(self, wrapped: SessionTurnLog, timings: Timings, delays: DelayControls) -> None:
        self._wrapped = wrapped
        self._timings = timings
        self._delays = delays

    @property
    def turn_id(self) -> str | None:
        return self._wrapped.turn_id

    @property
    def workers(self) -> WorkerLifetime:
        return self._wrapped.workers

    @property
    def session(self) -> SessionState:
        return self._wrapped.session

    @property
    def entries(self) -> Sequence[Entry]:
        _call(self._timings, "turnlog_entries")
        started = time.perf_counter_ns()
        try:
            return self._wrapped.entries
        finally:
            _interval(self._timings.storage, started)

    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        _call(self._timings, "turnlog_append")

        async def operation() -> tuple[Entry, ...]:
            if self._delays.append_seconds:
                delay_started = time.perf_counter_ns()
                await asyncio.sleep(self._delays.append_seconds)
                _interval(self._timings.storage_delay, delay_started)
            return await self._wrapped.append(entries)

        return await _measured(self._timings.storage, operation())

    async def heartbeat(self) -> None:
        _call(self._timings, "turnlog_heartbeat")
        await _measured(self._timings.storage, self._wrapped.heartbeat())


class _Estimator:
    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        del request
        return TokenEstimate(
            tokens=0,
            method="benchmark-approximate",
            revision="1",
            quality="approximate",
        )


def _configuration() -> tuple[ContextConfig, _Estimator]:
    context = fabricated_model_context()
    return context["context_config"], _Estimator()


def _runtime_import_proof() -> dict[str, str]:
    return runtime_import_proof(
        PROJECT_ROOT,
        {
            "tether": "src/tether/__init__.py",
            "tether.engine": "src/tether/engine/__init__.py",
            "benchmark_engine": "scripts/benchmark_engine.py",
            "benchmark_engine_intervals": "scripts/benchmark_engine_intervals.py",
            "benchmark_measurements": "scripts/benchmark_measurements.py",
            "selected_profiles_process": "scripts/selected_profiles_process.py",
        },
    )


async def _one_step(
    delays: DelayControls | None = None,
) -> tuple[TimedInterval, Timings, TurnResult]:
    delays = delays or DelayControls()
    timings = Timings()
    files = MemoryFiles(StorageLimits())
    store: JsonlSessionStore | None = None
    engine: Engine | None = None
    primary_error: BaseException | None = None
    try:
        store = await JsonlSessionStore.open(files)
        raw_history = await JsonlHistory.open(files)
        history = _History(raw_history, timings)
        actor = Actor(id="fabricated-engine-benchmark")
        spec = AgentSpec(
            id="benchmark",
            version="1",
            title="Fabricated engine benchmark",
            instructions="Return the fixed benchmark response.",
            model="demo",
            max_steps=2,
        )
        summary = await store.create(actor.id, SessionInit(spec=spec.id))
        context_config, _estimator = _configuration()
        engine = Engine(
            model=_Model(
                scripted_model([ModelResponse(content="fixed", finish_reason="stop")]),
                timings,
                delays,
            ),
            history=MessageHistoryAdapter(history),
            history_provider=history,
            model_limits=ModelExecutionLimits(
                max_response_bytes=4096,
                max_tool_argument_bytes=1024,
                max_attempts=1,
                request_timeout_seconds=10,
                max_retry_delay_seconds=0.01,
            ),
            policy=demo_policy(),
            limits=ExecutionLimits(max_workers=1, max_worker_operations=2),
            specs=(spec,),
            context_config=context_config,
            estimator=_Estimator(),
        )
        async with store.turn(summary.id, actor.id, turn_id="benchmark-turn") as raw_log:
            started = time.perf_counter_ns()
            result = await engine.run_turn(
                actor, _TurnLog(raw_log, timings, delays), "fabricated request", spec=spec
            )
            ended = time.perf_counter_ns()
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup = asyncio.create_task(_close_resources(engine, store, files))
        try:
            await await_retained(cleanup)
        except BaseException:
            if primary_error is None:
                raise
            primary_error.add_note("benchmark_engine_cleanup_failed")
    return TimedInterval(started, ended), timings, result


async def _close_resources(
    engine: Engine | None,
    store: JsonlSessionStore | None,
    files: MemoryFiles,
) -> None:
    failures: list[BaseException] = []
    if engine is not None:
        try:
            if not await engine.aclose():
                async with asyncio.timeout(5):
                    await asyncio.gather(engine.wait_closed(), engine.wait_observers_closed())
        except BaseException as error:
            failures.append(error)
    try:
        if store is None:
            closed = await files.aclose()
        else:
            closed = await store.aclose()
        if not closed:
            raise RuntimeError("benchmark_engine_storage_close_incomplete")
    except BaseException as error:
        failures.append(error)
    if failures:
        raise failures[0]


async def benchmark_engine(samples: int = 100) -> dict[str, object]:
    if type(samples) is not int or samples < 100:
        raise ValueError("benchmark_samples_invalid")
    runtime_imports = _runtime_import_proof()
    source_before = await asyncio.to_thread(inventory, PROJECT_ROOT)
    elapsed: list[float] = []
    exclusive: list[float] = []
    counts = {
        "model_bodies": 0,
        "attempt_callbacks": 0,
        "storage_bodies": 0,
        "model_stream": 0,
        "attempt_reserve": 0,
        "attempt_start": 0,
        "attempt_observe": 0,
        "history_put": 0,
        "history_read": 0,
        "history_bind_exchange": 0,
        "turnlog_entries": 0,
        "turnlog_append": 0,
        "turnlog_heartbeat": 0,
    }
    for _ in range(samples):
        step, timings, result = await _one_step()
        if (
            result.stop_reason != "final"
            or not result.messages
            or result.messages[-1].content != "fixed"
        ):
            raise RuntimeError("benchmark_engine_turn_failed")
        if any(timings.calls.get(name) != 1 for name in _REQUIRED_PORT_CALLS):
            raise RuntimeError("benchmark_engine_required_port_call_count_mismatch")
        elapsed.append(step.end_ns - step.start_ns)
        elapsed[-1] /= 1_000_000
        exclusive_ns = exclusive_engine_duration_ns(
            step,
            timings.model,
            timings.callbacks,
            timings.storage,
        )
        exclusive.append(exclusive_ns / 1_000_000)
        counts["model_bodies"] += len(timings.model)
        counts["attempt_callbacks"] += len(timings.callbacks)
        counts["storage_bodies"] += len(timings.storage)
        for name, count in timings.calls.items():
            counts[name] += count
    source_after = await asyncio.to_thread(inventory, PROJECT_ROOT)
    if source_before != source_after:
        raise RuntimeError("benchmark_engine_source_changed_during_observation")
    return {
        "profile": "public-engine-exclusive-wall",
        "versions": runtime_versions(),
        "runtime_imports": runtime_imports,
        "source_sha256": source_before,
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "workload": {
            "samples": samples,
            "model_calls_per_step": 1,
            "tools": 0,
            "context_policy": "full",
            "history": "fresh",
        },
        "measurements": {
            "total_step_wall": distribution(elapsed),
            "exclusive_wall_upper_bound": distribution(exclusive),
        },
        "observed_boundary_calls": counts,
        "limits": [
            "Exclusive wall is a public-port interval bound, not module CPU time.",
            "Scripted model and MemoryFiles storage do not represent remote latency.",
            "Attempt-recorder callbacks are separated; nested storage/history bodies are excluded.",
            "Untimed session/workers/turn_id getters and wrapper bookkeeping remain included.",
            "Setup, close and deferred observer drain are outside run_turn; "
            "no background CPU claim.",
        ],
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Measure public one-step engine overhead.")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = await benchmark_engine(arguments.samples)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    with arguments.output.open("x", encoding="utf-8") as output:
        output.write(payload)


if __name__ == "__main__":
    asyncio.run(_main())
