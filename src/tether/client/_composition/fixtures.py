"""Owned process-only composition for the supported public test-kit factory."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from .host import ProvidedStorage


@asynccontextmanager
async def fabricated_storage() -> AsyncGenerator[ProvidedStorage]:
    """Own an isolated process-only port fixture; borrowers must close before exit.

    The supplied store/history implement the public ports. Nothing persists to
    disk and this fixture cannot qualify restart durability or external storage.
    """
    from tether.history.jsonl import JsonlHistory
    from tether.stores.jsonl import JsonlSessionStore
    from tether.stores.memory import MemoryFiles

    files = MemoryFiles()
    store = await JsonlSessionStore.open(files)
    try:
        history = await JsonlHistory.open(files)
        yield ProvidedStorage(store=store, history=history)
    finally:
        await store.aclose()
