"""Explicit service-caller projection preserves fresh credentials and ownership."""

import asyncio

import httpx
import pytest

from tether.cli import remote
from tether.cli.command import parser
from tether.client.databricks import SERVICE_CALLER_CREDENTIAL_HEADER, service_caller_auth


def test_cli_service_caller_projects_each_current_credential_and_closes_owned_http(monkeypatch):
    """@covers US-065-AC7: explicit carrier transport retains the remote-client lifetime."""
    calls, clients, projected = [], [], []

    class Profile:
        def __init__(self, settings):
            self.settings = settings

        async def host(self):
            return "https://fabricated-workspace.example"

        async def authorization(self):
            calls.append(len(calls) + 1)
            return " ".join(("Bearer", f"fabricated-{len(calls)}"))

    def ingress(request):
        assert request.headers[SERVICE_CALLER_CREDENTIAL_HEADER] == request.headers["Authorization"]
        projected.append(request.headers[SERVICE_CALLER_CREDENTIAL_HEADER])
        # Actual server-side principal verification belongs to carrier tests.
        if request.url.path.endswith("/capabilities"):
            return httpx.Response(200, json={"methods": {"list": "supported"}, "tiers": {}})
        assert request.url.path == "/api/v1/sessions"
        return httpx.Response(200, json={"items": [], "next": None})

    original = httpx.AsyncClient

    def transport(**settings):
        assert settings["trust_env"] is False
        client = original(**settings, transport=httpx.MockTransport(ingress))
        clients.append(client)
        return client

    monkeypatch.setattr(remote, "DatabricksCliProfile", Profile)
    monkeypatch.setattr(httpx, "AsyncClient", transport)
    args = parser().parse_args(
        [
            "--endpoint",
            "https://app.example",
            "--remote-profile",
            "fabricated",
            "--remote-service-caller",
            "list",
        ]
    )

    async def run():
        async with remote.open_remote(args) as client:
            assert (await client.list()).items == ()
            assert not clients[0].is_closed
        assert clients[0].is_closed
        with pytest.raises(RuntimeError, match="remote_client_closed"):
            await client.list()

    asyncio.run(run())
    assert calls == [1, 2]
    assert projected == [" ".join(("Bearer", f"fabricated-{index}")) for index in (1, 2)]


def test_missing_current_credential_cannot_reuse_a_projected_header():
    auth = service_caller_auth()
    request = httpx.Request(
        "GET", "https://app.example", headers={SERVICE_CALLER_CREDENTIAL_HEADER: "stale"}
    )
    flow = auth.auth_flow(request)
    assert SERVICE_CALLER_CREDENTIAL_HEADER not in next(flow).headers


def test_service_caller_requires_explicit_remote_endpoint_and_profile():
    with pytest.raises(ValueError, match="remote_endpoint_and_profile_required"):
        remote.validate_remote(parser().parse_args(["--remote-service-caller"]))


def test_projection_refuses_other_authorization_schemes_without_echoing_the_value():
    value = " ".join(("Basic", "fabricated-marker"))
    request = httpx.Request("GET", "https://app.example", headers={"Authorization": value})
    with pytest.raises(ValueError, match="requires_bearer") as error:
        next(service_caller_auth().auth_flow(request))
    assert value not in str(error.value)
