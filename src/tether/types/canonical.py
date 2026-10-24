"""Shared canonical JSON for immutable content and execution bindings."""

import hashlib
import json
from collections.abc import Sequence

from pydantic import JsonValue

from tether.types import AttachmentRef


def canonical(value: JsonValue) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def digest(value: JsonValue) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def input_digest(text: str, attachments: Sequence[AttachmentRef], spec: str | None) -> str:
    return digest(
        {
            "text": text,
            "attachments": [v.model_dump(mode="json") for v in attachments],
            "spec": spec,
        }
    )
