"""Public static hosting, isolated from Tether and private runtime state."""

import asyncio
import hashlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path

import httpx
from tether_web_ui import UiSettings, create_static_app


def assets(root: Path) -> None:
    root.mkdir(exist_ok=True)
    inventory = {
        "index.html": (
            b'<html><head><base href="__TETHER_BASE__"></head><body>Workspace</body></html>'
        ),
        "assets/app-abcdefgh.js": b'console.log("fabricated")',
    }
    for name, value in inventory.items():
        target = root / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(value)
    (root / "tether-assets.json").write_text(
        json.dumps({key: hashlib.sha256(value).hexdigest() for key, value in inventory.items()})
    )


def test_deep_links_and_api_paths_remain_distinct(tmp_path: Path) -> None:
    """@covers US-067-AC6 @covers US-069-AC2"""
    assets(tmp_path)
    app = create_static_app(settings=UiSettings(asset_root=str(tmp_path), mount_path="/assistant"))

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/assistant/sessions/fabricated")
            assert response.status_code == 200
            assert '<base href="/assistant/">' in response.text
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert (await client.get("/assistant/api/missing")).status_code == 404
            assert (await client.get("/assistant/assets/missing.js")).status_code == 404
            assert (await client.get("/unmounted")).status_code == 404
            script = await client.get("/assistant/assets/app-abcdefgh.js")
            assert "immutable" in script.headers["cache-control"]
            head = await client.head("/assistant/assets/app-abcdefgh.js")
            assert not head.content
            assert head.headers["content-length"] == str(len(script.content))

    asyncio.run(scenario())


def test_missing_or_corrupt_assets_fail_readiness(tmp_path: Path) -> None:
    """@covers US-069-AC6"""
    assets(tmp_path)
    (tmp_path / "index.html").write_text("altered")
    app = create_static_app(settings=UiSettings(asset_root=str(tmp_path)))

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/")
            assert response.status_code == 503
            assert str(tmp_path) not in response.text

    asyncio.run(scenario())


def test_total_asset_bound_is_enforced(tmp_path: Path) -> None:
    assets(tmp_path)
    app = create_static_app(settings=UiSettings(asset_root=str(tmp_path), max_total_bytes=4))

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/")).status_code == 503

    asyncio.run(scenario())


def test_mounted_asgi_root_is_not_duplicated(tmp_path: Path) -> None:
    """@covers US-067-AC6"""
    assets(tmp_path)
    app = create_static_app(settings=UiSettings(asset_root=str(tmp_path), mount_path="/assistant"))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app, root_path="/assistant")
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/assistant/sessions/one")
            assert '<base href="/assistant/">' in response.text

    asyncio.run(scenario())


def test_explicit_asset_manifest_blocks_symlinks(tmp_path: Path) -> None:
    assets(tmp_path)
    outside = tmp_path.parent / "fabricated-external.txt"
    outside.write_text("external content")
    (tmp_path / "index.html").unlink()
    (tmp_path / "index.html").symlink_to(outside)
    app = create_static_app(settings=UiSettings(asset_root=str(tmp_path)))

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/")
            assert response.status_code == 503
            assert "external content" not in response.text

    asyncio.run(scenario())


def test_configuration_rejects_ambiguous_prefixes_and_limits() -> None:
    import pytest

    for prefix in ("relative", "/a/../b", "/a//b", "/a?query", "/a\\b"):
        with pytest.raises(ValueError, match="invalid_mount_path"):
            UiSettings(mount_path=prefix)
    for path in ("relative", "/api?query", "/api\\v1"):
        with pytest.raises(ValueError, match="invalid_api_discovery_path"):
            UiSettings(api_discovery_path=path)
    with pytest.raises(ValueError, match="invalid_asset_limits"):
        UiSettings(max_asset_bytes=0)
    with pytest.raises(ValueError, match="invalid_asset_limits"):
        UiSettings(max_assets=0)
    with pytest.raises(ValueError, match="invalid_asset_limits"):
        UiSettings(max_total_bytes=0)


def test_untrusted_manifest_never_exposes_outside_files(tmp_path: Path) -> None:
    from tether_web_ui import StaticApp

    async def manifest_scenario() -> None:
        for document in (
            [],
            {"../outside": "a" * 64},
            {"missing.js": "a" * 64},
            {"index.html": 3},
            {},
            {"index.html": "a" * 64},
        ):
            (tmp_path / "tether-assets.json").write_text(json.dumps(document))
            app = StaticApp(UiSettings(asset_root=str(tmp_path)))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/")).status_code == 503

    asyncio.run(manifest_scenario())


def test_lifespan_readiness_shutdown_and_protocol_refusal(tmp_path: Path) -> None:
    import pytest
    from tether_web_ui import StaticApp

    assets(tmp_path)

    async def scenario() -> None:
        for valid in (True, False):
            if not valid:
                (tmp_path / "tether-assets.json").unlink()
            app = StaticApp(UiSettings(asset_root=str(tmp_path)))
            events: Iterator[Mapping[str, object]] = iter(
                ({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"})
            )
            sent: list[dict[str, object]] = []

            async def receive(
                events: Iterator[Mapping[str, object]] = events,
            ) -> Mapping[str, object]:
                return next(events)

            async def send(
                message: dict[str, object], sent: list[dict[str, object]] = sent
            ) -> None:
                sent.append(message)

            await app({"type": "lifespan"}, receive, send)
            expected = (
                ["lifespan.startup.complete", "lifespan.shutdown.complete"]
                if valid
                else ["lifespan.startup.failed"]
            )
            assert [item["type"] for item in sent] == expected
            with pytest.raises(ValueError, match="unsupported_asgi_scope"):
                await app({"type": "websocket"}, receive, send)

    asyncio.run(scenario())


def test_config_api_fallback_and_methods_are_explicit(tmp_path: Path) -> None:
    assets(tmp_path)
    app = create_static_app(settings=UiSettings(asset_root=str(tmp_path), mount_path="/assistant"))

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            config = await client.get("/assistant/.tether/config.json")
            assert config.json() == {
                "base_path": "/assistant",
                "api_discovery_path": "/api/v1/capabilities",
            }
            assert config.headers["cache-control"] == "no-store"
            assert (await client.post("/assistant/")).status_code == 405
            assert (await client.get("/assistant/api")).status_code == 404
            assert (await client.get("/assistant/bad\\path")).status_code == 404

    asyncio.run(scenario())
