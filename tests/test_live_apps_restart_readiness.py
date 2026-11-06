"""The postrestart qualifier retries only bounded capability readiness reads."""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, cast

import pytest
from qualify_live_apps_config import LiveSettings
from qualify_live_apps_transport import _client_for_restart_readiness

from tether.client.remote import RemoteProblem, RemoteTransportError


def _settings() -> LiveSettings:
    return LiveSettings("fabricated-app", "https://apps.example", "/prefix", "/api/v1")


class _Http:
    instances: ClassVar[list[_Http]] = []

    def __init__(self, **_kwargs: object) -> None:
        self.closed = False
        self.instances.append(self)

    async def aclose(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, remote: type) -> None:
    import qualify_live_apps_transport as transport

    _Http.instances.clear()
    monkeypatch.setattr(transport.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(transport, "RemoteClient", remote)


async def _wait_for_event_or_task(event: asyncio.Event, task: asyncio.Task[Any]) -> None:
    waiter = asyncio.create_task(event.wait())
    done, _pending = await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        task.result()
        raise AssertionError("readiness_operation_completed_before_control_barrier")


def test_readiness_retries_transient_capabilities_and_closes_each_failed_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import qualify_live_apps_transport as transport

    class Remote:
        statuses: ClassVar[list[int | None]] = [502, 503, 502, 503, None]
        opens = 0

        @classmethod
        async def open(cls, *_args: object, **_kwargs: object) -> object:
            cls.opens += 1
            status = cls.statuses.pop(0)
            if status is not None:
                raise RemoteProblem(status, "remote_request_failed")
            return cls()

    _install(monkeypatch, Remote)
    monkeypatch.setattr(transport, "_RESTART_RETRY_INITIAL_SECONDS", 0.0)

    async def run() -> None:
        remote, http = await _client_for_restart_readiness(
            _settings(), cast(Any, object()), service_caller=False
        )
        assert isinstance(remote, Remote)
        assert http is _Http.instances[-1]
        await http.aclose()

    asyncio.run(run())
    assert Remote.opens == 5
    assert [http.closed for http in _Http.instances] == [True] * 5


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500], ids=lambda status: f"http-{status}")
def test_readiness_does_not_retry_nontransient_http_status(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    class Remote:
        opens = 0

        @classmethod
        async def open(cls, *_args: object, **_kwargs: object) -> object:
            cls.opens += 1
            raise RemoteProblem(status, "remote_request_failed")

    _install(monkeypatch, Remote)

    async def run() -> None:
        with pytest.raises(RemoteProblem) as caught:
            await _client_for_restart_readiness(
                _settings(), cast(Any, object()), service_caller=False
            )
        assert caught.value.status == status

    asyncio.run(run())
    assert Remote.opens == 1
    assert len(_Http.instances) == 1
    assert _Http.instances[0].closed


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (RemoteTransportError, "remote_transport_unavailable"),
        (ValueError, "fixed_protocol_failure"),
    ],
    ids=["transport-error", "decode-error"],
)
def test_readiness_does_not_retry_non_http_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: type[Exception],
    message: str,
) -> None:
    class Remote:
        opens = 0

        @classmethod
        async def open(cls, *_args: object, **_kwargs: object) -> object:
            cls.opens += 1
            raise failure(message)

    _install(monkeypatch, Remote)

    async def run() -> None:
        with pytest.raises(failure, match=message):
            await _client_for_restart_readiness(
                _settings(), cast(Any, object()), service_caller=False
            )

    asyncio.run(run())
    assert Remote.opens == 1
    assert len(_Http.instances) == 1
    assert _Http.instances[0].closed


def test_readiness_retries_until_capabilities_are_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    import qualify_live_apps_transport as transport

    real_sleep = asyncio.sleep

    class Remote:
        statuses: ClassVar[list[int | None]] = [502, 502, 503, 502, None]
        opens = 0

        @classmethod
        async def open(cls, *_args: object, **_kwargs: object) -> object:
            cls.opens += 1
            status = cls.statuses.pop(0)
            if status is not None:
                raise RemoteProblem(status, "remote_request_failed")
            return cls()

    _install(monkeypatch, Remote)
    monkeypatch.setattr(transport, "_RESTART_RETRY_INITIAL_SECONDS", 0.0)

    async def yield_control(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(transport.asyncio, "sleep", yield_control)

    async def run() -> None:
        remote, http = await _client_for_restart_readiness(
            _settings(), cast(Any, object()), service_caller=False
        )
        assert isinstance(remote, Remote)
        assert http is _Http.instances[-1]
        await http.aclose()

    asyncio.run(run())
    assert Remote.opens == 5
    assert [http.closed for http in _Http.instances] == [True] * 5


def test_readiness_propagates_cancellation_and_closes_owned_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Remote:
        entered = asyncio.Event()

        @classmethod
        async def open(cls, *_args: object, **_kwargs: object) -> object:
            cls.entered.set()
            await asyncio.Event().wait()

    _install(monkeypatch, Remote)

    async def run() -> None:
        task = asyncio.create_task(
            _client_for_restart_readiness(_settings(), cast(Any, object()), service_caller=False)
        )
        await _wait_for_event_or_task(Remote.entered, task)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(_Http.instances) == 1
    assert _Http.instances[0].closed
