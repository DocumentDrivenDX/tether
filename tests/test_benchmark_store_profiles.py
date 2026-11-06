"""Structural controls for both explicit benchmark storage profiles."""

import asyncio
from types import SimpleNamespace
from typing import cast

import benchmark_store_profiles as profiles
import pytest
from benchmark_profiles import PROFILES
from benchmark_store_profiles import benchmark_store_profile, page_positions


def test_page_positions_are_unique_even_and_cover_endpoints() -> None:
    positions = page_positions(199)
    assert len(positions) == len(set(positions)) == 100
    assert positions[0] == 0
    assert positions[-1] == 99
    assert list(positions) == sorted(positions)

    reference_positions = page_positions(100_000)
    assert len(reference_positions) == 100
    assert len(reference_positions) < 100_000 - 100 + 1


@pytest.mark.parametrize("entry_count", [0, 99, 198])
def test_page_positions_reject_ranges_without_100_distinct_windows(entry_count: int) -> None:
    with pytest.raises(ValueError, match="range_too_small"):
        page_positions(entry_count)


def test_page_assertion_checks_real_fixture_indices_without_timing() -> None:
    calls: list[tuple[str, str, int, int]] = []

    class Store:
        async def entries(self, session, owner, *, start, limit):
            calls.append((session, owner, start, limit))
            return SimpleNamespace(
                items=tuple(SimpleNamespace(index=index) for index in range(start, start + limit))
            )

    assert asyncio.run(
        profiles._page(cast(profiles.JsonlSessionStore, Store()), "fabricated-session", 7)
    ) == tuple(range(7, 107))
    assert calls == [("fabricated-session", profiles.OWNER, 7, 100)]

    class WrongPage(Store):
        async def entries(self, session, owner, *, start, limit):
            del session, owner, start, limit
            return SimpleNamespace(items=(SimpleNamespace(index=0),))

    with pytest.raises(RuntimeError, match="entry_page_indices_mismatch"):
        asyncio.run(
            profiles._page(cast(profiles.JsonlSessionStore, WrongPage()), "fabricated-session", 7)
        )


def test_store_reference_workload_is_registered_only_for_explicit_performance_path() -> None:
    profile = PROFILES["memory-store"]
    assert profile.script == "benchmark_web.py"
    assert profile.arguments == ("--storage", "memory")
    assert profile.profile == "memoryfiles-jsonl-reference-capacity"
    assert profile.deadline_seconds == 18_000


def test_page_profile_rejects_nonpositive_sample_count(tmp_path) -> None:
    with pytest.raises(ValueError, match="workload_or_sample_count_invalid"):
        asyncio.run(
            benchmark_store_profile(
                profile="memoryfiles-jsonl-reference-capacity",
                root_path=tmp_path,
                sessions=1,
                entries_per_session=1,
                large_session_entries=199,
                samples=99,
            )
        )


def test_store_open_failure_closes_owned_files_and_preserves_primary_error(
    tmp_path, monkeypatch
) -> None:
    class Files:
        durability = "os"

        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> bool:
            self.closed = True
            return True

    files = Files()

    async def open_files(cls, path, *, limits=None):
        return files

    async def open_store(cls, owned_files):
        raise ValueError("open_primary")

    monkeypatch.setattr(profiles.JsonlRoot, "open", classmethod(open_files))
    monkeypatch.setattr(profiles.JsonlSessionStore, "open", classmethod(open_store))
    with pytest.raises(ValueError, match="open_primary"):
        asyncio.run(
            benchmark_store_profile(
                profile="local-posix-jsonl",
                root_path=tmp_path,
                sessions=1,
                entries_per_session=1,
                large_session_entries=199,
                samples=100,
            )
        )
    assert files.closed


def test_store_false_close_refuses_success_result() -> None:
    class Files:
        async def wait_closed(self) -> bool:
            return False

    class Store:
        files = Files()

        async def aclose(self) -> bool:
            return False

    with pytest.raises(RuntimeError, match="resources_not_closed"):
        asyncio.run(profiles._close_store(cast(profiles.JsonlSessionStore, Store())))
