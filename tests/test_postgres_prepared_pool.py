"""Prepared async pools are published only after real startup validation."""

from __future__ import annotations

import asyncio
import sys
from typing import ClassVar

import pytest
from context_lifetime_controls import wait_event_or_task
from postgres_fixture_lifetime import DatabaseFixtureControl, database_context
from postgres_pending_io_controls import retain_cleanup
from postgres_prepared_pool import PreparedPoolOwner, TrackedAsyncConnectionPool
from postgres_support import postgres_server as postgres_server
from psycopg import AsyncConnection, OperationalError

from tether.stores.postgres import DatabaseRoot

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class HeldCloseConnection(AsyncConnection):
    close_entered: ClassVar[asyncio.Event | None] = None
    close_release: ClassVar[asyncio.Event | None] = None
    hold_close: ClassVar[bool] = False

    async def close(self) -> None:
        entered = type(self).close_entered
        release = type(self).close_release
        if type(self).hold_close and entered is not None and release is not None:
            entered.set()
            await release.wait()
        await super().close()


class FailOnceCloseConnection(AsyncConnection):
    failure: ClassVar[BaseException | None] = None

    async def close(self) -> None:
        error = type(self).failure
        if error is not None:
            type(self).failure = None
            raise error
        await super().close()


class HeldReturnPool(TrackedAsyncConnectionPool):
    connection_to_hold: ClassVar[AsyncConnection | None] = None
    return_entered: ClassVar[asyncio.Event | None] = None
    return_release: ClassVar[asyncio.Event | None] = None

    async def _after_connection_return(self, conn: AsyncConnection) -> None:
        if conn is type(self).connection_to_hold:
            type(self).connection_to_hold = None
            entered, release = type(self).return_entered, type(self).return_release
            assert entered is not None
            assert release is not None
            entered.set()
            await release.wait()


class FailOnceReturnPool(TrackedAsyncConnectionPool):
    failure: ClassVar[BaseException | None] = None

    async def _after_connection_return(self, conn: AsyncConnection) -> None:
        del conn
        error = type(self).failure
        if error is not None:
            type(self).failure = None
            raise error


async def test_rejected_real_configure_attempts_are_closed_before_retry(postgres_server):
    def fail_with(error):
        async def configure(_connection):
            raise error

        return configure

    for attempt in range(2):
        sentinel = OperationalError(f"fabricated_configure_failure_{attempt}")
        owner = PreparedPoolOwner(postgres_server.get_uri(), configure=fail_with(sentinel))
        with pytest.raises(OperationalError) as caught:
            await owner.prepare()
        assert caught.value is sentinel
        assert owner.pool.closed
        assert owner.pool.get_stats()["requests_waiting"] == 0
        assert owner.failed_connections
        assert all(connection.closed for connection in owner.failed_connections)
        assert owner.worker_refs_done


async def _assert_eight_borrowers_drain_before_close(owner):
    release = asyncio.Event()
    all_borrowed = asyncio.Event()
    pids: set[int] = set()
    entered = 0
    borrowers: list[asyncio.Task[None]] = []
    aggregate = None
    closing = None
    try:

        async def borrow_and_hold() -> None:
            nonlocal entered
            async with owner.pool.connection() as connection:
                cursor = await connection.execute("SELECT pg_backend_pid(), 1")
                row = await cursor.fetchone()
                assert row is not None
                assert row[1] == 1
                pids.add(row[0])
                entered += 1
                if entered == 8:
                    all_borrowed.set()
                await release.wait()

        borrowers.extend(asyncio.create_task(borrow_and_hold()) for _ in range(8))
        aggregate = asyncio.gather(*borrowers)
        await wait_event_or_task(all_borrowed, aggregate)
        assert len(pids) == 8
        assert entered == 8
        closing = asyncio.create_task(owner.aclose())
        await wait_event_or_task(owner.pool_close_entered, closing)
        assert owner.pool.closed
        assert len(owner._borrowed_connections) == 8
        assert not closing.done()
        release.set()
        await aggregate
        await closing
    finally:
        await retain_cleanup(
            _release_and_settle_borrowers(release, borrowers, closing), primary=sys.exception()
        )


async def _release_and_settle_borrowers(release, borrowers, closing) -> None:
    release.set()
    owners = [*borrowers]
    if closing is not None:
        owners.append(closing)
    outcomes = await asyncio.gather(*owners, return_exceptions=True)
    errors = [
        outcome
        for outcome in outcomes
        if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError)
    ]
    if errors:
        raise BaseExceptionGroup("borrower_control_cleanup_failed", errors)


async def test_ready_pool_serves_eight_distinct_real_connections(postgres_server):
    owner = None
    try:
        owner = await PreparedPoolOwner(postgres_server.get_uri()).prepare()
        await _assert_eight_borrowers_drain_before_close(owner)
    finally:

        async def teardown() -> None:
            if owner is not None:
                await owner.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_close_error_still_closes_all_owned_real_connections(postgres_server):
    sentinel = OperationalError("fabricated_native_close_failure")
    FailOnceCloseConnection.failure = None
    owner = None
    close_attempted = False
    try:
        owner = await PreparedPoolOwner(
            postgres_server.get_uri(), connection_class=FailOnceCloseConnection
        ).prepare()
        FailOnceCloseConnection.failure = sentinel
        close_attempted = True
        with pytest.raises(BaseExceptionGroup) as caught:
            await owner.aclose()
        assert caught.value.exceptions == (sentinel,)
        assert owner.pool.closed
        assert all(connection.closed for connection in owner.configured_connections)
        assert owner.worker_refs_done
    finally:
        FailOnceCloseConnection.failure = None
        if owner is not None and not close_attempted:
            await owner.aclose()


async def test_repeated_cancel_retains_real_pool_close_until_connection_exit(postgres_server):
    entered, release = asyncio.Event(), asyncio.Event()
    HeldCloseConnection.close_entered = entered
    HeldCloseConnection.close_release = release
    HeldCloseConnection.hold_close = False
    owner = None
    closing = None
    acknowledgements = (asyncio.Event(), asyncio.Event())
    try:
        owner = await PreparedPoolOwner(
            postgres_server.get_uri(), connection_class=HeldCloseConnection
        ).prepare()
        HeldCloseConnection.hold_close = True
        closing = asyncio.create_task(owner.aclose(cancellation_acks=acknowledgements))
        await wait_event_or_task(entered, closing)
        assert any(not connection.closed for connection in owner.configured_connections)
        closing.cancel()
        await wait_event_or_task(acknowledgements[0], closing)
        assert not closing.done()
        closing.cancel()
        await wait_event_or_task(acknowledgements[1], closing)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert all(connection.closed for connection in owner.configured_connections)
        assert owner.pool.closed
        assert owner.worker_refs_done
    finally:

        async def teardown() -> None:
            release.set()
            if closing is not None:
                if not closing.done():
                    closing.cancel()
                await asyncio.gather(closing, return_exceptions=True)
            if owner is not None:
                await owner.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())
        HeldCloseConnection.hold_close = False
        HeldCloseConnection.close_entered = None
        HeldCloseConnection.close_release = None


async def _prove_raw_pool_handoff(owner, entered, release) -> None:
    pool = owner.pool
    connections: list[AsyncConnection] = []
    returning = None
    try:
        for _ in range(8):
            connections.append(await pool.getconn())
        selected = connections[0]
        HeldReturnPool.connection_to_hold = selected
        returning = asyncio.create_task(pool.putconn(selected))
        await wait_event_or_task(entered, returning)
        second = await pool.getconn()
        assert second is selected
        selected_loans = [loan for loan in pool.loans if loan.connection is selected]
        assert len(selected_loans) == 2
        assert sum(loan.returning for loan in selected_loans) == 1
        release.set()
        await returning
        selected_loans = [loan for loan in pool.loans if loan.connection is selected]
        assert len(selected_loans) == 1
        assert not selected_loans[0].returning
        await pool.putconn(second)
        connections.remove(selected)
        for connection in connections:
            await pool.putconn(connection)
        assert not pool.loans
        connections.clear()
    finally:

        async def teardown() -> None:
            release.set()
            errors = []
            if returning is not None:
                outcomes = await asyncio.gather(returning, return_exceptions=True)
                errors.extend(outcome for outcome in outcomes if isinstance(outcome, BaseException))
            for connection in connections:
                if any(loan.connection is connection for loan in pool.loans):
                    try:
                        await pool.putconn(connection)
                    except BaseException as error:
                        errors.append(error)
            if errors:
                raise BaseExceptionGroup("raw_pool_handoff_cleanup_failed", errors)

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_public_pool_return_keeps_distinct_handoff_loans(postgres_server):
    entered, release = asyncio.Event(), asyncio.Event()
    HeldReturnPool.connection_to_hold = None
    HeldReturnPool.return_entered = entered
    HeldReturnPool.return_release = release
    owner = await PreparedPoolOwner(postgres_server.get_uri(), pool_class=HeldReturnPool).prepare()
    try:
        await _prove_raw_pool_handoff(owner, entered, release)
    finally:

        async def teardown() -> None:
            release.set()
            await owner.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())
        HeldReturnPool.connection_to_hold = None
        HeldReturnPool.return_entered = None
        HeldReturnPool.return_release = None


async def test_failed_public_pool_return_releases_only_its_loan(postgres_server):
    sentinel = OperationalError("fabricated_pool_return_failure")
    FailOnceReturnPool.failure = None
    owner = await PreparedPoolOwner(
        postgres_server.get_uri(), pool_class=FailOnceReturnPool
    ).prepare()
    connection = None
    returned = False
    try:
        FailOnceReturnPool.failure = sentinel

        async def use_raw_context() -> None:
            nonlocal connection
            async with owner.pool.connection() as acquired:
                connection = acquired
                cursor = await acquired.execute("SELECT 1")
                assert await cursor.fetchone() == (1,)

        with pytest.raises(OperationalError) as caught:
            await use_raw_context()
        assert caught.value is sentinel
        returned = True
        assert not any(loan.connection is connection for loan in owner.pool.loans)
    finally:

        async def teardown() -> None:
            FailOnceReturnPool.failure = None
            if owner.pool.loans:
                await owner.wait_for_borrowers()
            await owner.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())
    assert returned
    assert connection is not None
    assert connection.closed


async def test_database_context_setup_failure_drops_its_planned_schema(postgres_server):
    sentinel = OperationalError("fabricated_root_open_failure")
    control = DatabaseFixtureControl()

    async def fail_root_open(*_args, **_kwargs):
        raise sentinel

    with pytest.raises(OperationalError) as caught:
        async with database_context(
            postgres_server.get_uri(), control=control, root_open=fail_root_open
        ):
            raise AssertionError("failed_setup_must_not_yield")
    assert caught.value is sentinel
    assert control.schema is not None
    assert control.owner is not None
    assert control.owner.pool.pool.closed
    assert control.owner.pool.worker_refs_done
    await _assert_schema_absent(postgres_server.get_uri(), control.schema)


async def test_database_context_retains_held_bootstrap_source_on_cancellation(postgres_server):
    control = DatabaseFixtureControl(hold_source_call=2)

    async def open_and_unexpectedly_yield() -> None:
        async with database_context(postgres_server.get_uri(), control=control):
            raise AssertionError("cancelled_setup_must_not_yield")

    operation = asyncio.create_task(open_and_unexpectedly_yield())
    try:
        await wait_event_or_task(control.source_entered, operation)
        assert control.owner is not None
        owner = control.owner
        assert len(owner.pool._borrowed_connections) == 1
        operation.cancel()
        await wait_event_or_task(control.setup_cancel_ack, operation)
        assert not operation.done()
        assert not owner.pool.pool.closed
        assert len(owner.pool._borrowed_connections) == 1
        control.source_release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert owner.root is not None
        assert owner.pool.pool.closed
        assert owner.pool.worker_refs_done
        assert not owner.source._invocations
        assert not owner.pool._borrowed_connections
        await _assert_schema_absent(postgres_server.get_uri(), owner.schema)
    finally:

        async def teardown() -> None:
            control.source_release.set()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if control.owner is not None:
                await control.owner.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())


async def test_cancelled_fixture_setup_retains_late_root_failure(postgres_server):
    sentinel = OperationalError("fabricated_late_root_failure")
    control = DatabaseFixtureControl(hold_source_call=2)

    async def close_then_fail(source, *, actor, schema):
        root = await DatabaseRoot.open(source, actor=actor, schema=schema)
        await root.aclose()
        assert await root.wait_closed()
        raise sentinel

    async def open_and_unexpectedly_yield() -> None:
        async with database_context(
            postgres_server.get_uri(), control=control, root_open=close_then_fail
        ):
            raise AssertionError("failed_setup_must_not_yield")

    operation = asyncio.create_task(open_and_unexpectedly_yield())
    try:
        await wait_event_or_task(control.source_entered, operation)
        operation.cancel()
        await wait_event_or_task(control.setup_cancel_ack, operation)
        assert not operation.done()
        control.source_release.set()
        with pytest.raises(BaseExceptionGroup) as caught:
            await operation
        assert len(caught.value.exceptions) == 2
        assert isinstance(caught.value.exceptions[0], asyncio.CancelledError)
        assert caught.value.exceptions[1] is sentinel
        assert control.owner is not None
        assert control.owner.pool.pool.closed
        assert control.owner.pool.worker_refs_done
        assert not control.owner.pool._borrowed_connections
        await _assert_schema_absent(postgres_server.get_uri(), control.schema)
    finally:

        async def teardown() -> None:
            control.source_release.set()
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            if control.owner is not None:
                await control.owner.aclose()

        await retain_cleanup(teardown(), primary=sys.exception())


async def _assert_schema_absent(conninfo: str, schema: str | None) -> None:
    assert schema is not None
    async with await AsyncConnection.connect(conninfo) as connection:
        cursor = await connection.execute("SELECT to_regnamespace(%s)", (schema,))
        assert await cursor.fetchone() == (None,)
