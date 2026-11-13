"""Shared fabricated oracle inputs for its focused unit-control modules."""

from __future__ import annotations

import json
from typing import Any

import selected_profiles_pg_oracle as oracle

_VERSION = oracle.EXPECTED_ASYNCPG_VERSION
_REQUEST: dict[str, Any] = {
    "host": "database.example.test",
    "port": 5432,
    "user": "caller",
    "password": "-".join(("fabricated", "value")),
    "database": "db",
    "ca_file": "/fabricated/ca.pem",
}


def _report(**override: object) -> bytes:
    value = {
        "kind": "connected",
        "current_user": "caller",
        "sqlstate": None,
        "driver_version": _VERSION,
        **override,
    }
    return json.dumps(value).encode() + b"\n"
