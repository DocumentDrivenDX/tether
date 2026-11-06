"""Settle owned persistence before cancellation can release turn ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from tether.types.lifetime import settle_completion

T = TypeVar("T")


async def settled(operation: Awaitable[T], *, cancel_after: bool = True) -> T:
    # Real persistence failures retain authority over cancellation. CommitUnknown
    # keeps the backend's reopen/refusal guard; success includes local publication.
    value, cancelled = await settle_completion(operation)
    if cancelled and cancel_after:
        raise asyncio.CancelledError
    return value
