"""Exercise real public-host followers, reconnect and turns for benchmark tooling."""

import asyncio
from pathlib import Path

from benchmark_client import benchmark_environment, settings

from tether.client import Message, ModelRequest, RequestTags, TokenEstimate, VolatileSettings


def test_volatile_client_profile_has_explicit_storage_and_capacity_without_running_measurements(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path, 2, 2, storage="volatile")
    assert isinstance(config.storage, VolatileSettings)
    assert config.resume == "off"
    assert config.client_limits.follower_queue_size == 32


def test_benchmark_profile_settings_do_not_run_measurements(tmp_path: Path) -> None:
    config = settings(tmp_path, 2, 4, storage="posix")
    assert config.storage.__class__.__name__ == "LocalJsonlSettings"
    assert config.client_limits.follower_queue_size == 32
    assert config.context_config.max_source_bytes == 4 * 1024 * 1024


def test_benchmark_estimator_exceeds_fixture_capacity_without_recording_or_mutation(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path, 2, 4)
    assert config.context_config.timeout_seconds == 30
    assert config.context_config.max_source_bytes == 4 * 1024 * 1024
    request = ModelRequest(
        model="fabricated",
        tags=RequestTags(
            session_id="session",
            principal="owner",
            spec_id="demo",
            spec_version="1",
            turn_id="turn",
            model_call_id="call",
        ),
        system="Fabricated instructions",
        messages=(Message(role="user", content="Fabricated request"),),
        max_output_tokens=64,
    )

    async def estimates() -> None:
        estimator = config.estimator
        before = request.model_dump(mode="json")
        previous: TokenEstimate | None = None
        for _ in range(1025):
            result = await estimator.estimate(request)
            assert result.tokens == 0
            assert result.quality == "approximate"
            assert result.method == "benchmark-approximate"
            assert result is not previous
            assert request.model_dump(mode="json") == before
            previous = result

    asyncio.run(estimates())


def test_benchmark_environment_keeps_fabricated_child_profile_explicit() -> None:
    environment = benchmark_environment()
    assert environment["LC_ALL"] == "C.UTF-8"
    assert environment["PATH"]
    assert environment["HOME"]
    assert "PYTHONPATH" not in environment
