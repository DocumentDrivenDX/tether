"""Explicit test resources and metadata-only qualification reports."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal

from tether.types import JsonValue, Payload
from tether.types.archive import ArchivedSession, ArchiveSink, ArchiveSource
from tether.types.execution import Actor
from tether.types.storage import SessionStore


class SuiteReport(Payload):
    schema_version: Literal[1] = 1
    suite: str
    capabilities: dict[str, JsonValue]
    sink_capabilities: dict[str, JsonValue] | None = None
    checks: tuple[str, ...]
    required_external: tuple[str, ...]


class ConformanceFailure(AssertionError):
    """A stable check identifier, never a serialized provider value."""


def require(condition: bool, check: str) -> None:
    if not condition:
        raise ConformanceFailure(check)


@dataclass(frozen=True)
class LiveStoreFixture:
    store: SessionStore
    actor: Actor
    foreign: Actor


@dataclass(frozen=True)
class ArchiveFixture:
    source: ArchiveSource
    actor: Actor
    foreign: Actor
    sink: ArchiveSink | None = None


LiveStoreFactory = Callable[[], AbstractAsyncContextManager[LiveStoreFixture]]
ArchiveFactory = Callable[[ArchivedSession], AbstractAsyncContextManager[ArchiveFixture]]
