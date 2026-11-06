"""Public optional ports ship finite dummies and executable conformance checks."""

import pytest

from tether.testing.approval_edits import NarrowCount, argument_transform_suite
from tether.testing.preparation import ReadyPreparation, preparation_suite
from tether.testing.settlement import MemorySettlement, settlement_suite
from tether.types.approval_edits import ApprovalEditRequest
from tether.types.execution import Actor, HistoryRef
from tether.types.preparation import PreparationRequest
from tether.types.settlement import SettlementBinding

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_preparation_dummy_conformance_and_capacity():
    request = PreparationRequest(
        actor=Actor(id="owner"),
        session_id="session",
        turn_id="turn",
        request_id="request",
        fingerprint="a" * 64,
        source_index=1,
        source=HistoryRef(
            provider="fixture", key="key", sha256="a" * 64, owner="owner", session_id="session"
        ),
        attachments=(),
    )
    participant = ReadyPreparation(capacity=1)
    await preparation_suite(participant, request)
    with pytest.raises(ValueError, match="preparation_capacity"):
        await participant.prepare(request)


async def test_semantic_argument_transform_conformance():
    await argument_transform_suite(
        NarrowCount(),
        ApprovalEditRequest(
            actor=Actor(id="owner"),
            session_id="session",
            invocation_id="invocation",
            original={"count": 3},
            shown={"count": 1},
        ),
    )


async def test_independent_settlement_conformance_and_conflict():
    actor = Actor(id="owner")
    participant = MemorySettlement()
    binding = SettlementBinding(
        settlement_id="settlement",
        invocation_id="invocation",
        participant=participant.id,
        stage_id="stage",
        owner=actor.id,
        session_id="session",
        actor_id=actor.id,
        principal=actor.id,
        spec_digest="a" * 64,
        result_digest="b" * 64,
        decision="commit",
    )
    await settlement_suite(participant, binding, actor)
    assert participant.calls == 1
    with pytest.raises(Exception, match="settlement_conflict"):
        await participant.settle(binding.model_copy(update={"decision": "rollback"}), actor)
