from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from fastapi import Request
from starlette.applications import Starlette
from starlette.routing import Mount
from tether_web_api import ApiSettings, create_api
from tether_web_api.errors import ApiFailure

from tether.client import (
    Actor,
    ClientProvider,
    SessionSummary,
    TurnResult,
    UnknownSession,
    UnknownTurn,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def summary(session_id: str = "s-1", *, ref: str | None = None) -> SessionSummary:
    now = datetime.now(UTC)
    return SessionSummary(id=session_id, owner="alice", ref=ref, created_at=now, updated_at=now)


class FakeHandle:
    turn_id = "t-1"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.result: TurnResult | None = None

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "methods": {"send": "supported"},
            "tiers": {"live": "available", "archive": "unavailable"},
        }

    async def create(self, **kwargs: Any) -> SessionSummary:
        self.calls.append(("create", (), kwargs))
        return summary(ref=kwargs["ref"])

    async def send(self, *args: Any, **kwargs: Any) -> FakeHandle:
        self.calls.append(("send", args, kwargs))
        return FakeHandle()

    async def turn_result(self, *args: Any) -> TurnResult | None:
        self.calls.append(("turn_result", args, {}))
        return self.result

    async def get(self, session_id: str, **kwargs: Any) -> Any:
        self.calls.append(("get", (session_id,), kwargs))
        return {
            "summary": summary(session_id),
            "settings": {},
            "specs": {},
            "pending": None,
            "tools": [],
            "tools_status": "not_loaded",
        }


class FakeAuthenticator:
    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def actor_for(self, request: Request) -> Actor:
        self.calls += 1
        if self.fail:
            raise ApiFailure(401, "authentication_required")
        return Actor(id="alice", principal="alice@example.test")


@pytest.fixture
async def api_client() -> AsyncIterator[tuple[httpx.AsyncClient, FakeClient, FakeAuthenticator]]:
    client = FakeClient()
    authenticator = FakeAuthenticator()
    app = create_api(
        clients=cast(ClientProvider, lambda actor: client),
        authenticator=authenticator,
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, client, authenticator


@pytest.mark.anyio
async def test_create_maps_request_id_to_public_client_ref(api_client: Any) -> None:
    http, client, _auth = api_client
    response = await http.post(
        "/api/v1/sessions",
        json={"request_id": "create-1", "title": "session", "settings": {"limit": 7}},
    )
    assert response.status_code == 201
    assert response.json()["ref"] == "create-1"
    assert client.calls[-1][0] == "create"
    assert client.calls[-1][2] == {
        "ref": "create-1",
        "spec": None,
        "title": "session",
        "tags": (),
        "settings": {"limit": 7},
    }


@pytest.mark.anyio
async def test_send_accepts_idempotently_and_result_is_nullable_until_complete(
    api_client: Any,
) -> None:
    http, client, _auth = api_client
    accepted = await http.post(
        "/api/v1/sessions/s-1/turns",
        json={"request_id": "send-1", "text": "hello"},
    )
    assert accepted.status_code == 202
    assert accepted.json() == {
        "request_id": "send-1",
        "turn_id": "t-1",
        "state": "accepted",
        "result_url": "/api/v1/sessions/s-1/turns/t-1",
    }
    assert client.calls[-1] == (
        "send",
        ("s-1", "hello"),
        {"request_id": "send-1", "attachments": (), "spec": None},
    )

    pending = await http.get("/api/v1/sessions/s-1/turns/t-1")
    assert pending.status_code == 200
    assert pending.json() is None
    client.result = TurnResult(turn_id="t-1", stop_reason="completed")
    completed = await http.get("/api/v1/sessions/s-1/turns/t-1")
    assert completed.json() == {
        "turn_id": "t-1",
        "stop_reason": "completed",
        "reason": None,
        "messages": [],
        "pending": [],
    }


@pytest.mark.anyio
async def test_accepted_result_url_keeps_the_external_mount_prefix() -> None:
    client = FakeClient()
    api = create_api(
        clients=cast(ClientProvider, lambda actor: client),
        authenticator=FakeAuthenticator(),
        settings=ApiSettings(api_prefix="/v1"),
    )
    mounted = Starlette(routes=[Mount("/assistant/api", app=api)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mounted), base_url="http://test"
    ) as http:
        accepted = await http.post(
            "/assistant/api/v1/sessions/s-1/turns", json={"request_id": "send-1", "text": "hi"}
        )
        assert accepted.json()["result_url"] == "/assistant/api/v1/sessions/s-1/turns/t-1"
        result = await http.get(accepted.json()["result_url"])
    assert result.status_code == 200
    assert result.json() is None


@pytest.mark.anyio
async def test_unknown_turn_and_auth_failure_are_content_free_problems(api_client: Any) -> None:
    http, _client, authenticator = api_client

    class UnknownClient(FakeClient):
        async def turn_result(self, *_args: Any) -> None:
            raise UnknownTurn("s-1/t-missing")

    app = create_api(
        clients=cast(ClientProvider, lambda actor: UnknownClient()),
        authenticator=authenticator,
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as unknown_http:
        unknown = await unknown_http.get("/api/v1/sessions/s-1/turns/t-missing")
    assert unknown.status_code == 404
    assert unknown.headers["content-type"].startswith("application/problem+json")
    assert unknown.json()["code"] == "unknown_turn"
    assert "s-1/t-missing" not in unknown.text

    authenticator.fail = True
    denied = await http.get("/api/v1/capabilities")
    assert denied.status_code == 401
    assert denied.json()["code"] == "authentication_required"
    assert "alice@example.test" not in denied.text


@pytest.mark.anyio
async def test_expected_unknown_owner_is_handled_but_unexpected_failure_still_raises() -> None:
    class MissingSessionClient(FakeClient):
        async def get(self, session_id: str, **kwargs: Any) -> Any:
            raise UnknownSession("owner_or_session_not_found")

    app = create_api(
        clients=cast(ClientProvider, lambda actor: MissingSessionClient()),
        authenticator=FakeAuthenticator(),
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/api/v1/sessions/other-owner-session")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_session"
    assert "owner_or_session_not_found" not in response.text

    class BrokenClient(FakeClient):
        async def get(self, session_id: str, **kwargs: Any) -> Any:
            raise RuntimeError("unexpected defect")

    broken = create_api(
        clients=cast(ClientProvider, lambda actor: BrokenClient()),
        authenticator=FakeAuthenticator(),
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=broken), base_url="http://test"
    ) as broken_http:
        with pytest.raises(RuntimeError, match="unexpected defect"):
            await broken_http.get("/api/v1/sessions/s-1")


@pytest.mark.anyio
async def test_invalid_request_returns_problem_details_and_schema_is_published(
    api_client: Any,
) -> None:
    http, _client, _auth = api_client
    invalid = await http.post("/api/v1/sessions", json={"request_id": ""})
    assert invalid.status_code == 400
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert invalid.json()["code"] == "invalid_request"

    schema = await http.get("/api/v1/schema/http-v1.json")
    assert schema.status_code == 200
    assert schema.json()["$defs"]["Change"]["discriminator"]["propertyName"] == "type"


@pytest.mark.anyio
async def test_versioned_openapi_references_wire_response_and_problem_schemas(
    api_client: Any,
) -> None:
    http, _client, _auth = api_client
    response = await http.get("/api/v1/openapi.json")
    assert response.status_code == 200
    document = response.json()
    schemas = document["components"]["schemas"]
    session_summary = schemas["SessionSummary"]["properties"]
    assert session_summary["entry_count"]["type"] == "string"
    assert session_summary["entry_count"]["format"] == "tether-counter"
    send = document["paths"]["/api/v1/sessions/{session_id}/turns"]["post"]
    assert send["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/AcceptedTurn"
    )
    assert send["responses"]["409"]["content"]["application/problem+json"]["schema"][
        "$ref"
    ].endswith("/ProblemDetails")
    events = document["paths"]["/api/v1/events"]["get"]
    assert "text/event-stream" in events["responses"]["200"]["content"]


@pytest.mark.anyio
async def test_openapi_response_registry_covers_every_public_route() -> None:
    client = FakeClient()
    app = create_api(
        clients=cast(ClientProvider, lambda actor: client),
        authenticator=FakeAuthenticator(),
        settings=ApiSettings(),
    )
    document = app.openapi()
    expected = {
        ("get", "/api/v1/capabilities"): ("200", "ClientCapabilities"),
        ("get", "/api/v1/sessions"): ("200", "PageSessionSummary"),
        ("post", "/api/v1/sessions"): ("201", "SessionSummary"),
        ("get", "/api/v1/sessions/by-ref/{ref}"): ("200", "SessionSummary"),
        ("get", "/api/v1/sessions/{session_id}"): ("200", "SessionView"),
        ("get", "/api/v1/sessions/{session_id}/snapshot"): ("200", "SessionSnapshot"),
        ("get", "/api/v1/sessions/{session_id}/entries"): ("200", "EntryPage"),
        ("get", "/api/v1/sessions/{session_id}/history"): ("200", "HistoryPage"),
        ("get", "/api/v1/sessions/{session_id}/pending"): ("200", "PendingConfirmation"),
        ("get", "/api/v1/sessions/{session_id}/effects"): ("200", "EffectPage"),
        ("post", "/api/v1/sessions/{session_id}/call-details"): ("200", "CallDetail"),
        ("post", "/api/v1/sessions/{session_id}/turns"): ("202", "AcceptedTurn"),
        ("get", "/api/v1/sessions/{session_id}/turns/{turn_id}"): ("200", "TurnResult"),
        ("delete", "/api/v1/sessions/{session_id}/turns/{turn_id}"): ("200", "boolean"),
        ("post", "/api/v1/sessions/{session_id}/cancel"): ("200", "boolean"),
        ("post", "/api/v1/sessions/{session_id}/confirmations/{invocation_id}"): (
            "200",
            "ConfirmResponse",
        ),
        ("post", "/api/v1/sessions/{session_id}/resume"): ("202", "AcceptedTurn"),
        ("post", "/api/v1/sessions/{session_id}/effects/{invocation_id}/resolve"): (
            "200",
            "SessionSummary",
        ),
        ("patch", "/api/v1/sessions/{session_id}"): ("200", "SessionSummary"),
        ("post", "/api/v1/sessions/{session_id}/close"): ("200", "SessionSummary"),
        ("post", "/api/v1/sessions/{session_id}/history-links"): ("200", "Entry"),
        ("post", "/api/v1/sessions/{session_id}/restore"): ("200", "SessionSummary"),
        ("get", "/api/v1/events"): ("200", "text/event-stream"),
    }
    operations = {
        (method, path) for path, methods in document["paths"].items() for method in methods
    }
    assert operations == set(expected)
    for (method, path), (status, expected_schema) in expected.items():
        operation = document["paths"][path][method]
        content = operation["responses"][status]["content"]
        media_type = (
            "text/event-stream" if expected_schema == "text/event-stream" else "application/json"
        )
        schema = content[media_type]["schema"]
        if expected_schema == "boolean":
            assert schema == {"type": "boolean"}
        elif expected_schema == "text/event-stream":
            assert schema == {"type": "string"}
        elif method == "post" and path.endswith("/call-details"):
            assert schema["type"] == "array"
            assert schema["items"]["$ref"].endswith("/CallDetail")
        elif method == "get" and path.endswith("/{turn_id}"):
            assert schema["anyOf"][0]["$ref"].endswith("/TurnResult")
            assert schema["anyOf"][1] == {"type": "null"}
        elif path in {"/api/v1/sessions/by-ref/{ref}", "/api/v1/sessions/{session_id}/pending"}:
            assert schema["anyOf"][0]["$ref"].endswith(f"/{expected_schema}")
            assert schema["anyOf"][1] == {"type": "null"}
        else:
            assert schema["$ref"].endswith(f"/{expected_schema}")
        assert operation["responses"]["401"]["content"]["application/problem+json"]["schema"][
            "$ref"
        ].endswith("/ProblemDetails")
        assert operation["responses"]["404"]["content"]["application/problem+json"]["schema"][
            "$ref"
        ].endswith("/ProblemDetails")
        assert operation["responses"]["408"]["content"]["application/problem+json"]["schema"][
            "$ref"
        ].endswith("/ProblemDetails")
    confirmation = document["paths"]["/api/v1/sessions/{session_id}/confirmations/{invocation_id}"][
        "post"
    ]
    assert confirmation["responses"]["202"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/AcceptedTurn")
