"""Fabricated parent process for optional server lifetime qualification."""

import asyncio
import sys

from tether.stores.postgres.embedded import EmbeddedConfig, EmbeddedPostgres


async def main():
    config = EmbeddedConfig.model_validate_json(sys.stdin.readline())
    server = await EmbeddedPostgres.start(config)
    try:
        native = server._processes.server
        assert native is not None
        print(native.pid, flush=True)
        await asyncio.to_thread(sys.stdin.readline)
    finally:
        await server.aclose()


if __name__ == "__main__":
    asyncio.run(main())
