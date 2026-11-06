from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from tether_embed_example.app import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_public_embedder_constructs_host_and_serves_fabricated_turn(tmp_path: Path) -> None:
    app, host = await create_app(tmp_path / "journal")
    try:
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

            accepted = await client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"request_id": "fabricated-send", "text": "hello"},
            )
            assert accepted.status_code == 202
            result_url = accepted.json()["result_url"]
            result = None
            for _attempt in range(100):
                response = await client.get(result_url)
                assert response.status_code == 200
                result = response.json()
                if result is not None:
                    break
                await asyncio.sleep(0.01)

        assert result is not None
        assert result["messages"][-1]["content"] == "Fabricated embedder response."
    finally:
        await host.aclose()
