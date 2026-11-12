"""Embedded ownership follows physical startup, acquisitions and worker exit."""

import asyncio
import sys
from contextlib import asynccontextmanager
from threading import Event

import pytest
from context_lifetime_controls import wait_event_or_task
from embedded_lifetime_controls import (
    RetainedCancellationControl,
    arm_captured_timeout,
    cancel_retained_waiter_twice,
    close_embedded,
    close_matching,
    owned_server,
    settle_embedded_server,
)
from process_lifetime_controls import await_retained
from test_embedded_postgres import acquisition
from test_embedded_postgres import config as config
from test_embedded_postgres import pending_io_control as pending_io_control

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
    await close_matching(config.data_dir, sys.exception())


async def closed(server):
    return await close_embedded(server)


async def test_duplicate_canonical_data_owner_refuses_even_with_other_runtime(config, tmp_path):
    async with owned_server(config) as server:
        alias = tmp_path / "alias"
        alias.symlink_to(config.data_dir, target_is_directory=True)
        with pytest.raises(RootBusy, match="embedded_directory_busy"):
            await EmbeddedPostgres.start(config.model_copy(update={"data_dir": alias}))
        source = acquisition(server.endpoint)
        await migrate(source, actor=Actor(id="fabricated"), schema="fabricated")
        assert server._processes.server is not None
        assert process(server).returncode is None
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
    if state == "existing_server":
        (config.data_dir / "postmaster.pid").unlink()


async def test_cancelled_startup_retains_actual_metadata_worker_and_directory_lock(
    config, monkeypatch
):
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    original = module.prepare

    def blocked(value):
        files = original(value)
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return files

    monkeypatch.setattr(module, "prepare", blocked)
    task = None
    try:
        task = asyncio.create_task(EmbeddedPostgres.start(config))
        await wait_event_or_task(entered, task)
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
    async with owned_server(config):
        pass


async def test_startup_timeout_expires_only_after_metadata_phase_entry(
    config, monkeypatch, pending_io_control
):
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    original = module.prepare

    def blocked(value):
        files = original(value)
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return files

    monkeypatch.setattr(module, "prepare", blocked)
    task = None
    try:
        task = asyncio.create_task(EmbeddedPostgres.start(config))
        await wait_event_or_task(entered, task)
        timer = arm_captured_timeout(pending_io_control, config.startup_seconds)
        with pytest.raises(StoreError, match="database_unavailable"):
            await task
        assert timer.expired()
        owner = next(server for server in module._LIVE if server.config.data_dir == config.data_dir)
        assert owner._close_task is not None
        assert not owner._close_task.done()
        with pytest.raises(RootBusy):
            await EmbeddedPostgres.start(config)
    finally:
        release.set()
        await no_live(config)
    monkeypatch.setattr(module, "prepare", original)
    assert not (config.data_dir / "PG_VERSION").exists()


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
    try:
        with pytest.raises(StoreError, match="fabricated_readiness_failure"):
            await EmbeddedPostgres.start(config)
    finally:
        await no_live(config)
    assert len(processes) == 1
    assert processes[0].returncode == 0
    assert (config.data_dir / "PG_VERSION").is_file()


async def test_close_waits_for_actual_source_release_after_caller_cancel(config):
    config = config.model_copy(update={"shutdown_seconds": 0.02})
    release = asyncio.Event()
    server = await EmbeddedPostgres.start(config)
    task = None
    root = None
    armed = False
    entered = asyncio.Event()
    try:
        base = acquisition(server.endpoint)
        actor = Actor(id="fabricated")
        await migrate(base, actor=actor, schema="fabricated")

        @asynccontextmanager
        async def source(caller, sid):
            async with base(caller, sid) as connection:
                yield connection
            if armed:
                entered.set()
                await release.wait()

        root = await server.open_root(source, actor=actor, schema="fabricated")
        armed = True
        task = asyncio.create_task(
            root.run(actor, None, lambda c: c.execute("SELECT 1"), write=True)
        )
        await wait_event_or_task(entered, task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not await server.aclose()
        assert process(server).returncode is None
        with pytest.raises(RootBusy):
            await EmbeddedPostgres.start(config)
    finally:
        await settle_embedded_server(
            server,
            release.set,
            waiters=(task,) if task is not None else (),
            expected_cancellation=(task,) if task is not None and task.cancelling() else (),
            primary=sys.exception(),
        )
    assert root is not None
    assert root.workers.closed
    assert (config.data_dir / "PG_VERSION").is_file()


async def _start_held_root_operation(server, release, entered):
    base = acquisition(server.endpoint)
    actor = Actor(id="fabricated")
    await migrate(base, actor=actor, schema="fabricated")
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
    operation = asyncio.create_task(
        root.run(actor, None, lambda connection: connection.execute("SELECT 1"), write=True)
    )
    await wait_event_or_task(entered, operation)
    return root, operation


async def test_server_close_task_survives_repeated_waiter_cancellation(config, monkeypatch):
    release = asyncio.Event()
    server = await EmbeddedPostgres.start(config)
    operation = None
    close_waiter = None
    close_task = None
    entered = asyncio.Event()
    try:
        _root, operation = await _start_held_root_operation(server, release, entered)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        server._seal()
        close_task = server._ensure_close()
        control = RetainedCancellationControl()
        control.install(monkeypatch)
        close_waiter = asyncio.create_task(await_retained(close_task))
        await cancel_retained_waiter_twice(close_waiter, close_task, control)
        assert process(server).returncode is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await close_waiter
        assert close_task.done()
        assert process(server).returncode == 0
    finally:
        waiters = tuple(waiter for waiter in (operation, close_waiter) if waiter is not None)
        cancelled = tuple(waiter for waiter in waiters if waiter.cancelling())
        await settle_embedded_server(
            server, release.set, waiters, cancelled, primary=sys.exception()
        )


async def test_failed_root_bootstrap_retains_unreturned_source_acquisition(
    config, pending_io_control
):
    config = config.model_copy(update={"shutdown_seconds": 0.02})
    server = await EmbeddedPostgres.start(config)
    entered, release = asyncio.Event(), asyncio.Event()
    actual = None
    task = None
    try:
        base = acquisition(server.endpoint)
        actor = Actor(id="fabricated")
        await migrate(base, actor=actor, schema="fabricated")

        @asynccontextmanager
        async def source(caller, sid):
            nonlocal actual
            actual = asyncio.current_task()
            entered.set()
            await release.wait()
            async with base(caller, sid) as connection:
                yield connection

        task = asyncio.create_task(
            server.open_root(
                source, actor=actor, schema="fabricated", limits=StorageLimits(io_seconds=0.2)
            )
        )
        await wait_event_or_task(entered, task)
        timer = arm_captured_timeout(pending_io_control, 0.2)
        with pytest.raises(StoreError, match="database_unavailable"):
            await task
        assert timer.expired()
        assert entered.is_set()
        assert not server._roots
        assert not await server.aclose()
        assert process(server).returncode is None
        assert server._connections == 1
    finally:
        await settle_embedded_server(server, release.set, primary=sys.exception())
    assert actual is not None
    assert actual.done()


async def test_close_retains_actual_worker_and_seals_registered_root_execution(config):
    config = config.model_copy(update={"shutdown_seconds": 0.02})
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    server = await EmbeddedPostgres.start(config)
    future = None
    root = None

    def work() -> object:
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return None

    try:
        source = acquisition(server.endpoint)
        actor = Actor(id="fabricated")
        await migrate(source, actor=actor, schema="fabricated")
        root = await server.open_root(source, actor=actor, schema="fabricated")
        future = asyncio.get_running_loop().run_in_executor(None, work)
        root.workers.retain("session", future)
        await wait_event_or_task(entered, future)
        assert not await server.aclose()
        assert root.workers.closed
        assert root.workers.busy("session")
        assert process(server).returncode is None
    finally:
        await settle_embedded_server(
            server,
            release.set,
            waiters=(future,) if future is not None else (),
            primary=sys.exception(),
        )
    assert root is not None


async def test_caller_acquisition_refusal_never_retries_with_other_authority(config):
    async with owned_server(config) as server:
        seen = []
        base = acquisition(server.endpoint)

        @asynccontextmanager
        async def refused(actor, sid):
            seen.append((actor.id, sid))
            if actor.id != "maintenance":
                raise ExecutionDenied("fabricated_denied")
            async with base(actor, sid) as connection:
                yield connection

        with pytest.raises(ExecutionDenied, match="fabricated_denied"):
            await server.open_root(refused, actor=Actor(id="caller"), schema="fabricated")
        assert seen == [("caller", None)]


@pytest.mark.parametrize("operation", ["endpoint", "close"])
async def test_wrong_loop_refuses_before_server_lifetime_changes(config, operation):
    async with owned_server(config) as server:

        async def invoke():
            if operation == "endpoint":
                return server.endpoint
            return await server.aclose()

        def foreign():
            with pytest.raises(RuntimeError, match="wrong_event_loop"):
                asyncio.run(invoke())

        await asyncio.to_thread(foreign)
        assert not server._closing
        assert process(server).returncode is None


async def test_shutdown_during_bootstrap_never_publishes_an_open_root(config):
    entered, release = asyncio.Event(), asyncio.Event()
    server = await EmbeddedPostgres.start(config.model_copy(update={"shutdown_seconds": 0.02}))
    task = None

    try:
        base = acquisition(server.endpoint)
        actor = Actor(id="fabricated")
        await migrate(base, actor=actor, schema="fabricated")

        @asynccontextmanager
        async def source(caller, sid):
            async with base(caller, sid) as connection:
                entered.set()
                await release.wait()
                yield connection

        task = asyncio.create_task(server.open_root(source, actor=actor, schema="fabricated"))
        await wait_event_or_task(entered, task)
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
        await settle_embedded_server(server, release.set, primary=sys.exception())


@pytest.mark.parametrize("missing", [True, False])
async def test_stopped_server_releases_lock_even_when_runtime_cleanup_fails(
    config, monkeypatch, missing
):
    import shutil

    from tether.stores.postgres.embedded import files as files_module

    original = shutil.rmtree
    server = await EmbeddedPostgres.start(config)
    try:
        files = server._files
        assert files is not None
        if missing:
            original(files.runtime)
        else:

            def refused(path):
                raise OSError("fabricated_runtime_cleanup_failure")

            monkeypatch.setattr(files_module.shutil, "rmtree", refused)
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
        if server._close_task is None or not server._close_task.done():
            await closed(server)
    async with owned_server(config):
        pass


async def test_replaced_database_directory_is_not_adopted(config, tmp_path):
    async with owned_server(config):
        pass
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
    task = None
    try:
        task = asyncio.create_task(EmbeddedPostgres.start(config))
        await wait_event_or_task(entered, task)
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
    async with owned_server(config):
        pass


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
    task = None
    try:
        task = asyncio.create_task(EmbeddedPostgres.start(config))
        await wait_event_or_task(entered, task)
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
    async with owned_server(config.model_copy(update={"max_roots": 1})) as server:
        source = acquisition(server.endpoint)
        actor = Actor(id="fabricated")
        with pytest.raises(IntegrityError):
            await server.open_root(source, actor=actor, schema="fabricated")
        await migrate(source, actor=actor, schema="fabricated")
        root = await server.open_root(source, actor=actor, schema="fabricated")
        with pytest.raises(StoreError, match="embedded_root_capacity"):
            await server.open_root(source, actor=actor, schema="fabricated")
        assert not root.workers.closed


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
    try:
        with pytest.raises(StoreError, match="embedded_initialization_failed"):
            await EmbeddedPostgres.start(config)
    finally:
        await no_live(config)
    assert len(natives) == 1
    assert natives[0].returncode != 0
    assert not (config.data_dir / ".tether-owner").exists()
    monkeypatch.setattr(Processes, "spawn", original)
    async with owned_server(config):
        pass
