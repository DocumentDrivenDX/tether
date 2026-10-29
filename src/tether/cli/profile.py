"""Explicit, secret-free configuration for the optional reference model profile."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue, model_validator

from tether.types import Count, Payload, PositiveInt
from tether.types.canonical import digest


class ReferenceModelProfile(Payload):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    base_url: str
    model_name: str = Field(min_length=1)
    authorization_file: Path | None = None
    databricks_profile: str | None = None
    available_models: tuple[str, ...] = Field(default=(), exclude=True)
    output_limit_field: Literal["max_tokens", "max_completion_tokens"]
    context_window_tokens: PositiveInt
    output_tokens: PositiveInt
    safety_margin_tokens: Count
    max_response_bytes: PositiveInt = Field(default=8192, le=1024 * 1024)
    tokenizer_profile: dict[str, JsonValue] | None = None
    vocabulary_path: Path | None = None
    workspace: Path | None = None
    identity_mode: Literal["caller", "anonymous"] = "caller"
    context_source: Literal["configured", "server", "starter"] = "configured"

    @model_validator(mode="after")
    def complete(self) -> ReferenceModelProfile:
        if self.authorization_file is not None and self.databricks_profile is not None:
            raise ValueError("Choose an authorization file or a Databricks profile")
        if (
            self.identity_mode == "caller"
            and self.authorization_file is None
            and self.databricks_profile is None
        ):
            raise ValueError("Caller mode requires an authorization file or Databricks profile")
        if (self.tokenizer_profile is None) != (self.vocabulary_path is None):
            raise ValueError("Supply both tokenizer_profile and vocabulary_path")
        return self

    @property
    def session_ref(self) -> str:
        values = self.model_dump(mode="json")
        if self.workspace is None:
            values.pop("workspace")
        if self.databricks_profile is None:
            values.pop("databricks_profile")
        # Preserve the previous caller-profile identity when only new defaults are omitted.
        if self.identity_mode == "caller":
            values.pop("identity_mode")
        if self.context_source == "configured":
            values.pop("context_source")
        return "model:" + self.id + ":" + digest(values)[:16]


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
            "workspace": (path.parent / profile.workspace).resolve()
            if profile.workspace is not None
            else None,
            "vocabulary_path": (path.parent / profile.vocabulary_path).resolve()
            if profile.vocabulary_path is not None
            else None,
            "authorization_file": (path.parent / profile.authorization_file).resolve()
            if profile.authorization_file is not None
            else None,
        }
    )
