"""Closed wire format for the isolated PostgreSQL qualification oracle."""

from __future__ import annotations

import json
from typing import Literal, TypedDict, cast


class OracleWireResult(TypedDict):
    kind: Literal["connected", "auth_refused", "other_failure"]
    current_user: str | None
    sqlstate: str | None
    driver_version: str


def encode_result(
    kind: Literal["connected", "auth_refused", "other_failure"],
    current_user: str | None,
    sqlstate: str | None,
    driver_version: str,
    max_output_bytes: int,
) -> bytes:
    data = json.dumps(
        {
            "kind": kind,
            "current_user": current_user,
            "sqlstate": sqlstate,
            "driver_version": driver_version,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    if len(data) > max_output_bytes:
        raise RuntimeError("oracle_output_limit")
    return data + b"\n"


def decode_result(
    raw: bytes,
    returncode: int,
    *,
    max_output_bytes: int,
    expected_driver_version: str,
    auth_sqlstates: frozenset[str],
    expired_exit_code: int,
) -> OracleWireResult:
    if (
        returncode not in {0, expired_exit_code}
        or not raw
        or len(raw) > max_output_bytes
        or not raw.endswith(b"\n")
    ):
        raise RuntimeError("database_oracle_child_failed")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("database_oracle_child_failed") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"kind", "current_user", "sqlstate", "driver_version"}
        or value.get("kind") not in {"connected", "auth_refused", "other_failure"}
        or (
            value.get("current_user") is not None and not isinstance(value.get("current_user"), str)
        )
        or (value.get("sqlstate") is not None and not isinstance(value.get("sqlstate"), str))
        or value.get("driver_version") != expected_driver_version
    ):
        raise RuntimeError("database_oracle_child_failed")
    if value["kind"] == "connected" and value["sqlstate"] is not None:
        raise RuntimeError("database_oracle_child_failed")
    if value["kind"] == "auth_refused" and value["sqlstate"] not in auth_sqlstates:
        raise RuntimeError("database_oracle_child_failed")
    if value["kind"] == "other_failure" and (
        value["sqlstate"] is not None or value["current_user"] is not None
    ):
        raise RuntimeError("database_oracle_child_failed")
    if returncode == expired_exit_code and (
        value["kind"] != "other_failure"
        or value["current_user"] is not None
        or value["sqlstate"] is not None
    ):
        raise RuntimeError("database_oracle_child_failed")
    return cast(OracleWireResult, value)
