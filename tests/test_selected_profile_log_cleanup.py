"""Owned log readers and children must finish cleanup before refusal or cancellation."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
import selected_profiles_logs as logs
from selected_profiles_privacy_types import PrivacyRejection, PrivacyTurn


class _ObservedEvent(asyncio.Event):
    def __init__(self) -> None:
        super().__init__()
        self.waiting = asyncio.Event()

    async def wait(self) -> Literal[True]:
        self.waiting.set()
        return await super().wait()


class _PartialStart:
    def __init__(self) -> None:
        self.spawning = asyncio.Event()
        self.release_spawn = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.first_cleaned = asyncio.Event()
        self.processes = [
            SimpleNamespace(stdout=object(), stderr=object(), returncode=None),
            SimpleNamespace(stdout=object(), stderr=object(), returncode=None),
        ]
        self.spawn_count = 0

    async def spawn(self, *args: Any, **_kwargs: Any) -> Any:
        assert args[args.index("--timeout") + 1] == "180s"
        self.spawn_count += 1
        if self.spawn_count == 2:
            self.spawning.set()
            await self.release_spawn.wait()
        return self.processes[self.spawn_count - 1]

    async def read(self, _stream: Any, _ready: Any, **_kwargs: Any) -> bytes:
        return b""

    async def stop(self, follower: Any) -> tuple[bytes, bytes]:
        if follower.process is self.processes[1]:
            self.cleaning.set()
            await self.release_cleanup.wait()
        else:
            assert follower.process is self.processes[0]
            self.first_cleaned.set()
        follower.stdout.cancel()
        follower.stderr.cancel()
        await asyncio.gather(follower.stdout, follower.stderr, return_exceptions=True)
        follower.process.returncode = 0
        return b"", b""

    async def unused(self) -> Any:
        raise AssertionError("requests_dispatched_before_both_followers_started")


async def _hold_reader() -> bytes:
    await asyncio.Future()
    return b""


def test_fresh_request_window_retains_both_followers_through_both_requests(monkeypatch) -> None:
    async def run() -> None:
        events: list[str] = []
        followers: list[Any] = []
        anchor_waiting, release_anchor = asyncio.Event(), asyncio.Event()

        async def start(_profile: str, _app: str, source: str | None) -> Any:
            follower = SimpleNamespace(source=source, process=SimpleNamespace(returncode=None))
            followers.append(follower)
            return follower

        async def stop(followers: list[Any]) -> list[tuple[bytes, bytes]]:
            events.append("stop")
            assert [follower.source for follower in followers] == ["APP", None]
            return [(b"", b""), (b"", b"")]

        async def success() -> Any:
            events.append("success")
            from selected_profiles_privacy_types import PrivacyTurn

            return PrivacyTurn("secret", "invocation", "session")

        async def rejected() -> Any:
            events.append("rejected")
            from selected_profiles_privacy_types import PrivacyRejection

            return PrivacyRejection("refused", "credential", "rejected")

        async def wait_for_witnesses(
            active: list[Any], success_turn: Any, rejected_turn: Any, prefix: str
        ) -> None:
            assert [follower.source for follower in active] == ["APP", None]
            assert success_turn.invocation_id == "invocation"
            assert rejected_turn.session_id == "rejected"
            assert prefix == "/api"
            anchor_waiting.set()
            await release_anchor.wait()

        monkeypatch.setattr(logs, "_start_follower", start)
        monkeypatch.setattr(logs, "_stop_followers", stop)
        monkeypatch.setattr(logs, "_wait_for_privacy_witnesses", wait_for_witnesses)
        task = asyncio.create_task(
            logs.capture_privacy_window("profile", "app", success, rejected, api_prefix="/api")
        )
        await anchor_waiting.wait()
        assert not task.done()
        assert "stop" not in events
        release_anchor.set()
        await task
        assert events == ["success", "rejected", "stop"]

    asyncio.run(run())


def test_fresh_anchor_wait_requires_both_streams_and_exact_http_records() -> None:
    async def hold() -> bytes:
        await asyncio.Future()
        return b""

    def record(source: str, message: str) -> bytes:
        item = json.dumps({"source": source, "message": message})
        return (item + "\n").encode()

    async def run() -> None:
        app_changed, platform_changed = _ObservedEvent(), _ObservedEvent()
        readers = [asyncio.create_task(hold()) for _ in range(2)]
        app = logs._Follower(
            cast(Any, SimpleNamespace(returncode=None)),
            readers[0],
            readers[0],
            asyncio.Event(),
            bytearray(record("APP", "TETHER_QUALIFICATION_WORKER_STARTED invocation")),
            bytearray(),
            app_changed,
            asyncio.Event(),
        )
        platform = logs._Follower(
            cast(Any, SimpleNamespace(returncode=None)),
            readers[1],
            readers[1],
            asyncio.Event(),
            bytearray(record("HTTP", '"GET /api/sessions/session HTTP/1.1" 200 OK')),
            bytearray(),
            platform_changed,
            asyncio.Event(),
        )
        task = asyncio.create_task(
            logs._wait_for_privacy_witnesses(
                [app, platform],
                PrivacyTurn("private-success", "invocation", "session"),
                PrivacyRejection("private-rejection", "private-credential", "rejected"),
                "/api",
            )
        )
        await platform_changed.waiting.wait()
        assert not task.done()
        platform.stdout_data.extend(
            record("HTTP", '"POST /api/sessions/rejected/turns HTTP/1.1" 403 Forbidden')
        )
        platform_changed.set()
        await task
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("captured", [b"", b"not-json\n"], ids=["eof", "invalid-line"])
def test_missing_or_invalid_complete_anchor_record_is_incomplete(captured: bytes) -> None:
    async def completed() -> bytes:
        return captured

    async def run() -> None:
        exited = SimpleNamespace(returncode=0)
        reader = asyncio.create_task(completed())
        await reader
        follower = logs._Follower(
            cast(Any, exited),
            reader,
            reader,
            asyncio.Event(),
            bytearray(captured),
            bytearray(),
            asyncio.Event(),
            asyncio.Event(),
        )
        with pytest.raises(RuntimeError, match=r"platform_log_(record_invalid|capture_incomplete)"):
            await logs._wait_for_privacy_witnesses(
                [follower, follower],
                PrivacyTurn("private-success", "invocation", "session"),
                PrivacyRejection("private-rejection", "private-credential", "rejected"),
                "/api",
            )

    asyncio.run(run())


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_known_private_marker_failure_precedes_malformed_log_incomplete(
    stream_name: str,
) -> None:
    async def run() -> None:
        readers = [asyncio.create_task(_hold_reader()) for _ in range(2)]
        app = logs._Follower(
            cast(Any, SimpleNamespace(returncode=None)),
            readers[0],
            readers[0],
            asyncio.Event(),
            bytearray(),
            bytearray(),
            asyncio.Event(),
            asyncio.Event(),
        )
        getattr(app, f"{stream_name}_data").extend(b"malformed fabricated-secret")
        platform = logs._Follower(
            cast(Any, SimpleNamespace(returncode=None)),
            readers[1],
            readers[1],
            asyncio.Event(),
            bytearray(),
            bytearray(),
            asyncio.Event(),
            asyncio.Event(),
        )
        with pytest.raises(RuntimeError, match="privacy_marker_found_in_platform_logs"):
            await logs._wait_for_privacy_witnesses(
                [app, platform],
                PrivacyTurn("fabricated-secret", "invocation", "session"),
                PrivacyRejection("rejected-secret", "credential", "rejected"),
                "/api",
            )
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)

    asyncio.run(run())


def test_overflow_is_signaled_before_reader_finishes_draining() -> None:
    class Stream:
        def __init__(self) -> None:
            self.calls = 0
            self.release = asyncio.Event()

        async def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"12345"
            await self.release.wait()
            return b""

    async def run() -> None:
        stream, overflow = Stream(), asyncio.Event()
        captured = bytearray()
        reader = asyncio.create_task(
            logs._read_bounded(
                cast(Any, stream),
                asyncio.Event(),
                limit=4,
                captured=captured,
                overflow=overflow,
            )
        )
        await overflow.wait()
        assert bytes(captured) == b"1234"
        assert not reader.done()
        stream.release.set()
        with pytest.raises(RuntimeError, match="platform_log_capture_limit_exceeded"):
            await reader
        assert stream.calls == 2

    asyncio.run(run())


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_overflow_stops_anchor_wait_and_retains_drain_cleanup(
    monkeypatch, stream_name: str
) -> None:
    async def run() -> None:
        stopping, release_drain = asyncio.Event(), asyncio.Event()
        both_started = asyncio.Event()
        followers: list[Any] = []

        async def start(_profile: str, _app: str, source: str | None) -> Any:
            follower = SimpleNamespace(
                process=SimpleNamespace(returncode=None),
                stdout=asyncio.create_task(_hold_reader()),
                stderr=asyncio.create_task(_hold_reader()),
                source=source,
                stdout_data=bytearray(),
                stderr_data=bytearray(),
                stdout_changed=asyncio.Event(),
                capture_overflow=asyncio.Event(),
            )
            followers.append(follower)
            if len(followers) == 2:
                both_started.set()
            return follower

        async def stop(active: list[Any]) -> list[tuple[bytes, bytes]]:
            assert active == followers
            stopping.set()
            await release_drain.wait()
            for follower in active:
                follower.stdout.cancel()
                follower.stderr.cancel()
            await asyncio.gather(
                *(reader for follower in active for reader in (follower.stdout, follower.stderr)),
                return_exceptions=True,
            )
            return [(b"", b""), (b"", b"")]

        async def success() -> Any:
            return PrivacyTurn("private-success", "invocation", "session")

        async def rejected() -> Any:
            return PrivacyRejection("private-rejection", "private-credential", "rejected")

        monkeypatch.setattr(logs, "_start_follower", start)
        monkeypatch.setattr(logs, "_stop_followers", stop)
        task = asyncio.create_task(
            logs.capture_privacy_window("profile", "app", success, rejected, api_prefix="/api")
        )
        await both_started.wait()
        followers[1].capture_overflow.set()
        getattr(followers[1], f"{stream_name}_data").extend(b"over-cap")
        followers[1].stdout_changed.set()
        await stopping.wait()
        assert not task.done()
        release_drain.set()
        with pytest.raises(RuntimeError, match="platform_log_capture_limit_exceeded"):
            await task

    asyncio.run(run())


def test_failed_follower_cleanup_joins_other_follower(monkeypatch) -> None:
    async def run() -> None:
        held = asyncio.Event()
        entered = asyncio.Event()

        async def stop(follower: Any) -> tuple[bytes, bytes]:
            if follower == "first":
                raise RuntimeError("fabricated_reader_failure")
            entered.set()
            await held.wait()
            return b"", b""

        monkeypatch.setattr(logs, "_stop_follower", stop)
        task = asyncio.create_task(
            logs._stop_followers(cast(list[logs._Follower], ["first", "second"]))
        )
        await entered.wait()
        await asyncio.sleep(0)
        assert not task.done()
        held.set()
        with pytest.raises(RuntimeError, match="platform_log_capture_incomplete"):
            await task

    asyncio.run(run())


def test_cancelled_second_start_retains_both_child_cleanups(monkeypatch) -> None:
    async def run() -> None:
        scenario = _PartialStart()
        monkeypatch.setattr(logs.shutil, "which", lambda _name: "/fabricated/databricks")
        monkeypatch.setattr(logs.asyncio, "create_subprocess_exec", scenario.spawn)
        monkeypatch.setattr(logs, "_read_bounded", scenario.read)
        monkeypatch.setattr(logs, "_stop_follower", scenario.stop)
        task = asyncio.create_task(
            logs.capture_privacy_window(
                "profile", "app", scenario.unused, scenario.unused, api_prefix="/api"
            )
        )
        await scenario.spawning.wait()
        task.cancel()
        scenario.release_spawn.set()
        await scenario.cleaning.wait()
        task.cancel()
        assert not task.done()
        assert scenario.processes[0].returncode is None
        assert scenario.processes[1].returncode is None
        scenario.release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await scenario.first_cleaned.wait()
        assert scenario.processes[0].returncode == 0
        assert scenario.processes[1].returncode == 0

    asyncio.run(run())
