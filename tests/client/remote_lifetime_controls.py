"""Owned task and host controls for remote turn lifetime tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from selected_profiles_process import await_retained
from tether_web_api import ApiSettings, create_api
from tui_event_waits import wait_for_event_or_operation

from tether.client import ClientHost, ClientSettings
from tether.client.remote import RemoteClient, RemoteClientSettings, RemoteTransportError
from tether.models.testing import ScriptedModel
from tether.types import ModelResponse


class SecondTurnHeldModel(ScriptedModel):
    def __init__(self) -> None:
        super().__init__(
            [
                ModelResponse(content="first", finish_reason="stop"),
                ModelResponse(content="second", finish_reason="stop"),
            ]
        )
        self.second_started = asyncio.Event()
        self.release_second = asyncio.Event()

    async def stream(self, request: Any) -> AsyncGenerator[Any, None]:
        if len(self.requests) == 1:
            self.second_started.set()
            await self.release_second.wait()
        async for item in super().stream(request):
            yield item


class TargetCancelThenHoldSuccessorModel(ScriptedModel):
    def __init__(self) -> None:
        super().__init__([ModelResponse(content="successor reply", finish_reason="stop")])
        self.target_started = asyncio.Event()
        self.successor_started = asyncio.Event()
        self.release_successor = asyncio.Event()
        self.calls = 0

    async def stream(self, request: Any) -> AsyncGenerator[Any, None]:
        self.calls += 1
        if self.calls == 1:
            self.target_started.set()
            await asyncio.Event().wait()
        self.successor_started.set()
        await self.release_successor.wait()
        async for item in super().stream(request):
            yield item


class LoseFirstTargetCancelResponse(httpx.AsyncBaseTransport):
    def __init__(self, app: Any) -> None:
        self._inner = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        self._lost = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if not self._lost and request.method == "DELETE" and "/turns/" in request.url.path:
            self._lost = True
            await response.aread()
            raise httpx.ReadError("fabricated_cancel_response_lost", request=request)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


@asynccontextmanager
async def _owned_host(settings: ClientSettings) -> AsyncGenerator[ClientHost, None]:
    host = await ClientHost.open(settings)
    primary: BaseException | None = None
    try:
        yield host
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = asyncio.create_task(_close_host(host))
        try:
            await await_retained(cleanup)
        except BaseException as cleanup_error:
            if primary is not None and cleanup_error is primary:
                pass
            elif primary is not None:
                raise BaseExceptionGroup(
                    "remote_host_test_and_cleanup_failed", [primary, cleanup_error]
                ) from primary
            else:
                raise


async def _close_host(host: ClientHost) -> None:
    errors: list[BaseException] = []
    try:
        if not await host.aclose():
            errors.append(AssertionError("remote_test_host_did_not_close"))
    except BaseException as error:
        errors.append(error)
    try:
        if not await host.wait_closed():
            errors.append(AssertionError("remote_test_host_did_not_drain"))
    except BaseException as error:
        errors.append(error)
    if errors:
        raise BaseExceptionGroup("remote_test_host_cleanup_failed", errors)


async def _wait_for_event_or_result(
    event: asyncio.Event, operation: asyncio.Task[Any], observed: set[asyncio.Task[Any]]
) -> None:
    try:
        await wait_for_event_or_operation(event, operation)
    except BaseException:
        if operation.done():
            observed.add(operation)
        raise


async def _observed_result(operation: asyncio.Task[Any], observed: set[asyncio.Task[Any]]) -> Any:
    try:
        result = await operation
    except BaseException:
        observed.add(operation)
        raise
    observed.add(operation)
    return result


async def _close_remote_turn_resources(
    remote: RemoteClient | None,
    releases: tuple[asyncio.Event, ...],
    observers: tuple[asyncio.Task[Any], ...],
    observed: set[asyncio.Task[Any]],
) -> None:
    errors: list[BaseException] = []
    for release in releases:
        release.set()
    for observer in observers:
        if not observer.done():
            # Stopping observation is not evidence that the accepted server turn was canceled.
            observer.cancel()
    outcomes = await asyncio.gather(*observers, return_exceptions=True)
    errors.extend(
        result
        for observer, result in zip(observers, outcomes, strict=True)
        if observer not in observed
        and isinstance(result, BaseException)
        and not isinstance(result, asyncio.CancelledError)
    )
    if remote is not None:
        try:
            await remote.aclose()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("remote_turn_cleanup_failed", errors)


async def run_stale_remote_cancellation(
    settings: ClientSettings,
    model: SecondTurnHeldModel,
    authenticator: Any,
    token_provider: Any,
) -> None:
    async with _owned_host(settings) as host:
        api = create_api(clients=host.client, authenticator=authenticator, settings=ApiSettings())
        transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
            remote: RemoteClient | None = None
            observers: list[asyncio.Task[Any]] = []
            observed: set[asyncio.Task[Any]] = set()
            primary: BaseException | None = None
            try:
                remote = await RemoteClient.open(
                    RemoteClientSettings("http://tether.test", token_provider),
                    http_client=http,
                )
                await _exercise_stale_cancellation(remote, model, observers, observed)
            except BaseException as error:
                primary = error
                raise
            else:
                primary = None
            finally:
                cleanup = asyncio.create_task(
                    _close_remote_turn_resources(
                        remote,
                        (model.release_second,),
                        tuple(observers),
                        observed,
                    )
                )
                try:
                    await await_retained(cleanup)
                except BaseException as cleanup_error:
                    if primary is not None and cleanup_error is primary:
                        pass
                    elif primary is not None:
                        raise BaseExceptionGroup(
                            "stale_cancel_test_and_cleanup_failed", [primary, cleanup_error]
                        ) from primary
                    else:
                        raise


async def _exercise_stale_cancellation(
    remote: RemoteClient,
    model: SecondTurnHeldModel,
    observers: list[asyncio.Task[Any]],
    observed: set[asyncio.Task[Any]],
) -> None:
    session = await remote.create(ref="stale-cancel")
    first = await remote.send(session.id, "first", request_id="turn-one")
    assert (await first.result()).stop_reason == "final"
    second = await remote.send(session.id, "second", request_id="turn-two")
    second_result = _start_result_observer(second.result(), observers)
    await _wait_for_event_or_result(model.second_started, second_result, observed)
    assert await remote.cancel_turn(session.id, first.turn_id) is False
    assert (await remote.snapshot(session.id)).view.summary.active_turn_id == second.turn_id
    model.release_second.set()
    result = await _observed_result(second_result, observed)
    assert result.messages[-1].content == "second"
    assert len(model.requests) == 2


async def run_lost_target_cancel_response_retry(
    settings: ClientSettings,
    model: TargetCancelThenHoldSuccessorModel,
    authenticator: Any,
    token_provider: Any,
) -> None:
    async with _owned_host(settings) as host:
        api = create_api(clients=host.client, authenticator=authenticator, settings=ApiSettings())
        transport = LoseFirstTargetCancelResponse(api)
        async with httpx.AsyncClient(transport=transport, base_url="http://tether.test") as http:
            remote: RemoteClient | None = None
            observers: list[asyncio.Task[Any]] = []
            observed: set[asyncio.Task[Any]] = set()
            primary: BaseException | None = None
            try:
                remote = await RemoteClient.open(
                    RemoteClientSettings("http://tether.test", token_provider),
                    http_client=http,
                )
                await _exercise_lost_cancel_retry(remote, model, observers, observed)
            except BaseException as error:
                primary = error
                raise
            else:
                primary = None
            finally:
                cleanup = asyncio.create_task(
                    _close_remote_turn_resources(
                        remote,
                        (model.release_successor,),
                        tuple(observers),
                        observed,
                    )
                )
                try:
                    await await_retained(cleanup)
                except BaseException as cleanup_error:
                    if primary is not None and cleanup_error is primary:
                        pass
                    elif primary is not None:
                        raise BaseExceptionGroup(
                            "lost_cancel_test_and_cleanup_failed", [primary, cleanup_error]
                        ) from primary
                    else:
                        raise


async def _exercise_lost_cancel_retry(
    remote: RemoteClient,
    model: TargetCancelThenHoldSuccessorModel,
    observers: list[asyncio.Task[Any]],
    observed: set[asyncio.Task[Any]],
) -> None:
    session = await remote.create(ref="lost-cancel-ack")
    target = await remote.send(session.id, "target", request_id="target-turn")
    target_result_task = _start_result_observer(target.result(), observers)
    await _wait_for_event_or_result(model.target_started, target_result_task, observed)
    with pytest.raises(RemoteTransportError, match="remote_transport_unavailable"):
        await remote.cancel_turn(session.id, target.turn_id)
    target_result = await _observed_result(target_result_task, observed)
    assert target_result.stop_reason == "cancelled"

    successor = await remote.send(session.id, "successor", request_id="next-turn")
    successor_result_task = _start_result_observer(successor.result(), observers)
    await _wait_for_event_or_result(model.successor_started, successor_result_task, observed)
    assert await remote.cancel_turn(session.id, target.turn_id) is False
    assert await remote.turn_result(session.id, target.turn_id) == target_result
    active = (await remote.snapshot(session.id)).view.summary.active_turn_id
    assert active == successor.turn_id
    assert model.calls == 2

    model.release_successor.set()
    successor_result = await _observed_result(successor_result_task, observed)
    assert successor_result.stop_reason == "final"
    assert successor_result.messages[-1].content == "successor reply"
    assert model.calls == 2


def _start_result_observer(
    result_coro: Any, observers: list[asyncio.Task[Any]]
) -> asyncio.Task[Any]:
    try:
        result_task = asyncio.create_task(result_coro)
    except BaseException:
        result_coro.close()
        raise
    observers.append(result_task)
    return result_task
