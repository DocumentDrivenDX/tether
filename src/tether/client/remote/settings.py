"""Settings for an HTTP-backed public client."""

from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlsplit

from tether.client.protocol import CredentialProvider


@dataclass(frozen=True)
class RemoteClientSettings:
    """Origin, API prefix, credential source, and request bounds for a remote client."""

    base_url: str
    credential_provider: CredentialProvider
    api_prefix: str = "/api/v1"
    request_timeout_seconds: float = 30.0
    max_sse_line_bytes: int = 1_048_576
    max_sse_event_bytes: int = 1_048_576
    event_idle_timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("base_url_must_be_http_origin") from None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("base_url_must_be_http_origin")
        if parsed.query or parsed.fragment or "?" in self.base_url or "#" in self.base_url:
            raise ValueError("base_url_must_not_include_query_or_fragment")
        if not self.api_prefix.startswith("/") or self.api_prefix.endswith("/"):
            raise ValueError("api_prefix_must_be_absolute_without_trailing_slash")
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not math.isfinite(self.request_timeout_seconds)
            or self.request_timeout_seconds <= 0
        ):
            raise ValueError("request_timeout_must_be_positive")
        if (
            type(self.max_sse_line_bytes) is not int
            or type(self.max_sse_event_bytes) is not int
            or self.max_sse_line_bytes <= 0
            or self.max_sse_event_bytes <= 0
            or self.max_sse_line_bytes > self.max_sse_event_bytes
        ):
            raise ValueError("remote_event_limits_must_be_positive")
        if (
            isinstance(self.event_idle_timeout_seconds, bool)
            or not math.isfinite(self.event_idle_timeout_seconds)
            or self.event_idle_timeout_seconds <= 0
        ):
            raise ValueError("remote_event_idle_timeout_must_be_positive")
