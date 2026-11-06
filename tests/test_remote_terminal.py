"""Remote terminal launcher uses fresh explicit credentials and borrowed UI lifetime."""

import argparse

import httpx
import pytest
from fastapi import Request
from tether_web_api import ApiSettings, create_api
from textual.widgets import Button, Input
from tui_event_waits import (
    let_pilot_screen_wait_use_outer_owner,
    observe_session_shows,
    wait_for_state_or_operation,
)

from tether.cli import remote
from tether.cli.command import parser
from tether.cli.demo import open_demo
from tether.client import Actor
from tether.client.databricks import DatabricksCliSettings
from tether.client.remote import RemoteClient
from tether.tui import ClientApp


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pilot_screen_watchdog(monkeypatch):
    let_pilot_screen_wait_use_outer_owner(monkeypatch)


@pytest.mark.parametrize(
    "arguments",
    [
        ["--endpoint", "https://app.example"],
        ["--remote-profile", "fabricated"],
        ["--endpoint", "http://app.example", "--remote-profile", "fabricated"],
        [
            "--endpoint",
            "".join(("https://", "user", ":", "secret", "@app.example")),
            "--remote-profile",
            "fabricated",
        ],
        ["--endpoint", "https://app.example/path", "--remote-profile", "fabricated"],
        ["--endpoint", "https://app.example", "--remote-profile", "fabricated", "--volatile"],
        [
            "--endpoint",
            "https://app.example",
            "--remote-profile",
            "fabricated",
            "--api-prefix",
            "//other",
        ],
        [
            "--endpoint",
            "https://app.example",
            "--remote-profile",
            "fabricated",
            "--api-prefix",
            "/a/../b",
        ],
    ],
)
def test_remote_options_refuse_ambiguous_or_unsafe_composition(arguments):
    with pytest.raises(ValueError, match="remote_"):
        remote.validate_remote(parser().parse_args(arguments))


class Authenticator:
    async def actor_for(self, request: Request) -> Actor:
        assert request.headers["authorization"].startswith(" ".join(("Bearer", "fabricated-")))
        return Actor(id="demo")


@pytest.mark.anyio
async def test_remote_terminal_reads_and_approves_through_http_without_owning_client(
    tmp_path, monkeypatch
):
    """@covers US-065-AC7: same read/send/exact-approval/final and borrowed lifetime as local."""
    credentials = []

    class Profile:
        def __init__(self, settings: DatabricksCliSettings):
            self.settings = settings

        async def host(self):
            return "https://fabricated-workspace.example"

        async def authorization(self):
            assert self.settings.expected_host == "https://fabricated-workspace.example"
            credentials.append(len(credentials) + 1)
            return " ".join(("Bearer", f"fabricated-{len(credentials)}"))

    args = parser().parse_args(
        [
            "--endpoint",
            "https://app.example",
            "--remote-profile",
            "fabricated",
            "--api-prefix",
            "/carrier/api/v1",
        ]
    )
    monkeypatch.setattr(remote, "DatabricksCliProfile", Profile)
    async with open_demo(tmp_path) as (host, actor, tool):
        created = await host.client(actor).create(ref="demo")
        api = create_api(
            settings=ApiSettings(api_prefix=args.api_prefix),
            clients=host.client,
            authenticator=Authenticator(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url=args.endpoint
        ) as http:
            original = RemoteClient.open

            async def injected(settings):
                return await original(settings, http_client=http)

            monkeypatch.setattr(RemoteClient, "open", staticmethod(injected))
            async with remote.open_remote(args) as client:
                app = ClientApp(client, session_id=created.id)
                shown = observe_session_shows(app)
                async with app.run_test() as pilot:
                    assert app.session_id == created.id
                    follower = app._follower
                    assert follower is not None
                    app.query_one("#message", Input).value = "Fabricated request"
                    await pilot.click("#send")

                    async def approval_ready():
                        pending = await client.pending(created.id)
                        return (
                            pending is not None and not app.query_one("#approve", Button).disabled
                        )

                    await wait_for_state_or_operation(shown, approval_ready, follower)
                    await pilot._wait_for_screen()
                    assert not app.query_one("#approve", Button).disabled
                    await pilot.click("#approve")

                    async def approval_completed():
                        pending = await client.pending(created.id)
                        view = await client.get(created.id)
                        return (
                            pending is None
                            and app.query_one("#approve", Button).disabled
                            and tool.recorded == ["seedling"]
                            and view.summary.status == "idle"
                        )

                    await wait_for_state_or_operation(shown, approval_completed, follower)
                    await pilot._wait_for_screen()
                    assert tool.recorded == ["seedling"]
                assert (await client.get(created.id)).summary.status == "idle"
            with pytest.raises(RuntimeError, match="remote_client_closed"):
                await client.get(created.id)
        assert (await host.client(actor).get(created.id)).summary.status == "idle"
    assert len(credentials) >= 6


def test_remote_prefix_allows_explicit_proxy_mount():
    args = argparse.Namespace(
        endpoint="https://app.example",
        remote_profile="fabricated",
        api_prefix="/assistant/api/v1",
        volatile=False,
        model_config=None,
        action=None,
    )
    remote.validate_remote(args)
