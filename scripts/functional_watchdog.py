"""Canonical emergency watchdog configuration for functional command owners."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping

FUNCTIONAL_WATCHDOG_ENV = "TETHER_FUNCTIONAL_WATCHDOG_MS"
DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS = 30 * 60
MAX_FUNCTIONAL_WATCHDOG_MS = 2**31 - 1
WATCHDOG_DECIMAL = re.compile(r"[1-9][0-9]*\Z")


def functional_watchdog_seconds(values: Mapping[str, str]) -> float:
    raw = values.get(FUNCTIONAL_WATCHDOG_ENV)
    if raw is None:
        return float(DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS)
    if not WATCHDOG_DECIMAL.fullmatch(raw):
        raise ValueError("functional_watchdog_invalid")
    milliseconds = int(raw)
    if not 1_000 <= milliseconds <= MAX_FUNCTIONAL_WATCHDOG_MS:
        raise ValueError("functional_watchdog_invalid")
    seconds = milliseconds / 1000
    if not math.isfinite(seconds):
        raise ValueError("functional_watchdog_invalid")
    return seconds
