"""Public follower allocation observations with causal queue/buffer fill barriers."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from benchmark_follower_memory_aggregate import _aggregate_followers
from benchmark_follower_memory_fixture import _summary
from benchmark_follower_memory_ports import (
    _COALESCED_TEXT_BYTES,
    _HELD_TURNS,
    _QUEUE_SIZE,
    _TEXT_BYTES,
)
from benchmark_follower_memory_trial import _trial
from benchmark_measurements import runtime_import_proof, runtime_versions
from web_artifacts import inventory

_SAMPLE_TRIALS = 100
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_EXPECTED_IMPORTS = {
    "benchmark_follower_memory": "scripts/benchmark_follower_memory.py",
    "benchmark_follower_memory_fixture": "scripts/benchmark_follower_memory_fixture.py",
    "benchmark_follower_memory_cleanup": "scripts/benchmark_follower_memory_cleanup.py",
    "benchmark_follower_memory_ports": "scripts/benchmark_follower_memory_ports.py",
    "benchmark_follower_memory_aggregate": "scripts/benchmark_follower_memory_aggregate.py",
    "benchmark_follower_memory_trial": "scripts/benchmark_follower_memory_trial.py",
    "benchmark_measurements": "scripts/benchmark_measurements.py",
    "selected_profiles_process": "scripts/selected_profiles_process.py",
    "web_artifacts": "scripts/web_artifacts.py",
    "tether.client": "src/tether/client/__init__.py",
    "tether.client.testing": "src/tether/client/testing.py",
    "tether.history.jsonl": "src/tether/history/jsonl.py",
    "tether.stores.jsonl.store": "src/tether/stores/jsonl/store.py",
    "tether.stores.memory": "src/tether/stores/memory.py",
}


async def benchmark_follower_memory(
    samples: int = _SAMPLE_TRIALS, *, followers: int = 1000, queue_size: int = _QUEUE_SIZE
) -> dict[str, object]:
    if type(samples) is not int or samples != _SAMPLE_TRIALS:
        raise ValueError("benchmark_memory_sample_count_invalid")
    if type(followers) is not int or followers != 1000:
        raise ValueError("benchmark_memory_follower_count_invalid")
    if type(queue_size) is not int or queue_size != _QUEUE_SIZE:
        raise ValueError("benchmark_memory_queue_size_invalid")
    runtime_imports = runtime_import_proof(PROJECT_ROOT, _EXPECTED_IMPORTS)
    source_before = await asyncio.to_thread(inventory, PROJECT_ROOT)
    trials = [await _trial() for _ in range(samples)]
    aggregate = await _aggregate_followers(followers)
    empty_active = [trial["persisted"]["empty_follower_current_bytes"] for trial in trials]
    persisted = [trial["persisted"]["python_growth_bytes"] for trial in trials]
    live = [trial["live"]["python_growth_bytes"] for trial in trials]
    coalesced = [trial["coalesced"]["python_growth_bytes"] for trial in trials]
    live_event_bytes = [trial["live"]["live_event_max_serialized_bytes"] for trial in trials]
    live_event_text_bytes = [trial["live"]["live_event_max_text_bytes"] for trial in trials]
    coalesced_text_bytes = [trial["coalesced"]["coalesced_text_bytes"] for trial in trials]
    coalesced_event_bytes = [
        trial["coalesced"]["coalesced_event_serialized_bytes"] for trial in trials
    ]
    source_after = await asyncio.to_thread(inventory, PROJECT_ROOT)
    if source_before != source_after:
        raise RuntimeError("benchmark_source_changed_during_observation")
    return {
        "profile": "public-follower-retained-python",
        "versions": runtime_versions(),
        "runtime_imports": runtime_imports,
        "source_sha256": source_before,
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "workload": {
            "single_follower_trials": samples,
            "aggregate_followers": followers,
            "queue_size": queue_size,
            "persisted_queue_capacity": _QUEUE_SIZE,
            "live_buffer_capacity": _QUEUE_SIZE,
            "held_turns": _HELD_TURNS,
            "max_active_turns": _HELD_TURNS,
            "overflow_active_turns": _HELD_TURNS,
            "event_text_bytes": _TEXT_BYTES,
            "coalesced_total_model_text_bytes": _COALESCED_TEXT_BYTES,
            "coalesced_buffered_text_bytes": _COALESCED_TEXT_BYTES - 1,
            "coalesced_response_limit_bytes": 65_536,
            "max_read_bytes": 1_048_576,
        },
        "measurements": {
            "single_follower_empty_active_persisted_queue_current_python_bytes": _summary(
                empty_active
            ),
            "single_follower_full_persisted_queue_python_growth_bytes": _summary(persisted),
            "single_follower_full_live_buffer_python_growth_bytes": _summary(live),
            "single_follower_coalesced_live_event_python_growth_bytes": _summary(coalesced),
            "serialized_event_payload_bytes": {
                "live_buffer_max_per_sample": _summary(live_event_bytes),
                "coalesced_event_per_sample": _summary(coalesced_event_bytes),
            },
            "event_text_bytes": {
                "live_buffer_max_per_sample": _summary(live_event_text_bytes),
                "coalesced_buffered_per_sample": _summary(coalesced_text_bytes),
            },
            "aggregate_1000_followers": aggregate,
            "causal_occupancy": {
                "persisted_queue": {
                    "capacity": _QUEUE_SIZE,
                    "occupancy": _QUEUE_SIZE,
                    "changes_enqueued_after_one_consumed": _QUEUE_SIZE,
                    "journal_commits_preseeded_before_baseline": _QUEUE_SIZE + 3,
                    "overflow_probe_pending_before_snapshot": True,
                },
                "live_buffer": {"capacity": _QUEUE_SIZE, "occupancy": _QUEUE_SIZE},
                "coalesced_live": {
                    "events": 1,
                    "text_bytes": _COALESCED_TEXT_BYTES - 1,
                    "total_model_text_bytes": _COALESCED_TEXT_BYTES,
                    "later_delta_chunks": 32,
                    "distinct_attempts": 1,
                },
            },
        },
        "limits": [
            "Retained measurements are Python tracemalloc current-allocation growth, "
            "not native/RSS memory.",
            "Full-state growth includes fixture, producer/model, and observation "
            "allocations retained during each trial; it is observed whole-trial "
            "net growth, not isolated per-follower allocation or a formal upper bound.",
            "Negative net deltas refuse the nonnegative baseline profile; they are not clamped.",
            "The event profile is fabricated and does not guarantee max_read_bytes "
            "worst-case retention.",
            "The 1000-follower process envelope is measured separately, never "
            "extrapolated from one follower.",
            "The queue capacity 32 is explicit benchmark configuration, not a library default.",
            "Single-follower trials precede the aggregate observation; "
            "process peak RSS includes prior trials.",
        ],
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Measure retained follower allocation profiles.")
    parser.add_argument("--samples", type=int, default=_SAMPLE_TRIALS)
    parser.add_argument("--followers", type=int, default=1000)
    parser.add_argument("--queue-size", type=int, default=_QUEUE_SIZE)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = await benchmark_follower_memory(
        arguments.samples, followers=arguments.followers, queue_size=arguments.queue_size
    )
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    with arguments.output.open("x", encoding="utf-8") as output:
        output.write(payload)


if __name__ == "__main__":
    asyncio.run(_main())
