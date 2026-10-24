"""Accepted summary observations carry correlation, counts and hashes only."""

import pytest
from test_agent_summaries import fixture, turn
from test_engine_telemetry import id_factory

from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types.execution import CommitUnknown

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_summary_observations_correlate_accepted_compaction_without_content(limits):
    sink, tracer = ListAuditSink(capacity=512), RecordingTracer(id_factory(), capacity=512)
    async with fixture(limits, audit=sink, tracer=tracer) as h:
        await turn(h, "first")
        result, entries = await turn(h, "second")
        assert result.stop_reason == "final"
        assert await h.engine.aclose()
        compact = next(e for e in entries if e.kind == "compaction")
        row = next(r for r in sink.records if r.kind == "x.context.compaction")
        assert row.detail["entry_position"] == compact.index
        assert row.detail["model_call_id"] == compact.facts["model_call_id"]
        span = next(
            r
            for r in tracer.records
            if r.name == "tether.model_call" and r.attributes["tether.purpose"] == "summary"
        )
        assert row.context.span_id == span.ids.span_id
        assert span.attributes["tether.input_tokens"] == 7
        assert span.attributes["tether.usage_complete"] is True
        encoded = repr(sink.records) + repr(tracer.records)
        assert "Generated summary" not in encoded
        assert "Fabricated old answer" not in encoded
        assert "summary_ref" not in row.detail


async def test_unknown_summary_commit_emits_no_accepted_compaction(limits, monkeypatch):
    sink = ListAuditSink(capacity=512)
    async with fixture(limits, audit=sink) as h:
        await turn(h, "first")
        original = h.files.append_file

        async def unknown(name, value, **kwargs):
            result = await original(name, value, **kwargs)
            if name == "journal" and any(
                e["kind"] == "compaction" for e in value.get("entries", [])
            ):
                raise CommitUnknown("fabricated_summary_unknown")
            return result

        monkeypatch.setattr(h.files, "append_file", unknown)
        with pytest.raises(CommitUnknown):
            await turn(h, "second")
        assert await h.engine.aclose()
        assert not any(r.kind == "x.context.compaction" for r in sink.records)
        assert not any(r.kind == "x.context.failed" for r in sink.records)
