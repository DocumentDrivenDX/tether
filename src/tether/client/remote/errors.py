"""Public failures from the optional HTTP transport."""

from __future__ import annotations

from collections.abc import Mapping


class RemoteTransportError(RuntimeError):
    """A network or protocol failure that does not expose response content."""


class RemoteProblem(RuntimeError):
    """A typed HTTP problem returned by the Tether API."""

    def __init__(
        self,
        status: int,
        code: str,
        *,
        retryable: bool = False,
        locator: Mapping[str, str] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.retryable = retryable
        self.locator = dict(locator) if locator is not None else None
        self.retry_after_seconds = retry_after_seconds
        super().__init__(code)
