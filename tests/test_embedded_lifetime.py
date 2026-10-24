"""Embedded ownership follows physical startup, acquisitions and worker exit."""

import asyncio
from contextlib import asynccontextmanager
from threading import Event

import pytest
from test_embedded_postgres import acquisition
from test_embedded_postgres import config as config

from tether.stores.postgres import migrate
from tether.stores.postgres.embedded import EmbeddedPostgres
from tether.stores.postgres.embedded import server as module
from tether.stores.postgres.embedded.processes import Processes
from tether.types.execution import Actor, ExecutionDenied
from tether.types.storage import IntegrityError, RootBusy, StorageLimits, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def process(server: EmbeddedPostgres) -> asyncio.subprocess.Process:
    value = server._processes.server
    assert value is not None
    return value


async def no_live(config):
    async with asyncio.timeout(10):
        for server in tuple(module._LIVE):
            if server.config.data_dir == config.data_dir:
                task = server._close_task
                assert task is not None
                await asyncio.shield(task)


async def closed(server):
    async with asyncio.timeout(10):
        await server.aclose()
        task = server._close_task
        assert task is not None
        await asyncio.shield(task)
        assert await server.aclose()


async def test_duplicate_canonical_data_owner_refuses_even_with_other_runtime(config, tmp_path):
    server = await EmbeddedPostgres.start(config)
    alias = tmp_path / "alias"
    alias.symlink_to(config.data_dir, target_is_directory=True)
    try:
        with pytest.raises(RootBusy, match="embedded_directory_busy"):
            await EmbeddedPostgres.start(config.model_copy(update={"data_dir": alias}))
        source = acquisition(server.endpoint)
        await migrate(source, actor=Actor(id="fabricated"), schema="fabricated")
        assert server._processes.server is not None
        assert process(server).returncode is None
    finally:
        await closed(server)
    assert (config.data_dir / "PG_VERSION").is_file()


@pytest.mark.parametrize("state", ["unmanaged", "marker", "existing_server"])
async def test_unmanaged_corrupted_or_already_running_data_never_adopts(config, state):
    config.data_dir.mkdir()
    (config.data_dir / "PG_VERSION").write_bytes(b"18\n")
    if state != "unmanaged":
        marker = config.data_dir / ".tether-owner"
        marker.write_bytes(
            b"x" * 513 if state == "marker" else b'{"version":1,"profile":"postgres18"}'
        )
    if state == "existing_server":
        (config.data_dir / "postmaster.pid").write_bytes(b"fabricated-native-state")
    before = {p.name: p.read_bytes() for p in config.data_dir.iterdir()}
    with pytest.raises((IntegrityError, RootBusy)):
        await EmbeddedPostgres.start(config)
    await no_live(config)
    assert {p.name: p.read_bytes() for p in config.data_dir.iterdir()} == before


async def test_cancelled_startup_retains_actual_metadata_worker_and_directory_lock(
    config, monkeypatch
):
    entered, release = Event(), Event()
    original = module.prepare

    def blocked(value):
        files = original(value)
        entered.set()
        if not release.wait(5):
            raise RuntimeError("fabricated_startup_deadline")
        return files

    monkeypatch.setattr(module, "prepare", blocked)
    task = asyncio.create_task(EmbeddedPostgres.start(config))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RootBusy):
            await EmbeddedPostgres.start(config)
        assert any(server.config.data_dir == config.data_dir for server in module._LIVE)
    finally:
        release.set()
        await no_live(config)
    monkeypatch.setattr(module, "prepare", original)
    server = await EmbeddedPostgres.start(config)
    await closed(server)


async def test_failed_readiness_cleans_the_known_physical_process(config, monkeypatch):
    original = Processes.start
    processes: list[asyncio.subprocess.Process] = []

    async def fail(self, files, poll, user):
        await original(self, files, poll, user)
        native = self.server
        assert native is not None
        processes.append(native)
        raise StoreError("fabricated_readiness_failure")

    monkeypatch.setattr(Processes, "start", fail)
    with pytest.raises(StoreError, match="fabricated_readiness_failure"):
        await EmbeddedPostgres.start(config)
    await no_live(config)
    assert len(processes) == 1
    assert processes[0].returncode == 0
    assert (config.data_dir / "PG_VERSION").is_file()


async def test_close_waits_for_actual_source_release_after_caller_cancel(config):
    config = config.model_copy(update={"shutdown_seconds": 0.02})
    server = await EmbeddedPostgres.start(config)
    base = acquisition(server.endpoint)
    actor = Actor(id="fabricated")
    await migrate(base, actor=actor, schema="fabricated")
    entered, release = asyncio.Event(), asyncio.Event()
    armed = False

    @asynccontextmanager
    async def source(caller, sid):
        async with base(caller, sid) as connection:
            yield connection
        if armed:
            entered.set()
            await release.wait()

    root = await server.open_root(source, actor=actor, schema="fabricated")
    armed = True
    task = asyncio.create_task(root.run(actor, None, lambda c: c.execute("SELECT 1"), write=True))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await server.aclose()
        assert process(server).returncode is None
        with pytest.raises(RootBusy):
            await EmbeddedPostgres.start(config)
    finally:
        release.set()
        await closed(server)
    assert root.workers.closed
    assert (config.data_dir / "PG_VERSION").is_file()


async def test_failed_root_bootstrap_retains_unreturned_source_acquisition(config):
    config = config.model_copy(update={"shutdown_seconds": 0.02})
    server = await EmbeddedPostgres.start(config)
    base = acquisition(server.endpoint)
    actor = Actor(id="fabricated")
    await migrate(base, actor=actor, schema="fabricated")
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def source(caller, sid):
        entered.set()
        await release.wait()
        async with base(caller, sid) as connection:
            yield connection

    task = asyncio.create_task(
        server.open_root(
            source, actor=actor, schema="fabricated", limits=StorageLimits(io_seconds=0.2)
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        with pytest.raises(StoreError, match="database_unavailable"):
            await task
        assert entered.is_set()
        assert not server._roots
        assert not await server.aclose()
        assert process(server).returncode is None
        assert server._connections == 1
    finally:
        release.set()
        await closed(server)


async def test_close_retains_actual_worker_and_seals_registered_root_execution(config):
    config = config.model_copy(update={"shutdown_seconds": 0.02})
    server = await EmbeddedPostgres.start(config)
    source = acquisition(server.endpoint)
    actor = Actor(id="fabricated")
    await migrate(source, actor=actor, schema="fabricated")
    root = await server.open_root(source, actor=actor, schema="fabricated")
    entered, release = Event(), Event()

    def work() -> object:
        entered.set()
        if not release.wait(5):
            raise RuntimeError("fabricated_worker_deadline")
        return None

    future = asyncio.get_running_loop().run_in_executor(None, work)
    root.workers.retain("session", future)
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert not await server.aclose()
        assert root.workers.closed
        assert root.workers.busy("session")
        assert process(server).returncode is None
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(future), 3)
        await closed(server)


async def test_caller_acquisition_refusal_never_retries_with_other_authority(config):
    server = await EmbeddedPostgres.start(config)
    seen = []
    base = acquisition(server.endpoint)

    @asynccontextmanager
    async def refused(actor, sid):
        seen.append((actor.id, sid))
        if actor.id != "maintenance":
            raise ExecutionDenied("fabricated_denied")
        async with base(actor, sid) as connection:
            yield connection

    try:
        with pytest.raises(ExecutionDenied, match="fabricated_denied"):
            await server.open_root(refused, actor=Actor(id="caller"), schema="fabricated")
        assert seen == [("caller", None)]
    finally:
        await closed(server)


@pytest.mark.parametrize("operation", ["endpoint", "close"])
async def test_wrong_loop_refuses_before_server_lifetime_changes(config, operation):
    server = await EmbeddedPostgres.start(config)

    async def invoke():
        if operation == "endpoint":
            return server.endpoint
        return await server.aclose()

    def foreign():
        with pytest.raises(RuntimeError, match="wrong_event_loop"):
            asyncio.run(invoke())

    try:
        await asyncio.to_thread(foreign)
        assert not server._closing
        assert process(server).returncode is None
    finally:
        await closed(server)


async def test_shutdown_during_bootstrap_never_publishes_an_open_root(config):
    server = await EmbeddedPostgres.start(config.model_copy(update={"shutdown_seconds": 0.02}))
    base = acquisition(server.endpoint)
    actor = Actor(id="fabricated")
    await migrate(base, actor=actor, schema="fabricated")
    entered, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def source(caller, sid):
        async with base(caller, sid) as connection:
            entered.set()
            await release.wait()
            yield connection

    task = asyncio.create_task(server.open_root(source, actor=actor, schema="fabricated"))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not await server.aclose()
        release.set()
        with pytest.raises(StoreError, match="embedded_closed"):
            await task
        assert len(server._roots) == 1
        assert server._roots[0].workers.closed
        from tether.engine.workers import Workers

        workers = Workers(1)
        workers.scopes["fabricated"] = server._roots[0].workers
        effects = []
        with pytest.raises(ExecutionDenied, match="engine_closed"):
            await workers.call("fabricated", lambda: effects.append("forbidden"))
        assert not effects
        assert not workers.active
    finally:
        release.set()
        await closed(server)


@pytest.mark.parametrize("missing", [True, False])
async def test_stopped_server_releases_lock_even_when_runtime_cleanup_fails(
    config, monkeypatch, missing
):
    import shutil

    from tether.stores.postgres.embedded import files as files_module

    server = await EmbeddedPostgres.start(config)
    files = server._files
    assert files is not None
    original = shutil.rmtree
    if missing:
        original(files.runtime)
    else:

        def refused(path):
            raise OSError("fabricated_runtime_cleanup_failure")

        monkeypatch.setattr(files_module.shutil, "rmtree", refused)
    try:
        if missing:
            assert await server.aclose()
        else:
            with pytest.raises(OSError, match="fabricated_runtime_cleanup_failure"):
                await server.aclose()
        assert files.lock_fd is None
        assert server not in module._LIVE
        assert process(server).returncode == 0
    finally:
        monkeypatch.setattr(files_module.shutil, "rmtree", original)
    replacement = await EmbeddedPostgres.start(config)
    await closed(replacement)


async def test_replaced_database_directory_is_not_adopted(config, tmp_path):
    server = await EmbeddedPostgres.start(config)
    await closed(server)
    config.data_dir.rename(tmp_path / "original")
    config.data_dir.mkdir()
    (config.data_dir / "PG_VERSION").write_bytes(b"18\n")
    with pytest.raises(IntegrityError, match="embedded_unmanaged_directory"):
        await EmbeddedPostgres.start(config)
    await no_live(config)
    assert (tmp_path / "original" / ".tether-owner").is_file()
    assert not (config.data_dir / ".tether-owner").exists()


async def test_cancelled_real_initialization_finishes_before_directory_release(config, monkeypatch):
    original = Processes.spawn
    entered, release = asyncio.Event(), asyncio.Event()
    natives = []

    async def spawn(self, files, name, *args):
        native = await original(self, files, name, *args)
        if name == "initdb":
            natives.append(native)
            entered.set()
            await release.wait()
        return native

    monkeypatch.setattr(Processes, "spawn", spawn)
    task = asyncio.create_task(EmbeddedPostgres.start(config))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RootBusy):
            await EmbeddedPostgres.start(config)
    finally:
        release.set()
        await no_live(config)
    assert len(natives) == 1
    assert natives[0].returncode == 0
    assert not (config.data_dir / "postmaster.pid").exists()
    server = await EmbeddedPostgres.start(config)
    await closed(server)


async def test_cancelled_readiness_keeps_known_process_until_cleanup(config, monkeypatch):
    original = Processes.start
    entered, release = asyncio.Event(), asyncio.Event()
    natives = []

    async def start(self, files, poll, user):
        await original(self, files, poll, user)
        natives.append(self.server)
        entered.set()
        await release.wait()

    monkeypatch.setattr(Processes, "start", start)
    task = asyncio.create_task(EmbeddedPostgres.start(config))
    try:
        await asyncio.wait_for(entered.wait(), 15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RootBusy):
            await EmbeddedPostgres.start(config)
        assert natives[0].returncode is None
    finally:
        release.set()
        await no_live(config)
    assert natives[0].returncode == 0


async def test_root_capacity_and_explicit_migration(config):
    server = await EmbeddedPostgres.start(config.model_copy(update={"max_roots": 1}))
    source = acquisition(server.endpoint)
    actor = Actor(id="fabricated")
    try:
        with pytest.raises(IntegrityError):
            await server.open_root(source, actor=actor, schema="fabricated")
        await migrate(source, actor=actor, schema="fabricated")
        root = await server.open_root(source, actor=actor, schema="fabricated")
        with pytest.raises(StoreError, match="embedded_root_capacity"):
            await server.open_root(source, actor=actor, schema="fabricated")
        assert not root.workers.closed
    finally:
        await closed(server)


async def test_actual_initdb_failure_releases_ownership_without_adoption(config, monkeypatch):
    original = Processes.spawn
    natives = []

    async def spawn(self, files, name, *args):
        if name == "initdb":
            args = (*args, "--fabricated-invalid-option")
        native = await original(self, files, name, *args)
        natives.append(native)
        return native

    monkeypatch.setattr(Processes, "spawn", spawn)
    with pytest.raises(StoreError, match="embedded_initialization_failed"):
        await EmbeddedPostgres.start(config)
    await no_live(config)
    assert len(natives) == 1
    assert natives[0].returncode != 0
    assert not (config.data_dir / ".tether-owner").exists()
    monkeypatch.setattr(Processes, "spawn", original)
    server = await EmbeddedPostgres.start(config)
    await closed(server)
