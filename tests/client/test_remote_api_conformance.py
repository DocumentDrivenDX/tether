from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from fastapi import Request
from tether_web_api import ApiSettings, create_api

from tether.client import (
    Actor,
    Answer,
    Change,
    ClientCapabilities,
    ClientProvider,
    EffectPage,
    Entry,
    EntryPage,
    HistoryPage,
    HistoryRef,
    Page,
    ResyncRequired,
    SessionSnapshot,
    SessionSummary,
    SessionView,
    TurnResult,
)
from tether.client.remote import RemoteClient, RemoteClientSettings, RemoteProblem

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Credential:
    async def authorization(self) -> str:
        return " ".join(("Bearer", "fabricated-user"))


async def credential_provider() -> Credential:
    return Credential()


class Handle:
    turn_id = "turn-1"


class ApiSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.now = datetime.now(UTC)
        self.summary_value = SessionSummary(
            id="session-one",
            owner="alice",
            ref="ref/one",
            created_at=self.now,
            updated_at=self.now,
            entry_count=2**61,
            message_count=2**60,
            last_seq=2**62,
        )
        self.view_value = SessionView(summary=self.summary_value, settings={"user_integer": 2**60})
        self.confirm_count = 0
        self._responses: dict[str, Any] = {
            "list": self._listed,
            "create": self._created,
            "find": self._found,
            "get": lambda *_args, **_kwargs: self.view_value,
            "snapshot": lambda *_args, **_kwargs: SessionSnapshot(
                view=self.view_value, through_index=-1, seq=2**62
            ),
            "entries": lambda *_args, **_kwargs: EntryPage(items=(), next_start=None),
            "history": lambda *_args, **_kwargs: HistoryPage(items=(), next_start=None),
            "pending": lambda *_args, **_kwargs: None,
            "effects": lambda *_args, **_kwargs: EffectPage(items=(), next_start=None),
            "call_detail": lambda *_args, **_kwargs: (),
            "send": lambda *_args, **_kwargs: Handle(),
            "turn_result": self._turn_result,
            "cancel_turn": lambda *_args, **_kwargs: True,
            "cancel": lambda *_args, **_kwargs: True,
            "confirm": self._confirm,
            "resume": lambda *_args, **_kwargs: Handle(),
            "resolve": lambda *_args, **_kwargs: self.summary_value,
            "update": lambda *_args, **_kwargs: self.summary_value,
            "close": lambda *_args, **_kwargs: self.summary_value,
            "restore": lambda *_args, **_kwargs: self.summary_value,
            "link_history": self._linked,
        }
        self.capabilities = ClientCapabilities(
            methods={
                name: "supported"
                for name in (
                    "list",
                    "get",
                    "entries",
                    "history",
                    "pending",
                    "snapshot",
                    "effects",
                    "call_detail",
                    "follow",
                    "find",
                    "create",
                    "send",
                    "confirm",
                    "resume",
                    "resolve",
                    "cancel",
                    "cancel_turn",
                    "update",
                    "close",
                    "link_history",
                    "turn_result",
                    "archive_list",
                    "restore",
                )
            },
            tiers={"live": "available", "archive": "available"},
        )

    def __getattr__(self, name: str) -> Any:
        if name not in self.capabilities.methods:
            raise AttributeError(name)
        if name == "follow":

            def follow(*args: Any, **kwargs: Any) -> AsyncIterator[Change]:
                self.calls.append((name, args, kwargs))
                return self._follow()

            return follow

        async def operation(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return self._responses[name](*args, **kwargs)

        return operation

    async def _follow(self) -> AsyncIterator[Change]:
        yield ResyncRequired(reason="gap")

    def _listed(self, *_args: Any, **_kwargs: Any) -> Page[SessionSummary]:
        return Page(items=(self.summary_value,), next=None)

    def _created(self, *_args: Any, **kwargs: Any) -> SessionSummary:
        self.summary_value = self.summary_value.model_copy(
            update={"ref": kwargs["ref"], "title": kwargs["title"]}
        )
        return self.summary_value

    def _found(self, *_args: Any, **_kwargs: Any) -> SessionSummary:
        return self.summary_value

    @staticmethod
    def _turn_result(*args: Any, **_kwargs: Any) -> TurnResult:
        return TurnResult(turn_id=args[1], stop_reason="completed")

    def _confirm(self, *_args: Any, **_kwargs: Any) -> Handle | None:
        self.confirm_count += 1
        return Handle() if self.confirm_count == 1 else None

    def _linked(self, *_args: Any, **_kwargs: Any) -> Entry:
        return Entry(kind="history_link", index=9, at=self.now)


class Authenticator:
    async def actor_for(self, request: Request) -> Actor:
        assert request.headers.get("authorization") == " ".join(("Bearer", "fabricated-user"))
        return Actor(id="alice", principal="alice")


def assert_forwarded_operations(spy: ApiSpy) -> None:
    assert [call[0] for call in spy.calls] == [
        "list",
        "create",
        "find",
        "get",
        "snapshot",
        "entries",
        "history",
        "pending",
        "effects",
        "call_detail",
        "snapshot",
        "follow",
        "send",
        "turn_result",
        "cancel_turn",
        "cancel",
        "confirm",
        "confirm",
        "get",
        "resume",
        "resolve",
        "update",
        "close",
        "link_history",
        "restore",
    ]
    assert spy.calls[0][2] == {"tier": "archive", "cursor": "next/one", "limit": 7}
    assert spy.calls[0][1][0].owner == "alice"
    assert spy.calls[1][2] == {
        "ref": "request/ref",
        "spec": None,
        "title": "created",
        "tags": ("tag",),
        "settings": {"n": 2**60},
    }
    assert spy.calls[2][1] == ("request/ref",)
    assert spy.calls[3] == ("get", ("session-one",), {"tier": "archive"})
    assert spy.calls[5] == (
        "entries",
        ("session-one",),
        {"tier": "archive", "start": 3, "limit": 4, "reverse": True},
    )
    assert spy.calls[6] == (
        "history",
        ("session-one",),
        {"tier": "archive", "start": 5, "limit": 6},
    )
    assert spy.calls[8][2] == {"start": 7, "limit": 8}
    assert spy.calls[9][1] == ("session-one", ("call/a",))
    assert spy.calls[11][2] == {"session_id": "session-one", "since": 2**62}
    assert spy.calls[12][1] == ("session-one", "hello")
    assert spy.calls[12][2]["request_id"] == "send/ref"
    assert spy.calls[12][2]["spec"] == "spec"
    assert spy.calls[13][1] == ("session-one", "turn-1")
    assert spy.calls[14][1] == ("session-one", "turn-1")
    assert spy.calls[15][1] == ("session-one",)
    assert spy.calls[16][1][2] == Answer(approved=True)
    assert spy.calls[17][1][2] == Answer(approved=False)
    assert spy.calls[19][1] == ("session-one",)
    assert spy.calls[20] == (
        "resolve",
        ("session-one", "invoke-3", "completed"),
        {"reason": "verified"},
    )
    assert spy.calls[21][2] == {
        "title": "updated",
        "tags": ("x",),
        "settings": {"z": 2**60},
    }
    assert spy.calls[22][1] == ("session-one",)
    assert spy.calls[23][2] == {"key": "idempotency"}
    assert spy.calls[24][1] == ("session-one",)
    assert spy.calls[23][1][1] == 9


async def assert_empty_idempotency_keys_are_rejected(remote: RemoteClient, spy: ApiSpy) -> None:
    initial_calls = len(spy.calls)
    with pytest.raises(RemoteProblem) as invalid_create:
        await remote.create(ref="")
    with pytest.raises(RemoteProblem) as invalid_send:
        await remote.send("session-one", "hello", request_id="")
    assert invalid_create.value.status == 400
    assert invalid_send.value.status == 400
    assert len(spy.calls) == initial_calls


@pytest.mark.anyio
async def test_api_routes_forward_complete_public_protocol_and_decode_typed_results() -> None:
    spy = ApiSpy()
    api = create_api(
        clients=cast(ClientProvider, lambda actor: spy),
        authenticator=Authenticator(),
        settings=ApiSettings(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://tether.test"
    ) as http:
        remote = await RemoteClient.open(
            RemoteClientSettings("http://tether.test", credential_provider), http_client=http
        )
        try:
            assert len(remote.capabilities.methods) == 23
            await assert_empty_idempotency_keys_are_rejected(remote, spy)
            assert (await remote.list(tier="archive", cursor="next/one", limit=7)).items
            created = await remote.create(
                ref="request/ref", title="created", tags=("tag",), settings={"n": 2**60}
            )
            assert created.entry_count == 2**61
            assert created.last_seq == 2**62
            assert await remote.find("request/ref") == created
            assert (await remote.get("session-one", tier="archive")).settings[
                "user_integer"
            ] == 2**60
            assert (await remote.snapshot("session-one")).seq == 2**62
            await remote.entries("session-one", tier="archive", start=3, limit=4, reverse=True)
            await remote.history("session-one", tier="archive", start=5, limit=6)
            assert await remote.pending("session-one") is None
            await remote.effects("session-one", start=7, limit=8)
            assert await remote.call_detail("session-one", ["call/a"]) == ()
            async for _ in remote.follow(session_id="session-one", since=2**62):
                pass
            handle = await remote.send("session-one", "hello", request_id="send/ref", spec="spec")
            assert handle.turn_id == "turn-1"
            assert (await handle.result()).turn_id == "turn-1"
            assert await remote.cancel_turn("session-one", "turn-1")
            assert await remote.cancel("session-one")
            accepted = await remote.confirm("session-one", "invoke-1", Answer(approved=True))
            assert accepted is not None
            assert accepted.turn_id == "turn-1"
            assert await remote.confirm("session-one", "invoke-2", Answer(approved=False)) is None
            assert (await remote.resume("session-one")).turn_id == "turn-1"
            await remote.resolve("session-one", "invoke-3", "completed", reason="verified")
            await remote.update("session-one", title="updated", tags=("x",), settings={"z": 2**60})
            await remote.close("session-one")
            ref = HistoryRef(
                provider="p", key="k", sha256="0" * 64, owner="alice", session_id="session-one"
            )
            await remote.link_history("session-one", 9, ref, ref, key="idempotency")
            await remote.restore("session-one")
            assert_forwarded_operations(spy)
        finally:
            await remote.aclose()
