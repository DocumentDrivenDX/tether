"""Offline fabricated ASGI embedder; replace its auth and model before deployment."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from tether_web_api import ApiSettings, create_api
from tether_web_ui import UiSettings, create_static_app

from tether.client import (
    Actor,
    AgentSpec,
    ClientHost,
    ClientLimits,
    ClientSettings,
    LocalJsonlSettings,
    ModelExecutionLimits,
    ModelResponse,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model

if TYPE_CHECKING:
    from fastapi import Request


class FabricatedAuthenticator:
    """Local example identity only; do not use it to authenticate real users."""

    async def actor_for(self, request: Request) -> Actor:
        return Actor(id="fabricated-local-user")


def client_settings(root: Path) -> ClientSettings:
    context = fabricated_model_context()
    return ClientSettings(
        model=scripted_model(
            [ModelResponse(content="Fabricated embedder response.", finish_reason="stop")]
        ),
        storage=LocalJsonlSettings(root=root),
        policy=demo_policy(),
        client_limits=ClientLimits(
            max_page_size=100,
            max_call_ids=8,
            max_read_bytes=1_048_576,
            max_lookup_entries=1_000,
            read_timeout_seconds=10,
            heartbeat_interval_seconds=15,
            shutdown_timeout_seconds=5,
            follower_queue_size=32,
            max_followers=8,
            max_active_turns=8,
            max_request_bytes=65_536,
        ),
        model_limits=ModelExecutionLimits(
            max_response_bytes=65_536,
            max_tool_argument_bytes=16_384,
            max_attempts=2,
            request_timeout_seconds=30,
            max_retry_delay_seconds=2,
        ),
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated embedder",
                instructions="Reply to the fabricated request.",
                model="demo",
            ),
        ),
        default_spec="demo",
    )


async def create_app(root: Path) -> tuple[FastAPI, ClientHost]:
    """Return an app plus its owned host so the caller controls shutdown."""
    host = await ClientHost.open(client_settings(root))
    api = create_api(
        clients=host.client,
        authenticator=FabricatedAuthenticator(),
        settings=ApiSettings(api_prefix="/v1"),
    )
    app = FastAPI()
    app.mount("/api", api)
    app.mount("/", create_static_app(settings=UiSettings()))
    return app, host
