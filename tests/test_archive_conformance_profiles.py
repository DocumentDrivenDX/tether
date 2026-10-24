"""Read-only/derived archive profiles and a disclosure-bug negative control."""

from contextlib import asynccontextmanager

import pytest

from tether.stores.archive import InMemoryArchive
from tether.testing.conformance import ArchiveFixture, ConformanceFailure, archive_suite
from tether.types.archive import ArchiveCapabilities, ArchiveLimits, NotRestorable
from tether.types.execution import Actor
from tether.types.history import HistoryValue

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def archive():
    return InMemoryArchive(ArchiveLimits(max_sessions=10, max_entries=100, max_bytes=1048576))


async def test_read_only_archive_source_does_not_report_a_write_check():
    @asynccontextmanager
    async def factory(sample):
        source = archive()
        await source.archive(sample)
        source.capabilities = source.capabilities.model_copy(update={"writable": False})
        yield ArchiveFixture(source, Actor(id=sample.view.summary.owner), Actor(id="foreign"))

    report = await archive_suite(factory)
    assert "archive_idempotency" not in report.checks
    assert report.capabilities["writable"] is False


class PartialArchive(InMemoryArchive):
    async def get(self, session_id, *, actor):
        view = await super().get(session_id, actor=actor)
        if view is not None:
            return view.model_copy(
                update={"summary": view.summary.model_copy(update={"partial": True})}
            )
        return None

    async def entries(self, session_id, *, actor, start, limit, reverse):
        page = await super().entries(
            session_id, actor=actor, start=start, limit=limit, reverse=reverse
        )
        return page.model_copy(update={"partial": True})

    async def read(self, refs, *, actor):
        return tuple(
            HistoryValue(ref=ref, status="unsupported" if ref.owner == actor.id else "denied")
            for ref in refs
        )

    async def restore(self, session_id, *, actor):
        raise NotRestorable("fabricated_derived_archive")


async def test_derived_facts_only_archive_is_partial_and_cannot_restore():
    @asynccontextmanager
    async def factory(sample):
        source = PartialArchive(ArchiveLimits(max_sessions=10, max_entries=100, max_bytes=1048576))
        await source.archive(sample)
        source.capabilities = ArchiveCapabilities(
            kind="derived", writable=False, lossless=False, restorable=False, content="facts_only"
        )
        yield ArchiveFixture(source, Actor(id=sample.view.summary.owner), Actor(id="foreign"))

    report = await archive_suite(factory)
    assert report.capabilities["kind"] == "derived"
    assert "archive_idempotency" not in report.checks


async def test_testkit_rejects_foreign_archive_content_disclosure():
    class BrokenArchive(InMemoryArchive):
        async def read(self, refs, *, actor):
            return await super().read(refs, actor=Actor(id=refs[0].owner))

    @asynccontextmanager
    async def factory(sample):
        source = BrokenArchive(ArchiveLimits(max_sessions=10, max_entries=100, max_bytes=1048576))
        yield ArchiveFixture(
            source, Actor(id=sample.view.summary.owner), Actor(id="foreign"), source
        )

    with pytest.raises(ConformanceFailure, match="archive_content_owner"):
        await archive_suite(factory)


async def test_independent_sink_and_credentialled_source_keep_isolated_callers():
    from typing import cast

    from tether.types.archive import ArchiveSource

    class CredentialProbe:
        def __init__(self):
            self.calls = 0

        async def authorization(self):
            self.calls += 1

    credentials = {"owner": CredentialProbe(), "foreign": CredentialProbe()}
    supplied = []

    class SourceFacade:
        def __init__(self, source, actors):
            self.source, self.actors = source, actors
            self.capabilities = source.capabilities.model_copy(update={"writable": False})

        def __getattr__(self, name):
            operation = getattr(self.source, name)

            async def call(*args, actor, **kwargs):
                expected = self.actors[actor.id]
                assert actor is not expected
                assert actor.credential is expected.credential
                assert actor.attributes == {"nested": {"original": True}}
                actor.attributes["nested"]["changed"] = True
                await actor.credential.authorization()
                return await operation(*args, actor=actor, **kwargs)

            return call

    @asynccontextmanager
    async def factory(sample):
        source = archive()
        owner = Actor(
            id=sample.view.summary.owner,
            credential=credentials["owner"],
            attributes={"nested": {"original": True}},
        )
        foreign = Actor(
            id="foreign",
            credential=credentials["foreign"],
            attributes={"nested": {"original": True}},
        )
        supplied.extend([owner, foreign])
        facade = SourceFacade(source, {owner.id: owner, foreign.id: foreign})
        yield ArchiveFixture(cast(ArchiveSource, facade), owner, foreign, source)

    report = await archive_suite(factory)
    assert report.capabilities["writable"] is False
    assert report.sink_capabilities is not None
    assert report.sink_capabilities["writable"] is True
    assert "archive_idempotency" in report.checks
    assert all(actor.attributes == {"nested": {"original": True}} for actor in supplied)
    assert all(probe.calls > 0 for probe in credentials.values())


async def test_falsey_sink_is_a_valid_independent_provider():
    class FalseyArchive(InMemoryArchive):
        def __bool__(self):
            return False

    @asynccontextmanager
    async def factory(sample):
        source = FalseyArchive(ArchiveLimits(max_sessions=10, max_entries=100, max_bytes=1048576))
        yield ArchiveFixture(
            source, Actor(id=sample.view.summary.owner), Actor(id="foreign"), source
        )

    report = await archive_suite(factory)
    assert "archive_idempotency" in report.checks
