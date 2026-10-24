"""Context observations describe accepted projections and closed failure metadata only."""

import pytest
from test_agent_context import fixture, run
from test_engine_telemetry import id_factory

from tether.context.testing import FixedEstimator, fabricated_context
from tether.testing.telemetry import ListAuditSink, RecordingTracer
from tether.types.execution import CommitUnknown

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_context_prepared_audit_correlates_accepted_use_and_span_without_content(limits):
    sink, tracer = ListAuditSink(capacity=128), RecordingTracer(id_factory(), capacity=128)
    async with fixture(limits, audit=sink, tracer=tracer) as h:
        assert (await run(h, "Fabricated body marker")).stop_reason == "final"
        assert await h.engine.aclose()
        record = next(r for r in sink.records if r.kind == "x.context.prepared")
        use = next(e for e in h.log.entries if e.kind == "context_use")
        span = next(r for r in tracer.records if r.name == "tether.context")
        assert record.detail["entry_position"] == use.index
        assert record.detail["preparation_ordinal"] == 0
        assert record.detail["model_call_id"] == h.model.requests[0].tags.model_call_id
        assert record.context.span_id == span.ids.span_id
        assert record.detail["estimate_quality"] == "approximate"
        assert record.detail["profile_revision"] == "1"
        encoded = " ".join(r.model_dump_json() for r in sink.records) + str(tracer.records)
        for marker in ("Fabricated body marker", "Fabricated instructions", "Fabricated done"):
            assert marker not in encoded
        assert not any(r.kind == "x.context.failed" for r in sink.records)


async def test_context_failure_audit_has_known_reason_and_no_phantom_prepared_use(limits):
    sink = ListAuditSink(capacity=128)
    config = fabricated_context()["context_config"].model_copy(update={"max_source_bytes": 1})
    async with fixture(limits, context_config=config, audit=sink) as h:
        result = await run(h)
        assert result.reason == "context_exhausted"
        assert await h.engine.aclose()
        event = next(r for r in sink.records if r.kind == "x.context.failed")
        assert event.detail["reason"] == "context_exhausted"
        assert not any(r.kind == "x.context.prepared" for r in sink.records)
        ended = next(r for r in sink.records if r.kind == "turn_ended")
        assert ended.detail["stop_reason"] == "context_unavailable"


async def test_unknown_use_ack_does_not_emit_prepared_or_failure_authority(limits):
    sink = ListAuditSink(capacity=128)
    async with fixture(limits, audit=sink) as h:
        original = h.log.append

        async def append(entries):
            accepted = await original(entries)
            if any(e.kind == "context_use" for e in entries):
                raise CommitUnknown("fabricated_ack_lost")
            return accepted

        h.log.append = append
        with pytest.raises(CommitUnknown, match="fabricated_ack_lost"):
            await run(h)
        assert await h.engine.aclose()
        assert not any(r.kind in {"x.context.prepared", "x.context.failed"} for r in sink.records)
        assert any(e.kind == "context_use" for e in h.log.entries)
        assert not h.model.requests


async def test_failing_context_tracer_and_sink_remain_secondary(limits):
    class Tracer:
        def start_as_current_span(self, *args, **kwargs):
            raise RuntimeError("Fabricated private tracer marker")

        def current_ids(self):
            raise RuntimeError("Fabricated private tracer marker")

    class Sink:
        async def emit(self, **kwargs):
            raise RuntimeError("Fabricated private sink marker")

    async with fixture(limits, audit=Sink(), tracer=Tracer(), estimator=FixedEstimator(0)) as h:
        assert (await run(h)).stop_reason == "final"
        assert await h.engine.aclose()
        assert h.engine.observers.tracer.failures > 0
        assert h.engine.audit_stats.failed > 0
