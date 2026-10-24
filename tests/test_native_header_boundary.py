"""Ambient native SDK header defaults never become physical egress inputs."""

import json

import httpx2 as httpx
import pytest
from test_compatible_adapter import CredentialProbe, wire_response

from tether.adapters.openai_compat import OpenAICompatModel
from tether.models import complete
from tether.types import ModelUnavailable

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("mode", ["anonymous", "caller", "application"])
async def test_ambient_sdk_headers_cannot_widen_identity_or_request_metadata(
    model_request, limits, monkeypatch, mode
):
    caller, application = CredentialProbe("caller"), CredentialProbe("application")
    forged = {
        "Authorization": " ".join(("Synthetic", "ambient")),
        "Host": "ambient.invalid",
        "Cookie": "fabricated-cookie",
        "Content-Type": "text/plain",
        "Content-Length": "999999",
        "Accept": "text/plain",
        "Accept-Encoding": "gzip",
        "OpenAI-Organization": "fabricated-org",
        "OpenAI-Project": "fabricated-project",
        "traceparent": "ambient",
        "X-Tether-Request-Tags": "ambient",
        "X-Fabricated": "ambient",
    }
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", "\n".join(f"{key}: {value}" for key, value in forged.items())
    )
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=wire_response())

    trace = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    request = model_request.model_copy(
        update={
            "identity_mode": mode,
            "credential": caller if mode == "caller" else None,
            "traceparent": trace,
        }
    )
    model = OpenAICompatModel(
        base_url="https://fabricated.invalid:8443/v1",
        tiers={"demo": "fabricated"},
        limits=limits,
        output_limit_field="max_tokens",
        application_credential=application,
        transport=httpx.MockTransport(handler),
    )
    try:
        await complete(model, request)
        headers = seen[0].headers
        assert headers["host"] == "fabricated.invalid:8443"
        assert headers["content-length"] == str(len(seen[0].content))
        assert headers["content-type"] == headers["accept"] == "application/json"
        assert headers["accept-encoding"] == "identity"
        assert headers["traceparent"] == trace
        tags = json.loads(headers["x-tether-request-tags"])
        assert tags["model_call_id"] == request.tags.model_call_id
        expected = {
            "host",
            "content-length",
            "content-type",
            "accept",
            "accept-encoding",
            "traceparent",
            "x-tether-request-tags",
        }
        if mode == "anonymous":
            assert "authorization" not in headers
            assert caller.reads == application.reads == 0
        else:
            selected = caller if mode == "caller" else application
            assert headers["authorization"] == " ".join(("Synthetic", selected.actor, "1"))
            assert selected.reads == 1
            expected.add("authorization")
        assert set(headers) == expected
    finally:
        await model.aclose()


async def test_malformed_ambient_sdk_header_fails_closed_before_transport(
    model_request, limits, monkeypatch
):
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Fabricated: é")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=wire_response())

    model = OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "fabricated"},
        limits=limits,
        output_limit_field="max_tokens",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ModelUnavailable, match="invalid_response") as error:
            await complete(model, model_request)
        assert not seen
        assert "é" not in str(error.value)
    finally:
        await model.aclose()
