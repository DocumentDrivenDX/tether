from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from benchmark_apps import measure_remote_reads

from tether.client import Page, SessionSummary
from tether.client.remote import RemoteClient, RemoteClientSettings, RemoteProblem


class FabricatedCredential:
    async def authorization(self) -> str:
        return " ".join(("Bearer", "fabricated"))


async def credential() -> FabricatedCredential:
    return FabricatedCredential()


def test_remote_samples_use_actual_gets_and_keep_borrowed_transport_open() -> None:
    requests: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.headers["authorization"] == " ".join(("Bearer", "fabricated"))
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={"methods": {}, "tiers": {"live": "available", "archive": "unavailable"}},
            )
        assert request.url.params["limit"] == "50"
        return httpx.Response(200, json=Page[SessionSummary](items=()).model_dump(mode="json"))

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://apps.example.test", transport=httpx.MockTransport(respond)
        ) as http:
            result = await measure_remote_reads(
                RemoteClientSettings("https://apps.example.test", credential),
                http_client=http,
                samples=3,
            )
            assert not http.is_closed
        assert result["capability_requests"] == result["list_requests"] == 3
        assert result["capability_handshake"]["sample_count"] == 3
        assert result["list_page"]["sample_count"] == 3
        assert result["list_items_per_page"] == 0

    asyncio.run(run())
    assert requests == [("GET", "/api/v1/capabilities")] * 3 + [("GET", "/api/v1/sessions")] * 3


def test_denied_sample_refuses_result_without_retry_or_transport_ownership() -> None:
    calls = 0

    def denied(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, json={"type": "forbidden", "title": "Forbidden"})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://apps.example.test", transport=httpx.MockTransport(denied)
        ) as http:
            with pytest.raises(RemoteProblem):
                await measure_remote_reads(
                    RemoteClientSettings("https://apps.example.test", credential),
                    http_client=http,
                    samples=3,
                )
            assert not http.is_closed

    asyncio.run(run())
    assert calls == 1


def test_cancelled_sample_keeps_borrowed_transport_and_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    closed_views: list[RemoteClient] = []
    close = RemoteClient.aclose

    async def record_close(client: RemoteClient) -> None:
        await close(client)
        closed_views.append(client)

    monkeypatch.setattr(RemoteClient, "aclose", record_close)

    async def held(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={"methods": {}, "tiers": {"live": "available", "archive": "unavailable"}},
            )
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://apps.example.test", transport=httpx.MockTransport(held)
        ) as http:
            task = asyncio.create_task(
                measure_remote_reads(
                    RemoteClientSettings("https://apps.example.test", credential),
                    http_client=http,
                    samples=1,
                )
            )
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not http.is_closed
            assert len(closed_views) == 1

    asyncio.run(run())


def test_transport_origin_mismatch_refuses_before_any_request() -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError("request_must_not_be_dispatched")

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://other.example.test", transport=httpx.MockTransport(unexpected)
        ) as http:
            with pytest.raises(ValueError, match=r"^remote_read_transport_origin_mismatch$"):
                await measure_remote_reads(
                    RemoteClientSettings("https://apps.example.test", credential),
                    http_client=http,
                )

    asyncio.run(run())


@pytest.mark.parametrize("counts", [(0, 1), (51, 51)], ids=["changed", "exceeds-limit"])
def test_list_cardinality_change_or_excess_refuses_samples(counts: tuple[int, int]) -> None:
    lists = 0
    now = datetime.now(UTC)
    item = SessionSummary(id="fabricated", owner="fabricated", created_at=now, updated_at=now)

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal lists
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(
                200,
                json={"methods": {}, "tiers": {"live": "available", "archive": "unavailable"}},
            )
        count = counts[lists]
        lists += 1
        return httpx.Response(
            200, json=Page[SessionSummary](items=(item,) * count).model_dump(mode="json")
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="https://apps.example.test", transport=httpx.MockTransport(respond)
        ) as http:
            with pytest.raises(RuntimeError, match="remote_read_list_cardinality_changed"):
                await measure_remote_reads(
                    RemoteClientSettings("https://apps.example.test", credential),
                    http_client=http,
                    samples=2,
                )
            assert not http.is_closed

    asyncio.run(run())
