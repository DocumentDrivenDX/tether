"""On-behalf-of calls through the Databricks AI Gateway header profile.

An application that holds a per-request user token passes it as the caller
credential. These wire tests use a mock transport and fabricated tokens only;
the live per-tier receipts are an operator step (see README).
"""

import asyncio
import json

import httpx2 as httpx
import pytest
from test_compatible_adapter import wire_response

from tether.adapters.openai_compat import OpenAICompatModel
from tether.models import complete
from tether.types import ModelExecutionLimits, ModelRequest, ModelUnavailable

pytestmark = pytest.mark.anyio
GATEWAY_TAGS = "databricks-ai-gateway-request-tags"
SCHEME = "Bearer"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class UserToken:
    """Stands in for the application's per-request credential holder."""

    def __init__(self, user: str) -> None:
        self.user = user
        self.reads = 0

    async def authorization(self) -> str:
        self.reads += 1
        return " ".join((SCHEME, "fabricated-token-for-" + self.user))


def gateway(limits: ModelExecutionLimits, handler, application=None) -> OpenAICompatModel:
    return OpenAICompatModel(
        base_url="https://fabricated-workspace.invalid/serving-endpoints",
        tiers={"demo": "fabricated-endpoint"},
        limits=limits,
        output_limit_field="max_tokens",
        application_credential=application,
        request_tags_header="Databricks-Ai-Gateway-Request-Tags",
        transport=httpx.MockTransport(handler),
    )


def caller_request(model_request: ModelRequest, user: str, trace: str) -> ModelRequest:
    tags = model_request.tags.model_copy(update={"principal": user})
    return model_request.model_copy(
        update={
            "identity_mode": "caller",
            "credential": UserToken(user),
            "traceparent": trace,
            "tags": tags,
        }
    )


def trace_for(index: int) -> str:
    return f"00-{index + 1:032x}-{index + 1:016x}-01"


async def test_caller_token_is_the_only_authorization_on_the_wire(model_request, limits):
    application = UserToken("application-service-principal")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json=wire_response(), headers={"x-request-id": "fabricated-gateway-id"}
        )

    request = caller_request(model_request, "user-a", trace_for(0))
    response = await complete(gateway(limits, handler, application), request)
    assert [r.headers.get_list("authorization") for r in seen] == [
        [" ".join((SCHEME, "fabricated-token-for-user-a"))]
    ]
    assert application.reads == 0
    assert response.request_id == "fabricated-gateway-id"
    receipt = response.receipt
    assert receipt is not None
    assert receipt.provider_request_id == "fabricated-gateway-id"
    assert receipt.request_id_source == "header"


async def test_concurrent_callers_each_see_their_own_token_and_request_id(model_request, limits):
    users = [f"user-{index}" for index in range(5)]
    started = asyncio.Event()
    arrived: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix(SCHEME + " ")
        user = token.removeprefix("fabricated-token-for-")
        arrived.append(user)
        if len(arrived) == len(users):
            started.set()
        await started.wait()  # every call is in flight before any returns
        tags = json.loads(request.headers[GATEWAY_TAGS])
        assert tags["principal"] == user
        assert tags["session_id"] == model_request.tags.session_id
        assert request.headers["traceparent"] == trace_for(users.index(user))
        return httpx.Response(
            200, json=wire_response(), headers={"x-request-id": "gateway-id-" + user}
        )

    model = gateway(limits, handler, UserToken("application-service-principal"))
    requests = [caller_request(model_request, u, trace_for(i)) for i, u in enumerate(users)]
    responses = await asyncio.gather(*(complete(model, r) for r in requests))
    assert sorted(arrived) == users
    assert [r.request_id for r in responses] == ["gateway-id-" + u for u in users]
    receipts = [r.receipt for r in responses]
    assert all(receipt is not None for receipt in receipts)
    assert [r.provider_request_id for r in receipts if r is not None] == [
        "gateway-id-" + u for u in users
    ]
    assert all(r.credential.reads >= 1 for r in requests)  # type: ignore[union-attr]


async def test_scope_tags_are_strings_on_the_gateway_header(model_request, limits):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=wire_response())

    await complete(gateway(limits, handler), caller_request(model_request, "user-a", trace_for(0)))
    tags = json.loads(seen[0].headers[GATEWAY_TAGS])
    assert tags["principal"] == "user-a"
    assert tags["model_call_id"] == model_request.tags.model_call_id
    assert all(isinstance(value, str) for value in tags.values())
    assert "x-tether-request-tags" not in seen[0].headers


async def test_gateway_error_reports_status_and_request_id_without_body(model_request, limits):
    marker = "fabricated-sensitive-body-text"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"error_code": "PERMISSION_DENIED", "message": marker},
            headers={"x-request-id": "fabricated-denied-id"},
        )

    with pytest.raises(ModelUnavailable) as raised:
        await complete(
            gateway(limits, handler), caller_request(model_request, "user-a", trace_for(0))
        )
    error = raised.value
    assert error.reason == "http 403"
    assert error.request_id == "fabricated-denied-id"
    assert error.receipt is not None
    assert error.receipt.provider_request_id == "fabricated-denied-id"
    assert marker not in str(error)
    assert marker not in repr(error)
    assert marker not in error.receipt.model_dump_json()
