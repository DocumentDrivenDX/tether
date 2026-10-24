"""Shared canonical JSON for immutable content and execution bindings."""

import hashlib
import json

from pydantic import JsonValue


def canonical(value: JsonValue) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def digest(value: JsonValue) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()
