"""Explicit remote terminal composition; no credential or endpoint discovery."""

from __future__ import annotations

import argparse
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from tether.client import ClientProtocol, Credential
from tether.client.databricks import DatabricksCliProfile, DatabricksCliSettings


def validate_remote(args: argparse.Namespace) -> None:
    if not args.endpoint or not args.remote_profile:
        raise ValueError("remote_endpoint_and_profile_required")
    endpoint: str = args.endpoint
    parsed = urlsplit(endpoint)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise ValueError("remote_endpoint_requires_https_origin_or_explicit_loopback")
    prefix: str = args.api_prefix
    if (
        not prefix.startswith("/")
        or prefix.endswith("/")
        or any(part in {"", ".", ".."} for part in prefix[1:].split("/"))
        or any(character in prefix for character in ("?", "#", "\\"))
    ):
        raise ValueError("remote_api_prefix_requires_normalized_absolute_path")
    if args.volatile or args.model_config is not None or args.action == "replay":
        raise ValueError("remote_mode_refuses_local_composition_options")


@asynccontextmanager
async def open_remote(args: argparse.Namespace) -> AsyncGenerator[ClientProtocol]:
    validate_remote(args)
    try:
        from tether.client.remote import RemoteClient, RemoteClientSettings
    except ImportError:
        raise RuntimeError("Install novirhiza-tether[remote] for endpoint access") from None
    profile = DatabricksCliProfile(DatabricksCliSettings(args.remote_profile))
    workspace_host = await profile.host()
    bound = DatabricksCliProfile(DatabricksCliSettings(args.remote_profile, workspace_host))

    async def credential() -> Credential:
        return bound

    settings = RemoteClientSettings(
        base_url=args.endpoint.rstrip("/"),
        credential_provider=credential,
        api_prefix=args.api_prefix,
    )
    if getattr(args, "remote_service_caller", False):
        import httpx

        from tether.client.databricks import service_caller_auth

        async with (
            httpx.AsyncClient(
                base_url=settings.base_url,
                timeout=settings.request_timeout_seconds,
                auth=service_caller_auth(),
                trust_env=False,
            ) as http,
            await RemoteClient.open(settings, http_client=http) as client,
        ):
            yield client
        return
    async with await RemoteClient.open(settings) as client:
        yield client


async def run_remote(
    args: argparse.Namespace,
    dispatch: Callable[[ClientProtocol, argparse.Namespace], Awaitable[None]],
) -> None:
    async with open_remote(args) as client:
        if args.action is None:
            try:
                from tether.tui import ClientApp
            except ImportError:
                raise RuntimeError(
                    "Install novirhiza-tether[tui,remote] for the remote TUI"
                ) from None
            await ClientApp(client).run_async()
        else:
            await dispatch(client, args)
