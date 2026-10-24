"""Applications supply idle async connections and own credential/pool/RLS policy."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from psycopg import AsyncConnection

from tether.types.execution import Actor


class ConnectionSource(Protocol):
    def __call__(
        self, actor: Actor, session_id: str | None, /
    ) -> AbstractAsyncContextManager[AsyncConnection[tuple[object, ...]]]: ...
