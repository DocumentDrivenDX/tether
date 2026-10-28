"""Explicit, secret-free configuration for the optional reference model profile."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue

from tether.types import Count, Payload, PositiveInt
from tether.types.canonical import digest


class ReferenceModelProfile(Payload):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    base_url: str
    model_name: str = Field(min_length=1)
    authorization_file: Path
    output_limit_field: Literal["max_tokens", "max_completion_tokens"]
    context_window_tokens: PositiveInt
    output_tokens: PositiveInt
    safety_margin_tokens: Count
    tokenizer_profile: dict[str, JsonValue]
    vocabulary_path: Path

    @property
    def session_ref(self) -> str:
        return "model:" + self.id + ":" + digest(self.model_dump(mode="json"))[:16]


class FileAuthorization:
    """Read an operator-owned authorization file afresh for each physical attempt."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def authorization(self) -> str | None:
        try:
            value = (await read_bounded(self.path, 4096)).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        return value.strip()


async def read_bounded(path: Path, limit: int) -> bytes:
    def read() -> bytes:
        with path.open("rb") as stream:
            return stream.read(limit + 1)

    data = await asyncio.to_thread(read)
    if len(data) > limit:
        raise ValueError("model_file_bytes")
    return data


async def load_profile(path: Path) -> ReferenceModelProfile:
    """Read a bounded public profile; its authorization value stays in the environment."""

    try:
        data = await read_bounded(path, 65536)
    except ValueError:
        raise ValueError("model_config_bytes") from None
    try:
        profile = ReferenceModelProfile.model_validate_json(data)
    except ValueError:
        raise ValueError("invalid_model_config") from None
    return profile.model_copy(
        update={
            "vocabulary_path": (path.parent / profile.vocabulary_path).resolve(),
            "authorization_file": (path.parent / profile.authorization_file).resolve(),
        }
    )
