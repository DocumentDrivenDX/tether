"""Adversarial input mutation cannot rewrite the public testkit's private oracle."""

from contextlib import asynccontextmanager

import pytest

from tether.testing.conformance import ArchiveFixture, ConformanceFailure, archive_suite
from tether.types.archive import ArchiveCapabilities, ArchivedSession
from tether.types.execution import Actor, EntryConflict
from tether.types.history import HistoryValue, StoredHistory
from tether.types.storage import EntryPage, Page, UnknownSession, Unsupported

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class MutatingArchive:
    """Deliberately broken adapter that changes caller-owned metadata and echoes it."""

    def __init__(self, sample: ArchivedSession | None = None):
        self.sample = sample
        self.capabilities = ArchiveCapabilities(
            kind="authoritative",
            writable=sample is None,
            lossless=True,
            restorable=True,
            content="full",
        )

    async def archive(self, session):
        if (
            self.sample is not None
            and self.sample.bundle.session.entries != session.bundle.session.entries
        ):
            raise EntryConflict("fabricated_prefix_conflict")
        session.view.settings["provider_mutation"] = True
        self.sample = session

    async def get(self, session_id, *, actor):
        if self.sample is None or actor.id != self.sample.view.summary.owner:
            return None
        return self.sample.view

    async def list(self, query, *, cursor, limit, actor):
        sample = self.sample
        if sample is None or actor.id != sample.view.summary.owner:
            return Page(items=())
        return Page(items=(sample.view.summary,))

    async def entries(self, session_id, *, actor, start, limit, reverse):
        sample = self.sample
        if sample is None or actor.id != sample.view.summary.owner:
            raise UnknownSession("fabricated_unknown")
        return EntryPage(
            items=sample.bundle.session.entries[start : start + limit], next_start=None
        )

    async def read(self, refs, *, actor):
        sample = self.sample
        assert sample is not None
        stored = [StoredHistory.model_validate(raw) for raw in sample.bundle.records]
        return tuple(
            HistoryValue(
                ref=ref, status="ready", record=next(s.record for s in stored if s.ref == ref)
            )
            if ref.owner == actor.id
            else HistoryValue(ref=ref, status="denied")
            for ref in refs
        )

    async def restore(self, session_id, *, actor):
        if self.sample is None or actor.id != self.sample.view.summary.owner:
            raise UnknownSession("fabricated_unknown")
        return self.sample

    async def call_detail(self, request_ids, *, actor):
        raise Unsupported("fabricated_unsupported")


@pytest.mark.parametrize("boundary", ["factory", "sink"])
async def test_mutating_factory_or_sink_cannot_change_expected_archive(boundary):
    @asynccontextmanager
    async def factory(sample):
        if boundary == "factory":
            sample.view.settings["provider_mutation"] = True
            source = MutatingArchive(sample)
            sink = None
        else:
            source = MutatingArchive()
            sink = source
        yield ArchiveFixture(source, Actor(id=sample.view.summary.owner), Actor(id="foreign"), sink)

    with pytest.raises(ConformanceFailure, match="archive_view_lossless"):
        await archive_suite(factory)


async def test_unmutated_source_fixture_passes_the_same_lossless_checks():
    @asynccontextmanager
    async def factory(sample):
        source = MutatingArchive(sample)
        yield ArchiveFixture(source, Actor(id=sample.view.summary.owner), Actor(id="foreign"))

    report = await archive_suite(factory)
    assert "archive_owner_isolation" in report.checks


async def test_conflicting_write_mutation_cannot_poison_the_private_oracle():
    class ConflictMutationArchive(MutatingArchive):
        async def archive(self, session):
            if (
                self.sample is not None
                and self.sample.bundle.session.entries != session.bundle.session.entries
            ):
                record = session.bundle.records[0]["record"]
                assert isinstance(record, dict)
                message = record["message"]
                assert isinstance(message, dict)
                message["content"] = "Fabricated candidate mutation."
                raise EntryConflict("fabricated_prefix_conflict")
            self.sample = session

    @asynccontextmanager
    async def factory(sample):
        source = ConflictMutationArchive()
        yield ArchiveFixture(
            source, Actor(id=sample.view.summary.owner), Actor(id="foreign"), source
        )

    report = await archive_suite(factory)
    assert "archive_content" in report.checks
