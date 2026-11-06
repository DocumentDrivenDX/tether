"""Aggregate-only host activity observations for reference-performance runs."""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

Phase = Literal["before", "through", "after"]
ActivityState = Literal["quiet", "interference", "unknown"]
SYSTEM_PROCESS_EXCLUSION_POLICY = "exclude-exact-idle-I-root-rows-only"
_PROCESS_STATE_CHARACTERS = frozenset("RSDITUWZ?<>NLls+E")


@dataclass(frozen=True, slots=True)
class ActivityObservation:
    observer: str
    phase: Phase
    state: ActivityState
    observed_at_utc: str
    evidence: str
    measurement_overhead_ms: float = 0.0


class ProcessTableObserver:
    """Observe external work and persist aggregate counts, never process IDs.

    Exclude only the exact idle (I) root row by explicit policy. Every other
    root state and all other users' processes remain unknown/interference.
    """

    def observe(self, phase: Phase) -> ActivityObservation:
        observed = datetime.now(UTC).isoformat()
        if platform.system() != "Darwin":
            return ActivityObservation(
                "process-table-v1", phase, "unknown", observed, "unsupported_host"
            )
        try:
            result = subprocess.run(
                ["/bin/ps", "-axo", "uid=,pid=,ppid=,state="],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ActivityObservation(
                "process-table-v1", phase, "unknown", observed, "process_observation_failed"
            )
        current_pid = os.getpid()
        rows = _parse_process_rows(result.stdout)
        if rows is None or current_pid not in rows:
            return ActivityObservation(
                "process-table-v1", phase, "unknown", observed, "process_table_incomplete"
            )
        state, evidence = _process_activity(rows, current_pid, os.getuid())
        return ActivityObservation("process-table-v1", phase, state, observed, evidence)


def _parse_process_rows(stdout: str) -> dict[int, tuple[int, int, str]] | None:
    rows: dict[int, tuple[int, int, str]] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            return None
        try:
            uid, pid, ppid = (int(value) for value in parts[:3])
        except ValueError:
            return None
        if uid < 0 or pid <= 0 or ppid < 0 or not set(parts[3]) <= _PROCESS_STATE_CHARACTERS:
            return None
        if pid in rows:
            return None
        rows[pid] = (uid, ppid, parts[3])
    return rows


def _process_activity(
    rows: dict[int, tuple[int, int, str]], current_pid: int, current_uid: int
) -> tuple[ActivityState, str]:
    own: set[int] = {current_pid}
    while True:
        children = {pid for pid, (_uid, ppid, _state) in rows.items() if ppid in own}
        expanded = own | children
        if expanded == own:
            break
        own = expanded
    external_count = 0
    unknown_count = 0
    for pid, (uid, _ppid, process_state) in rows.items():
        if pid in own or (uid == 0 and process_state == "I"):
            continue
        if uid == current_uid:
            external_count += 1
        else:
            unknown_count += 1
    evidence = f"same_uid_external_count={external_count};other_uid_external_count={unknown_count}"
    if external_count:
        return "interference", evidence
    if unknown_count:
        return "unknown", evidence
    return "quiet", evidence


__all__ = [
    "SYSTEM_PROCESS_EXCLUSION_POLICY",
    "ActivityObservation",
    "ActivityState",
    "Phase",
    "ProcessTableObserver",
]
