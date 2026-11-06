"""Bounded public-client observations, with explicit limits on latency inference."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Literal

from benchmark_client_support import (
    api_worker,
    child_startup,
    first_output,
    follower_worker,
    peak_rss,
    separate_process_follower,
)
from benchmark_reconnect import benchmark_reconnect
from benchmark_streaming import streaming_followers
from web_artifacts import inventory as source_inventory

from tether.client import (
    Actor,
    AgentSpec,
    Change,
    ClientHost,
    ClientLimits,
    ClientProtocol,
    ClientSettings,
    EntriesAppended,
    LocalJsonlSettings,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    SessionUpdated,
    TokenEstimate,
    VolatileSettings,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def benchmark_environment() -> dict[str, str]:
    """Explicit local child composition, independent of ambient credentials."""
    return {
        "PATH": ":".join(
            (str(Path(sys.executable).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin")
        ),
        "HOME": str(Path.home()),
        "TMPDIR": gettempdir(),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


class _BenchmarkApproximateEstimator:
    """Non-recording approximate estimates for the bounded fabricated reference workload."""

    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        del request
        return TokenEstimate(
            tokens=0,
            method="benchmark-approximate",
            revision="1",
            quality="approximate",
        )


def distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "median_ms": statistics.median(values),
    }


def settings(
    root: Path,
    followers: int,
    samples: int,
    *,
    storage: Literal["posix", "volatile"] = "posix",
) -> ClientSettings:
    context = fabricated_model_context()
    return ClientSettings(
        model=scripted_model(
            [ModelResponse(content="Fabricated answer", finish_reason="stop")] * samples
        ),
        storage=LocalJsonlSettings(root) if storage == "posix" else VolatileSettings(),
        policy=demo_policy(),
        client_limits=ClientLimits(
            max_page_size=100,
            max_call_ids=8,
            max_read_bytes=1048576,
            max_lookup_entries=1000,
            read_timeout_seconds=30,
            heartbeat_interval_seconds=1,
            shutdown_timeout_seconds=15,
            follower_queue_size=32,
            max_followers=followers,
            max_active_turns=8,
            max_request_bytes=65536,
        ),
        model_limits=ModelExecutionLimits(
            max_response_bytes=8192,
            max_tool_argument_bytes=512,
            max_attempts=2,
            request_timeout_seconds=10,
            max_retry_delay_seconds=0.01,
        ),
        context_config=context["context_config"],
        estimator=_BenchmarkApproximateEstimator(),
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated benchmark",
                instructions="Reply with fabricated data",
                model="demo",
            ),
        ),
        default_spec="demo",
        resume="report" if storage == "posix" else "off",
    )


async def observe(
    stream: AsyncIterator[Change],
    queue: asyncio.Queue[tuple[int, float]],
) -> None:
    try:
        async for event in stream:
            if isinstance(event, (SessionUpdated, EntriesAppended)) and event.last_in_commit:
                await queue.put((event.seq, time.perf_counter()))
    finally:
        if isinstance(stream, AsyncGenerator):
            await stream.aclose()


async def followers(
    client: ClientProtocol, sid: str, count: int, samples: int
) -> dict[str, object]:
    baseline = await client.snapshot(sid)
    queue: asyncio.Queue[tuple[int, float]] = asyncio.Queue(maxsize=count * 4)
    streams = [client.follow(session_id=sid, since=baseline.seq) for _ in range(count)]
    tasks = [asyncio.create_task(observe(stream, queue)) for stream in streams]
    lags: list[float] = []
    deliveries: list[float] = []
    sampled = asyncio.Event()

    async def watch_loop() -> None:
        while True:
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            lags.append(max(0, time.perf_counter() - before - 0.01) * 1000)
            sampled.set()

    watcher = asyncio.create_task(watch_loop())
    try:
        await asyncio.wait_for(sampled.wait(), timeout=30)
        for index in range(samples):
            started = time.perf_counter()
            summary = await client.update(sid, title=f"Fabricated {index}")
            for _ in range(count):
                seq, received = await asyncio.wait_for(queue.get(), 30)
                if seq != summary.last_seq:
                    raise RuntimeError("follower_commit_mismatch")
                deliveries.append((received - started) * 1000)
        return {
            "command_to_follower": distribution(deliveries),
            "event_loop_lag": distribution(lags),
            "followers": count,
            "queue_size": 32,
            "commits": samples,
        }
    finally:
        for task in [*tasks, watcher]:
            task.cancel()
        await asyncio.gather(*tasks, watcher, return_exceptions=True)


async def benchmark(
    count: int,
    samples: int,
    *,
    startup: bool = True,
    cross_process: bool = True,
    storage: Literal["posix", "volatile"] = "posix",
) -> dict[str, object]:
    source_before = await asyncio.to_thread(source_inventory, PROJECT_ROOT)
    with TemporaryDirectory(prefix="tether-client-benchmark-") as directory:
        config = settings(Path(directory), count, samples, storage=storage)
        timings = []
        for _ in range(samples):
            started = time.perf_counter()
            host = await ClientHost.open(config)
            timings.append((time.perf_counter() - started) * 1000)
            await host.aclose()
        async with await ClientHost.open(config) as host:
            client = host.client(Actor(id="fabricated-benchmark"))
            session = await client.create(spec="demo")
            feed = await followers(client, session.id, count, samples)
            reconnect = await followers(client, session.id, count, samples)
            sends = []
            for _ in range(samples):
                started = time.perf_counter()
                result = await (
                    await client.send(session.id, "Fabricated request", spec="demo")
                ).result()
                if result.stop_reason != "final" or not result.messages:
                    raise RuntimeError("benchmark_turn_failed")
                if result.messages[-1].content != "Fabricated answer":
                    raise RuntimeError("benchmark_turn_failed")
                sends.append((time.perf_counter() - started) * 1000)
        streaming = await streaming_followers(config, count, samples)
        cursor_reconnect = await benchmark_reconnect(config, samples=samples, storage=storage)
        rss_peak = peak_rss()
    cross_process_result = (
        await separate_process_follower(samples, environment=benchmark_environment())
        if cross_process and storage == "posix"
        else "not selected"
        if storage == "posix"
        else "not applicable: process-lifetime storage"
    )
    startup_result = (
        await child_startup(samples, environment=benchmark_environment())
        if startup and storage == "posix"
        else "not selected"
        if storage == "posix"
        else "measured once in posix runtime cohort"
    )
    if isinstance(startup_result, dict):
        with TemporaryDirectory(prefix="tether-cli-first-output-") as directory:
            startup_result["cli_operational_first_output"] = await first_output(
                Path(directory), environment=benchmark_environment(), samples=samples
            )
    source_after = await asyncio.to_thread(source_inventory, PROJECT_ROOT)
    if source_before != source_after:
        raise RuntimeError("benchmark_source_changed_during_measurement")
    return {
        "profile": (
            "local-posix-jsonl-public-client" if storage == "posix" else "volatile-public-client"
        ),
        "storage": {
            "configuration": "LocalJsonlSettings" if storage == "posix" else "VolatileSettings",
            "durability": "os" if storage == "posix" else "none",
            "resume": config.resume,
            "capacity": "unchanged composition defaults",
        },
        "child_environment_profile": "explicit-local-path/current-source/fixed-locale",
        "versions": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "architecture": platform.machine(),
        },
        "samples": samples,
        "host_open": distribution(timings),
        "followers": feed,
        "reconnect": reconnect,
        "cursor_reconnect": cursor_reconnect,
        "streaming_followers": streaming,
        "total_process_peak_rss_including_host_bytes": rss_peak,
        "scripted_turn_total": distribution(sends),
        "engine_step_total_time_upper_bound": {
            **distribution(sends),
            "scope": (
                "one scripted model step; full turn time is an upper bound and includes "
                "engine, model adapter, "
                "policy, history and storage"
            ),
        },
        "separate_process_follower": cross_process_result,
        "startup": startup_result,
        "source_sha256": source_before,
        "limits": [
            "Small fabricated session; not the 10,000-session reference dataset or Apps latency",
            "Command-to-delivery includes durable update; not isolated commit-to-delivery",
            (
                "Full scripted turn is an upper bound on one-step engine time, "
                "not isolated engine overhead"
            ),
            (
                "Peak RSS includes base host/runtime memory; it is not retained "
                "or per-follower attribution"
            ),
            (
                "Separate-process timing includes HTTP API and storage; it does not "
                "isolate Apps ingress"
            ),
            "Terminal first paint uses the actual Textual CLI under a local POSIX pseudo-terminal",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", choices=("api", "follow"))
    parser.add_argument("--followers", type=int, default=1000)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--storage", choices=("posix", "volatile"), default="posix")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--url")
    parser.add_argument("--session-id")
    parser.add_argument("--since", type=int)
    parser.add_argument("--worker-samples", type=int)
    args = parser.parse_args()
    if args.worker == "api":
        if args.root is None or args.port is None:
            parser.error("api worker requires --root and --port")
        asyncio.run(api_worker(args.root, args.port))
        return
    if args.worker == "follow":
        if (
            args.url is None
            or args.session_id is None
            or args.since is None
            or args.worker_samples is None
        ):
            parser.error("follow worker requires --url, --session-id, --since and --worker-samples")
        asyncio.run(follower_worker(args.url, args.session_id, args.since, args.worker_samples))
        return
    if args.output is None:
        parser.error("--output is required")
    if args.followers < 1 or args.samples < 100:
        parser.error("positive follower count and at least 100 samples required")
    result = asyncio.run(benchmark(args.followers, args.samples, storage=args.storage))
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
