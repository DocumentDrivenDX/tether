"""Fabricated parent process for optional server lifetime qualification."""

import asyncio
import sys

from embedded_lifetime_controls import close_embedded
from postgres_pending_io_controls import PendingIOControl

from tether.stores.postgres import io as io_module
from tether.stores.postgres.embedded import EmbeddedConfig, EmbeddedPostgres


async def main():
    config = EmbeddedConfig.model_validate_json(sys.stdin.readline())
    control = PendingIOControl()
    original_asyncio = io_module.asyncio
    io_module.asyncio = control.facade
    try:
        server = await EmbeddedPostgres.start(config)
        assert control.durations == (config.startup_seconds,)
    finally:
        io_module.asyncio = original_asyncio
    try:
        native = server._processes.server
        assert native is not None
        print(native.pid, flush=True)
        await asyncio.to_thread(sys.stdin.readline)
    finally:
        await close_embedded(server)


if __name__ == "__main__":
    asyncio.run(main())
