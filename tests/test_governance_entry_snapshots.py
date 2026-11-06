"""Current source reads use one authorized journal prefix per lookup."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from tether.engine.governance import AllowAll, Governance, PolicyDenied
from tether.testing.execution import MemoryHistory
from tether.types import Message
from tether.types._entry_snapshot import EntrySnapshot
from tether.types.execution import (
    Actor,
    Decision,
    Entry,
    ExecutionDenied,
    ExecutionLimits,
    NewEntry,
    SessionState,
)
from tether.types.history import HistoryLink
from tether.types.lifetime import WorkerLifetime


class CountingLog:
    durability = "none"

    def __init__(self, *, snapshot_view: bool = False) -> None:
        self.session = SessionState(id="session", owner="owner")
        self.turn_id: str | None = None
        self.workers = WorkerLifetime()
        self._entries: list[Entry] = []
        self.entry_reads = 0
        self.snapshot_view = snapshot_view

    @property
    def entries(self) -> Sequence[Entry]:
        self.entry_reads += 1
        entries = tuple(entry.model_copy(deep=True) for entry in self._entries)
        return EntrySnapshot(entries) if self.snapshot_view else entries

    async def append(self, entries: Sequence[NewEntry]) -> tuple[Entry, ...]:
        accepted = tuple(
            Entry(**entry.model_dump(), index=len(self._entries) + offset, at=datetime.now(UTC))
            for offset, entry in enumerate(entries)
        )
        self._entries.extend(entry.model_copy(deep=True) for entry in accepted)
        return tuple(entry.model_copy(deep=True) for entry in accepted)


class CountingHistory(MemoryHistory):
    def __init__(self) -> None:
        super().__init__()
        self.read_keys: list[str] = []

    async def read(self, ref, *, actor: Actor, session_id: str) -> Message:
        self.read_keys.append(ref.key)
        return await super().read(ref, actor=actor, session_id=session_id)


@pytest.mark.parametrize("snapshot_view", [False, True], ids=["sequence", "snapshot"])
def test_governance_read_uses_one_fresh_prefix_for_source_and_link_selection(
    snapshot_view: bool,
) -> None:
    async def scenario() -> None:
        actor = Actor(id="owner")
        log = CountingLog(snapshot_view=snapshot_view)
        history = CountingHistory()
        original_message = Message(role="user", content="Fabricated message")
        replacement_message = Message(role="user", content="Fabricated message")
        original = await history.put(
            actor=actor, session_id="session", key="original", message=original_message
        )
        replacement = await history.put(
            actor=actor, session_id="session", key="replacement", message=replacement_message
        )
        await log.append((NewEntry(kind="message", history_ref=original),))
        link = HistoryLink(
            message_index=0,
            previous=original,
            verified=replacement,
            canonical_digest=original.sha256,
        )
        await log.append((NewEntry(kind="history_linked", facts=link.model_dump(mode="json")),))
        gov = Governance(
            actor, log, history, AllowAll(), "turn", ExecutionLimits(max_history_link_hops=4)
        )
        reads_before = log.entry_reads

        result = await gov.read(original, source_index=0)

        assert result.content == "Fabricated message"
        assert history.read_keys == ["replacement"]
        # One view validates the accepted authorization append; one fresh view pins
        # source lookup, link lookup, and through_index after that authorization.
        assert log.entry_reads - reads_before == 2

        latest = await history.put(
            actor=actor, session_id="session", key="latest", message=replacement_message
        )
        next_link = HistoryLink(
            message_index=0,
            previous=replacement,
            verified=latest,
            canonical_digest=replacement.sha256,
        )
        await log.append(
            (NewEntry(kind="history_linked", facts=next_link.model_dump(mode="json")),)
        )
        result = await gov.read(original, source_index=0)

        assert result.content == "Fabricated message"
        assert history.read_keys == ["replacement", "latest"]

    asyncio.run(scenario())


@pytest.mark.parametrize("snapshot_view", [False, True], ids=["sequence", "snapshot"])
def test_governance_read_preserves_duplicate_source_refusal(snapshot_view: bool) -> None:
    async def scenario() -> None:
        actor = Actor(id="owner")
        log = CountingLog(snapshot_view=snapshot_view)
        history = CountingHistory()
        message = Message(role="user", content="Fabricated duplicate")
        ref = await history.put(actor=actor, session_id="session", key="duplicate", message=message)
        await log.append(
            (
                NewEntry(kind="message", history_ref=ref),
                NewEntry(kind="message", history_ref=ref),
            )
        )
        gov = Governance(actor, log, history, AllowAll(), "turn", ExecutionLimits())
        reads_before = log.entry_reads

        with pytest.raises(ExecutionDenied, match="unreachable_source"):
            await gov.read(ref)

        assert history.read_keys == []
        assert log.entry_reads - reads_before == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("snapshot_view", [False, True], ids=["sequence", "snapshot"])
def test_governance_commit_reuses_one_accepted_prefix_for_batch_membership(
    snapshot_view: bool,
) -> None:
    async def scenario() -> None:
        log = CountingLog(snapshot_view=snapshot_view)
        gov = Governance(
            Actor(id="owner"), log, MemoryHistory(), AllowAll(), "turn", ExecutionLimits()
        )
        reads_before = log.entry_reads

        accepted = await gov.commit(
            (NewEntry(kind="fabricated-first"), NewEntry(kind="fabricated-second"))
        )

        assert tuple(entry.kind for entry in accepted) == (
            "fabricated-first",
            "fabricated-second",
        )
        assert log.entry_reads - reads_before == 1

    asyncio.run(scenario())


def test_each_read_reauthorizes_before_snapshot_and_never_reuses_old_authority() -> None:
    class RevocablePolicy(AllowAll):
        denied = False

        async def authorize(self, request):
            if self.denied:
                return Decision(allowed=False, reason="denied")
            return Decision(allowed=True, reason="allowed")

    async def scenario() -> None:
        actor = Actor(id="owner")
        log = CountingLog(snapshot_view=True)
        history = CountingHistory()
        message = Message(role="user", content="Fabricated current message")
        ref = await history.put(actor=actor, session_id="session", key="current", message=message)
        await log.append((NewEntry(kind="message", history_ref=ref),))
        policy = RevocablePolicy()
        gov = Governance(actor, log, history, policy, "turn", ExecutionLimits())

        assert await gov.read(ref, source_index=0) == message
        policy.denied = True
        reads_before = log.entry_reads
        with pytest.raises(PolicyDenied, match="denied"):
            await gov.read(ref, source_index=0)

        assert history.read_keys == ["current"]
        # The denied authorization is durably appended and validated, but no
        # source snapshot is taken and no history content is opened afterward.
        assert log.entry_reads - reads_before == 1

    asyncio.run(scenario())


def test_changed_provider_payload_refuses_after_authorized_source_lookup() -> None:
    class ChangedHistory(CountingHistory):
        async def read(self, ref, *, actor: Actor, session_id: str) -> Message:
            self.read_keys.append(ref.key)
            return Message(role="user", content="Fabricated changed payload")

    async def scenario() -> None:
        actor = Actor(id="owner")
        log = CountingLog(snapshot_view=True)
        history = ChangedHistory()
        message = Message(role="user", content="Fabricated expected payload")
        ref = await history.put(actor=actor, session_id="session", key="digest", message=message)
        await log.append((NewEntry(kind="message", history_ref=ref),))
        gov = Governance(actor, log, history, AllowAll(), "turn", ExecutionLimits())

        with pytest.raises(ExecutionDenied, match="source_integrity"):
            await gov.read(ref, source_index=0)

        assert history.read_keys == ["digest"]

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["index", "facts"], ids=["wrong-index", "wrong-facts"])
def test_commit_refuses_accepted_value_missing_from_current_prefix(corruption: str) -> None:
    class CorruptAcceptedLog(CountingLog):
        async def append(self, entries):
            accepted = await super().append(entries)
            if not accepted:
                return accepted
            changed = accepted[0].model_copy(
                update={"index": 1000} if corruption == "index" else {"facts": {"x": 1}}
            )
            return (changed, *accepted[1:])

    async def scenario() -> None:
        log = CorruptAcceptedLog(snapshot_view=True)
        gov = Governance(
            Actor(id="owner"), log, MemoryHistory(), AllowAll(), "turn", ExecutionLimits()
        )

        with pytest.raises(ExecutionDenied, match="commit_not_accepted"):
            await gov.commit((NewEntry(kind="fabricated-fact"),))

    asyncio.run(scenario())
