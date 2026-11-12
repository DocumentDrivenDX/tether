from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import cast

import httpx
import pytest
from tether_embed_example.app import create_app

from tether.client import Actor, Change, EntriesAppended


class _RetainedCleanup:
    def __init__(self) -> None:
        self._closers: list[tuple[Callable[[], Awaitable[object]], ...]] = []

    def add(self, *steps: Callable[[], Awaitable[object]]) -> None:
        self._closers.insert(0, steps)

    async def __aenter__(self) -> _RetainedCleanup:
        return self

    async def __aexit__(self, exc_type, primary, _traceback) -> bool:
        del exc_type
        cleanup = asyncio.create_task(self._close_all())
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
            except BaseException:
                break
        cleanup_error = None
        try:
            cleanup.result()
        except BaseException as error:
            cleanup_error = error
        if primary is not None and cleanup_error is not None:
            raise BaseExceptionGroup(
                "embed_test_resource_and_cleanup_failed", [primary, cleanup_error]
            ) from primary
        if cleanup_error is not None:
            raise cleanup_error
        if cancelled and primary is None:
            raise asyncio.CancelledError
        return False

    async def _close_all(self) -> None:
        errors: list[BaseException] = []
        for steps in self._closers:
            for index, step in enumerate(steps):
                try:
                    result = await step()
                    if result is False and index == len(steps) - 1:
                        errors.append(AssertionError("embed_test_resource_drain_incomplete"))
                except BaseException as error:
                    errors.append(error)
        if errors:
            raise BaseExceptionGroup("embed_test_resources_cleanup_failed", errors)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_public_embedder_constructs_host_and_serves_fabricated_turn(tmp_path: Path) -> None:
    async with _RetainedCleanup() as cleanup:
        app, host = await create_app(tmp_path / "journal")
        cleanup.add(host.aclose, host.wait_closed)
        local_client = host.client(Actor(id="fabricated-local-user"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://example.test"
        ) as client:
            page = await client.get("/")
            assert page.status_code == 200
            assert "Tether" in page.text

            discovery = await client.get("/.tether/config.json")
            assert discovery.status_code == 200
            assert discovery.json() == {
                "base_path": "",
                "api_discovery_path": "/api/v1/capabilities",
            }

            created = await client.post(
                "/api/v1/sessions",
                json={"request_id": "fabricated-create", "spec": "demo"},
            )
            assert created.status_code == 201
            session_id = created.json()["id"]
            baseline = await local_client.snapshot(session_id)
            stream = cast(
                AsyncGenerator[Change, None],
                local_client.follow(session_id=session_id, since=baseline.seq),
            )
            cleanup.add(stream.aclose)

            accepted = await client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"request_id": "fabricated-send", "text": "hello"},
            )
            assert accepted.status_code == 202
            accepted_body = accepted.json()
            await wait_for_committed_turn(stream, session_id, accepted_body["turn_id"])
            response = await client.get(accepted_body["result_url"])
            assert response.status_code == 200
            result = response.json()

        assert result is not None
        assert result["messages"][-1]["content"] == "Fabricated embedder response."


async def wait_for_committed_turn(
    stream: AsyncGenerator[Change, None], session_id: str, turn_id: str
) -> None:
    async for event in stream:
        if (
            isinstance(event, EntriesAppended)
            and event.last_in_commit
            and event.session_id == session_id
            and any(
                entry.kind == "turn_ended" and entry.turn_id == turn_id for entry in event.entries
            )
        ):
            return
    raise AssertionError("committed_turn_feed_ended_before_terminal")
