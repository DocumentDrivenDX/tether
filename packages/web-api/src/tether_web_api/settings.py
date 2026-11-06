"""Explicit limits and configuration for the optional HTTP adapter."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import Field, field_validator

from tether.client import Payload


class ApiSettings(Payload):
    api_prefix: str = "/api/v1"
    event_queue_size: int = Field(default=256, gt=0)
    event_batch_limit: int = Field(default=128, gt=0)
    event_max_bytes: int = Field(default=1_048_576, gt=0)
    heartbeat_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    request_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    max_request_bytes: int = Field(default=1_048_576, gt=0)
    allowed_origins: tuple[str, ...] = ()

    @field_validator("api_prefix")
    @classmethod
    def valid_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value == "/" or value.endswith("/"):
            raise ValueError("api_prefix_must_be_absolute_path_without_trailing_slash")
        if "//" in value or ".." in value or "?" in value or "#" in value:
            raise ValueError("api_prefix_invalid")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def valid_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values) or any(not is_exact_origin(origin) for origin in values):
            raise ValueError("allowed_origins_must_be_unique_exact_origins")
        return values


def is_exact_origin(value: str) -> bool:
    if value == "*":
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and bool(hostname)
            and not any(character.isspace() for character in parsed.netloc)
            and parsed.username is None
            and parsed.password is None
            and parsed.path == ""
            and parsed.query == ""
            and parsed.fragment == ""
            and value == f"{parsed.scheme}://{parsed.netloc}"
        )
    except ValueError:
        return False
