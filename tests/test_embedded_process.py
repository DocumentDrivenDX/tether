"""A dead helper cannot authorize adopting or killing its surviving database."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest
from test_embedded_lifetime import closed, no_live
from test_embedded_postgres import config as config

from tether.stores.postgres.embedded import EmbeddedPostgres
from tether.types.storage import RootBusy

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_parent_sigkill_preserves_orphan_and_refuses_adoption(config):
    peer = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).with_name("embedded_process.py")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    native = None
    try:
        assert peer.stdin is not None
        assert peer.stdout is not None
        peer.stdin.write(config.model_dump_json().encode() + b"\n")
        await peer.stdin.drain()
        raw = await asyncio.wait_for(peer.stdout.readline(), 25)
        assert raw, await peer.stderr.read() if peer.stderr is not None else b""
        native = int(raw)
        os.kill(native, 0)
        peer.kill()
        await peer.wait()
        with pytest.raises(RootBusy, match="embedded_existing_server"):
            await EmbeddedPostgres.start(config)
        await no_live(config)
        os.kill(native, 0)
        assert (config.data_dir / "postmaster.pid").exists()
    finally:
        if peer.returncode is None:
            peer.kill()
            await peer.wait()
        if native is not None:
            os.kill(native, signal.SIGINT)
            from tether.stores.postgres.embedded.files import binaries

            waiter = await asyncio.create_subprocess_exec(
                str(binaries() / "pg_ctl"),
                "-D",
                str(config.data_dir),
                "-w",
                "-t",
                "10",
                "stop",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(waiter.wait(), 15)
    assert not (config.data_dir / "postmaster.pid").exists()
    assert (config.data_dir / "PG_VERSION").is_file()
    server = await EmbeddedPostgres.start(config)
    await closed(server)
