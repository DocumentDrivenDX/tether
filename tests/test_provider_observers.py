"""Registered hook observations report waits, never provider output or authority."""

import asyncio
from contextlib import suppress
from threading import get_ident

import pytest
from test_gated_execution import Harness
from test_retrieval_observers import ReadTool, observers

from tether.engine.observers.metadata import Scope
from tether.engine.providers import BaseProvider
from tether.types.execution import ExecutionLimits

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def hooks(sink):
    return [r for r in sink.records if r.kind.endswith(".hook")]


async def test_agent_provider_hooks_have_closed_metadata_and_not_outputs(limits):
    class Provider(BaseProvider):
        id = "demo"

        async def prompt_section(self, session, spec):
            return "provider content marker"

        async def capabilities(self, actor, session):
            return {dict: {"private": "capability content marker"}}

    sink, tracer = observers()
    h = Harness(limits, providers=(Provider(),), audit=sink, tracer=tracer)
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    rows = hooks(sink)
    assert {r.kind for r in rows} == {"x.demo.hook"}
    assert {str(r.detail["method"]) for r in rows} == {
        "prompt_section",
        "hidden_tools",
        "before_request",
        "before_call",
        "capabilities",
        "after_call",
    }
    assert len({r.context.event_id for r in rows}) == len(rows)
    assert all(r.detail["outcome"] == "returned" for r in rows)
    assert all("entry_position" not in r.detail for r in rows)
    assert "content marker" not in repr(sink.records) + repr(tracer.records)
    spans = [r for r in tracer.records if r.name == "tether.provider_hook"]
    assert len(spans) == len(rows)
    assert all(r.finished for r in spans)
    assert {r.context.span_id for r in rows} == {r.ids.span_id for r in spans}


async def test_hook_returned_does_not_claim_validated_provider_output(limits, monkeypatch):
    provider = BaseProvider()

    async def invalid(session, spec, messages):
        return ["invalid provider content marker"]

    monkeypatch.setattr(provider, "before_request", invalid)

    sink, tracer = observers()
    h = Harness(limits, providers=(provider,), audit=sink, tracer=tracer)
    result = await h.run()
    assert (result.stop_reason, result.reason) == ("context_unavailable", "context_invalid")
    assert not h.model.requests
    assert not h.tool.calls
    assert await h.engine.aclose()
    row = next(r for r in hooks(sink) if r.detail["method"] == "before_request")
    assert row.detail["outcome"] == "returned"
    assert "invalid provider content marker" not in repr(sink.records) + repr(tracer.records)


@pytest.mark.parametrize("mode", ["sync", "async", "returned"])
async def test_hook_runs_supported_body_and_awaitable_in_copied_context(limits, mode):
    sink, tracer = observers()
    contexts = []
    caller_thread = get_ident()

    def body(*args):
        contexts.append((get_ident(), tracer.current_ids()))

    async def async_body(*args):
        return body(*args)

    def returned(*args):
        return async_body(*args)

    provider = BaseProvider()
    provider.before_call = {"sync": body, "async": async_body, "returned": returned}[mode]
    h = Harness(limits, providers=(provider,), audit=sink, tracer=tracer)
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    span = next(
        r
        for r in tracer.records
        if r.name == "tether.provider_hook" and r.attributes["tether.method"] == "before_call"
    )
    assert contexts[0][1] == span.ids
    assert (contexts[0][0] != caller_thread) is (mode == "sync")


@pytest.mark.parametrize("mutate", [False, True])
async def test_invalid_or_mutated_namespace_is_omitted_with_secondary_counter(limits, mutate):
    provider = BaseProvider()
    if not mutate:
        provider.id = "provider content marker"
    sink, tracer = observers()
    h = Harness(limits, providers=(provider,), audit=sink, tracer=tracer)
    if mutate:
        provider.id = "mutated_provider_marker"
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    assert not hooks(sink)
    assert not any(r.name == "tether.provider_hook" for r in tracer.records)
    assert h.engine.observation_failures > 0
    text = repr(sink.records) + repr(tracer.records)
    assert "provider content marker" not in text
    assert "mutated_provider_marker" not in text


async def test_unknown_hook_method_cannot_enter_metadata_namespace(limits):
    provider = BaseProvider()
    sink, tracer = observers()
    h = Harness(limits, providers=(provider,), audit=sink, tracer=tracer)
    h.engine._bind_loop()
    with h.engine.observers.steps.provider(
        Scope("owner", "session", "current", "phase"), provider, "private method marker"
    ):
        pass
    assert await h.engine.aclose()
    assert not sink.records
    assert not tracer.records
    assert h.engine.observation_failures == 1


async def test_hook_exception_is_observed_without_exception_content(limits):
    class Provider(BaseProvider):
        async def before_call(self, session, spec, call):
            raise RuntimeError("hook exception marker")

    sink, tracer = observers()
    h = Harness(limits, providers=(Provider(),), audit=sink, tracer=tracer)
    await h.run()
    assert not h.tool.calls
    assert await h.engine.aclose()
    row = next(r for r in hooks(sink) if r.detail["method"] == "before_call")
    assert row.detail["outcome"] == "raised"
    assert "hook exception marker" not in repr(sink.records) + repr(tracer.records)


@pytest.mark.parametrize("times_out", [False, True])
async def test_abandoned_hook_wait_retains_actual_operation(limits, times_out):
    entered, release = asyncio.Event(), asyncio.Event()

    class Provider(BaseProvider):
        async def before_request(self, session, spec, messages):
            entered.set()
            while not release.is_set():
                with suppress(asyncio.CancelledError):
                    await release.wait()
            return messages

    sink, tracer = observers()
    h = Harness(
        limits,
        providers=(Provider(),),
        audit=sink,
        tracer=tracer,
        limits=ExecutionLimits(hook_timeout_seconds=0.02 if times_out else 2),
    )
    task = asyncio.create_task(h.run())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if times_out:
            result = await asyncio.wait_for(task, 1)
            assert result.stop_reason == "model_denied"
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        assert h.engine.workers.busy(h.log.session.id)
        span = next(
            r
            for r in tracer.records
            if r.name == "tether.provider_hook"
            and r.attributes["tether.method"] == "before_request"
        )
        assert span.finished
        assert span.error_reason == ("failed" if times_out else "cancelled")
    finally:
        release.set()
        await asyncio.gather(*tuple(h.engine.workers.active), return_exceptions=True)
        assert await h.engine.aclose()
    row = next(r for r in hooks(sink) if r.detail["method"] == "before_request")
    assert row.detail["outcome"] == ("raised" if times_out else "cancelled")


async def test_no_observer_constructs_no_step_identity_or_packet(limits, monkeypatch):
    from tether.engine.observers import steps

    def forbidden(*args):
        raise AssertionError("unexpected metadata construction")

    monkeypatch.setattr(steps, "uuid4", forbidden)
    monkeypatch.setattr(steps, "digest", forbidden)
    h = Harness(limits, tool=ReadTool(lambda: "Fabricated"), providers=(BaseProvider(),))
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    assert h.engine.observation_failures == 0


async def test_tracer_only_opens_step_spans_without_audit_packets(limits, monkeypatch):
    sink, tracer = observers()
    h = Harness(
        limits, tool=ReadTool(lambda: "Fabricated"), providers=(BaseProvider(),), tracer=tracer
    )

    def forbidden(*args):
        raise AssertionError("unexpected audit construction")

    monkeypatch.setattr(h.engine.observers.steps, "_emit", forbidden)
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    assert not sink.records
    assert any(r.name == "tether.retrieval" for r in tracer.records)
    assert any(r.name == "tether.provider_hook" for r in tracer.records)


async def test_sink_mutation_and_failure_cannot_change_fetch_or_hook_result(limits):
    class Sink:
        async def emit(self, **packet):
            packet["detail"].clear()
            raise RuntimeError("sink content marker")

    h = Harness(
        limits, tool=ReadTool(lambda: "Fabricated"), providers=(BaseProvider(),), audit=Sink()
    )
    assert (await h.run()).stop_reason == "final"
    assert await h.engine.aclose()
    assert h.engine.audit_stats.failed > 0
