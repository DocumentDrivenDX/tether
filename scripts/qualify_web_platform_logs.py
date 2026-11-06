"""Drain and reap the selected Apps log follower without retaining raw output."""

from __future__ import annotations

import contextlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import BinaryIO

from qualify_live_apps_incomplete import LiveQualificationIncomplete

_MARKER = b"TETHER_SHUTDOWN_REPORT "
_MAX_LINE_BYTES = 64 * 1024
_MAX_MARKER_BYTES = 256 * 1024


@dataclass
class _LogCapture:
    marker_lines: list[str] = field(default_factory=list)
    marker_bytes: int = 0
    overflow: bool = False
    read_failed: bool = False

    def read(self, stream: BinaryIO) -> None:
        try:
            while chunk := stream.readline(_MAX_LINE_BYTES + 1):
                if len(chunk) > _MAX_LINE_BYTES:
                    self.overflow = True
                    while chunk and not chunk.endswith(b"\n"):
                        chunk = stream.readline(_MAX_LINE_BYTES + 1)
                    continue
                if not chunk.endswith(b"\n") or _MARKER not in chunk:
                    continue
                if self.marker_bytes + len(chunk) > _MAX_MARKER_BYTES:
                    self.overflow = True
                    continue
                self.marker_lines.append(chunk.decode("utf-8", errors="replace").rstrip("\r\n"))
                self.marker_bytes += len(chunk)
        except OSError:
            self.read_failed = True


class OwnedLogFollower:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            if process.poll() is None:
                process.kill()
            process.wait()
            raise RuntimeError("qualification_log_stream_unavailable")
        self.process = process
        self.capture = _LogCapture()
        from threading import Thread

        self.reader = Thread(target=self.capture.read, args=(process.stdout,))
        self.reader.start()
        self.finished = False
        self.terminal_error: LiveQualificationIncomplete | None = None

    def finish(self, deadline: float | None, clock: Callable[[], float]) -> tuple[str, ...]:
        if self.finished:
            if self.terminal_error is not None:
                raise self.terminal_error
            return tuple(self.capture.marker_lines)
        timed_out = False
        if self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
        try:
            remaining = None if deadline is None else max(0.0, deadline - clock())
            try:
                self.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                self.process.wait()
        finally:
            if self.process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                self.process.wait()
            self.reader.join()
            self.finished = True
        if timed_out or self.capture.overflow or self.capture.read_failed:
            self.terminal_error = LiveQualificationIncomplete(
                "qualification_shutdown_log_not_observed"
            )
            raise self.terminal_error
        return tuple(self.capture.marker_lines)


def wait_app_state(
    profile: str,
    app: str,
    accepted: set[tuple[str, str]],
    *,
    deadline: float | None,
    cli: Callable[..., str],
) -> None:
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise LiveQualificationIncomplete("qualification_app_state_not_observed")
        value = json.loads(cli(profile, "apps", "get", app, timeout_seconds=30))
        app_state = value.get("app_status", {}).get("state")
        compute_state = value.get("compute_status", {}).get("state")
        if deadline is not None and time.monotonic() >= deadline:
            raise LiveQualificationIncomplete("qualification_app_state_not_observed")
        if (app_state, compute_state) in accepted:
            return
        time.sleep(3)


def restart_app(
    profile: str,
    app: str,
    watchdog_seconds: float | None,
    cli: Callable[..., str],
) -> None:
    del profile, app, watchdog_seconds, cli
    # The public Apps log-follow protocol has no correlated attachment
    # acknowledgment, so stopping here could lose the shutdown report.
    raise LiveQualificationIncomplete("qualification_log_observer_readiness_unavailable")


def _validate_shutdown_report(
    lines: tuple[str, ...], report_error: LiveQualificationIncomplete | None
) -> None:
    if report_error is not None:
        raise report_error
    reports: list[dict[str, object]] = []
    for line in lines:
        marker = "TETHER_SHUTDOWN_REPORT "
        if marker in line:
            try:
                reports.append(json.loads(line.split(marker, 1)[1]))
            except ValueError:
                continue
    if not reports or reports[-1].get("complete") is not True or reports[-1].get("outstanding"):
        if not reports:
            raise LiveQualificationIncomplete("qualification_shutdown_log_not_observed")
        raise RuntimeError("bounded_shutdown_report_incomplete")
    versions = reports[-1].get("asgi_http_versions")
    if not isinstance(versions, list) or "2" not in versions:
        raise RuntimeError("apps_backend_http2_not_observed")


__all__ = ["OwnedLogFollower", "restart_app", "wait_app_state"]
