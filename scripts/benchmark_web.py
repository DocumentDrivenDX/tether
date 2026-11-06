"""Reproducible fabricated JSONL reference workload; no hosted latency inference."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark_store_profiles import StorageProfile, StoreProfileResult, benchmark_store_profile
from web_artifacts import inventory

PROJECT = Path(__file__).resolve().parents[1]


async def benchmark(arguments: argparse.Namespace) -> None:
    sources = await asyncio.to_thread(inventory, PROJECT)
    with TemporaryDirectory(prefix="tether-web-benchmark-") as directory:
        profile: StorageProfile = (
            "local-posix-jsonl"
            if arguments.storage == "posix"
            else "memoryfiles-jsonl-reference-capacity"
        )
        observation = await benchmark_store_profile(
            profile=profile,
            root_path=Path(directory),
            sessions=arguments.sessions,
            entries_per_session=arguments.entries,
            large_session_entries=arguments.large,
            samples=arguments.samples,
        )
    if sources != await asyncio.to_thread(inventory, PROJECT):
        raise RuntimeError("benchmark_source_changed_during_observation")
    await asyncio.to_thread(write_receipt, arguments, observation, sources)
    print(json.dumps(observation["results"], indent=2))


def write_receipt(
    arguments: argparse.Namespace,
    observation: StoreProfileResult,
    sources: dict[str, str],
) -> None:
    versions = {
        "python": platform.python_version(),
        "platform": platform.system(),
        "architecture": platform.machine(),
    }
    source = {
        path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
        for path in (
            "scripts/benchmark_web.py",
            "scripts/benchmark_store_profiles.py",
            "scripts/benchmark_measurements.py",
            "src/tether/stores/jsonl/store.py",
            "src/tether/stores/jsonl/reads.py",
            "src/tether/stores/queries.py",
            "src/tether/stores/memory.py",
        )
    }
    receipt = {
        "profile": observation["profile"],
        "workload": observation["workload"],
        "versions": versions,
        "source": source,
        "source_sha256": sources,
        "journal_bytes": observation["journal_bytes"],
        "results": observation["results"],
        "limits": observation["limits"],
        "durability": observation["durability"],
        "checks": observation["checks"],
        "scope": observation["scope"],
    }
    arguments.output.write_text(json.dumps(receipt, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10_000)
    parser.add_argument("--entries", type=int, default=200)
    parser.add_argument("--large", type=int, default=100_000)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--storage", choices=("posix", "memory"), default="posix")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if (
        min(arguments.sessions, arguments.entries) <= 0
        or arguments.large < 199
        or arguments.samples < 100
    ):
        parser.error("positive dataset sizes, 199 large entries and at least 100 samples required")
    asyncio.run(benchmark(arguments))


if __name__ == "__main__":
    main()
