"""A dead helper cannot authorize adopting or killing its surviving database."""

import sys
from pathlib import Path

import pytest
from embedded_lifetime_controls import (
    assert_native_absent,
    close_embedded,
    orphaned_server,
    runtime_cleanup_safe,
    verify_native_identity,
)
from process_lifetime_controls import OwnedProcess
from test_embedded_lifetime import no_live
from test_embedded_postgres import config as config
from test_embedded_postgres import pending_io_control as pending_io_control

from tether.stores.postgres.embedded import EmbeddedPostgres
from tether.types.storage import RootBusy

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_parent_sigkill_preserves_orphan_and_refuses_adoption(config, pending_io_control):
    async with orphaned_server(config) as orphan:
        verify_native_identity(orphan.identity)
        with pytest.raises(RootBusy, match="embedded_existing_server"):
            await EmbeddedPostgres.start(config)
        await no_live(config)
        verify_native_identity(orphan.identity)
        assert (config.data_dir / "postmaster.pid").exists()
        await orphan.stop()
        assert_native_absent(orphan.identity)
    assert not (config.data_dir / "postmaster.pid").exists()
    assert (config.data_dir / "PG_VERSION").is_file()
    server = await EmbeddedPostgres.start(config)
    await close_embedded(server)


async def test_unknown_wrapper_exit_preserves_directory_without_signaling_a_native_pid(
    config, tmp_path, monkeypatch
):
    unknown = config.model_copy(
        update={
            "data_dir": tmp_path / "unknown-data",
            "runtime_dir": tmp_path / "unknown-runtime",
        }
    )
    original = OwnedProcess.spawn
    owners = []
    commands = []

    async def spawn(*argv, **options):
        commands.append(tuple(str(part) for part in argv))
        if Path(str(argv[0])) == Path(sys.executable):
            owner = await original(
                sys.executable,
                "-u",
                "-c",
                "import sys; sys.stdin.readline()",
                **options,
            )
            owners.append(owner)
            return owner
        raise AssertionError("unknown_native_identity_must_not_be_signaled")

    monkeypatch.setattr(OwnedProcess, "spawn", spawn)
    with pytest.raises(RuntimeError, match="embedded_wrapper_exited_before_native_pid"):
        async with orphaned_server(unknown):
            raise AssertionError("wrapper_without_pid_must_not_publish_an_owner")
    assert len(owners) == 1
    assert owners[0].returncode == 0
    assert owners[0]._wait_task.done()
    assert owners[0]._stderr_task.done()
    assert len(commands) == 1
    assert not runtime_cleanup_safe(unknown.data_dir)
    assert not (unknown.data_dir / "postmaster.pid").exists()
