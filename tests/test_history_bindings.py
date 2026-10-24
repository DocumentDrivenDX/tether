"""An exchange binding can expose only actually observed, integrity-verified components."""

from __future__ import annotations

import pytest

from tether.history.jsonl import JsonlHistory
from tether.stores.memory import MemoryFiles
from tether.types import AttemptIdentity, Message, ModelResponse, ResponseReceipt
from tether.types.canonical import digest
from tether.types.execution import Actor
from tether.types.history import ExchangeBinding, HistoryRecord
from tether.types.storage import IntegrityError, StoreError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("kind", ["assistant", "summary", "none"])
async def test_exchange_binding_preserves_component_and_full_record_digests(kind):
    history = await JsonlHistory.open(MemoryFiles())
    actor = Actor(id="owner")
    identity = AttemptIdentity(
        session_id="session",
        turn_id="turn",
        model_call_id="call",
        attempt_id="attempt",
        purpose="answer",
    )
    receipt = ResponseReceipt(
        provider="fabricated",
        endpoint="offline",
        schema_profile="fixture",
        request_id_source="none",
    )
    response = ModelResponse(content="Fabricated answer", finish_reason="stop")
    record = HistoryRecord(
        kind="model_exchange",
        request={"prompt": "Fabricated"},
        response=response.model_dump(mode="json") if kind != "none" else None,
    )
    sha = digest(record.model_dump(mode="json"))
    source = (
        HistoryRecord(kind="summary", summary=response.content)
        if kind == "summary"
        else HistoryRecord(
            kind="message", message=Message(role="assistant", content=response.content)
        )
    )
    binding = ExchangeBinding(
        identity=identity,
        receipt=receipt,
        owner=actor.id,
        request_digest=digest(record.request),
        response_digest=digest(record.response) if record.response else None,
        exchange_sha256=sha,
        source_sha256=digest(source.model_dump(mode="json")) if kind != "none" else None,
        codec="model-port-v1",
        response_kind=kind,
    )
    key = f"attempt:{identity.attempt_id}:model-port-v1:{sha}"
    ref = await history.put(actor=actor, session_id=identity.session_id, key=key, record=record)
    refs = await history.bind_exchange(actor=actor, binding=binding)
    assert refs.exchange == ref
    if kind == "none":
        assert refs.source is None
    else:
        assert refs.source is not None
        assert refs.source.sha256 != refs.exchange.sha256
        assert (await history.read([refs.source], actor=actor))[0].record == source
        with pytest.raises(IntegrityError, match="source_integrity"):
            await history.bind_exchange(
                actor=actor, binding=binding.model_copy(update={"source_sha256": "wrong"})
            )
    with pytest.raises(StoreError, match="unsupported_exchange_binding"):
        await history.bind_exchange(
            actor=actor, binding=binding.model_copy(update={"codec": "unknown"})
        )
    with pytest.raises(IntegrityError, match="exchange_integrity"):
        await history.bind_exchange(
            actor=actor, binding=binding.model_copy(update={"request_digest": "wrong"})
        )


async def test_unobserved_exchange_and_unknown_refs_do_not_become_empty_ready_sources():
    history = await JsonlHistory.open(MemoryFiles())
    actor = Actor(id="owner")
    ref = await history.put(
        actor=actor,
        session_id="session",
        key="one",
        record=HistoryRecord(kind="message", message=Message(role="user", content="Fabricated")),
    )
    missing = (await history.read([ref.model_copy(update={"key": "absent"})], actor=actor))[0]
    unsupported = (await history.read([ref.model_copy(update={"codec": "unknown"})], actor=actor))[
        0
    ]
    assert missing.status == "missing"
    assert missing.record is None
    assert unsupported.status == "unsupported"
    assert unsupported.record is None
