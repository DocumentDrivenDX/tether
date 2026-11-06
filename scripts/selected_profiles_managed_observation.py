"""Optional caller watchdog and retained resource acquisition for managed proofs."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

from selected_profiles_process import await_retained

T = TypeVar("T")


class ObservationWatchdogExpired(RuntimeError):
    """The caller-selected observation watchdog expired after owned cleanup."""


async def run_with_observation_watchdog(
    seconds: float | None,
    operation: Callable[[], Awaitable[T]],
    *,
    timeout_factory: Callable[[float], Any] = asyncio.timeout,
) -> T:
    """Use state completion by default; an explicit value is a caller emergency bound."""
    if seconds is None:
        return await operation()
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        raise ValueError("managed_observation_watchdog_invalid")
    watchdog = timeout_factory(seconds)
    try:
        async with watchdog:
            return await operation()
    except TimeoutError:
        if not watchdog.expired():
            raise
        raise ObservationWatchdogExpired("managed_observation_watchdog_expired") from None


async def acquire_or_close_on_cancellation(
    opening: Coroutine[Any, Any, T], close: Callable[[T], Coroutine[Any, Any, object]]
) -> T:
    """Retain open and close work; close any resource returned after caller cancellation."""
    task = asyncio.create_task(opening)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                cancelled = True
        except Exception:
            break
    try:
        resource = task.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if cancelled:
        cleanup = asyncio.create_task(close(resource))
        await await_retained(cleanup)
        raise asyncio.CancelledError
    return resource


async def database_observation(context: Any) -> dict[str, object]:
    async with context.source(context.actor, None) as connection:
        version_cursor = await connection.execute("SELECT current_setting('server_version_num')")
        version_row = await version_cursor.fetchone()
        tls_cursor = await connection.execute(
            "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
        )
        tls_row = await tls_cursor.fetchone()
        role_cursor = await connection.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname = current_user"
        )
        role_row = await role_cursor.fetchone()
        identity_cursor = await connection.execute("SELECT current_user")
        identity_row = await identity_cursor.fetchone()
    if not version_row or not tls_row or not role_row or not identity_row:
        raise RuntimeError("managed_process_database_observation_missing")
    version_value = version_row[0]
    if (
        not isinstance(version_value, str)
        or not version_value.isascii()
        or not version_value.isdigit()
    ):
        raise RuntimeError("managed_process_database_observation_invalid")
    version = int(version_value)
    if len(role_row) != 4 or any(type(flag) is not bool for flag in role_row):
        raise RuntimeError("managed_process_database_observation_invalid")
    superuser, bypass_rls, create_role, create_database = role_row
    role_safe = not superuser and not bypass_rls and not create_role and not create_database
    identity_matches = identity_row[0] == context.owner
    if version // 10000 != 18 or tls_row[0] is not True or not role_safe or not identity_matches:
        raise RuntimeError("managed_process_database_profile_mismatch")
    return {
        "server_version_num": version,
        "postgres_major": version // 10000,
        "tls_verified": True,
        "nonadmin_role": True,
        "role_superuser": superuser,
        "role_bypassrls": bypass_rls,
        "role_createrole": create_role,
        "role_createdb": create_database,
        "caller_identity_matches": True,
    }


__all__ = [
    "ObservationWatchdogExpired",
    "acquire_or_close_on_cancellation",
    "database_observation",
    "run_with_observation_watchdog",
]
