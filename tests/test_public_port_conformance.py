"""Run the same consumer contract through ordinary and alternate injected ports."""

import asyncio
from dataclasses import replace

import pytest
from public_port_fixtures import (
    FabricatedTool,
    RecordingAudit,
    RecordingContext,
    RecordingGuard,
    RecordingHistory,
    RecordingModel,
    RecordingPolicy,
    RecordingProvider,
    RecordingStore,
)
from selected_profiles_process import await_retained

from tether.client import (
    Actor,
    AgentSpec,
    Answer,
    ClientHost,
    ClientPorts,
    ClientSettings,
    ExecutionDenied,
    IntegrityError,
    ModelResponse,
    NotUnresolved,
    ProvidedStorage,
    ToolCall,
    UnknownSession,
    Unsupported,
    VolatileSettings,
)
from tether.client.testing import fabricated_model_context, fabricated_storage, scripted_model

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def consumer_contract(client):
    """Every advertised method, with typed failure checks for unavailable states."""
    assert client.capabilities.methods["restore"] == "unsupported"
    assert client.capabilities.methods["resume"] == "unsupported"
    session = await client.create(ref="fabricated", spec="demo", title="Original")
    assert (await client.find("fabricated")).id == session.id
    assert (await client.list()).items[0].id == session.id
    assert (await client.get(session.id)).summary.title == "Original"
    snapshot = await client.snapshot(session.id)
    await _update_and_read_summary(client, session.id, snapshot.seq)
    assert await client.pending(session.id) is None
    with pytest.raises(Unsupported, match="unsupported_store"):
        await client.resume(session.id)
    with pytest.raises(NotUnresolved, match="not_unresolved"):
        await client.resolve(session.id, "missing", "not_completed", reason="Fabricated")
    await approved_turn(client, session.id)
    await committed_reads_and_close(client, session.id)
    return session.id


async def _update_and_read_summary(client, session_id, since) -> None:
    stream = None
    next_event = None
    next_event_coro = None
    reader_observed = False
    primary = None
    try:
        stream = client.follow(session_id=session_id, since=since)
        next_event_coro = anext(stream)
        try:
            next_event = asyncio.create_task(next_event_coro)
        except BaseException:
            next_event_coro.close()
            next_event_coro = None
            raise
        changed = await client.update(session_id, title="Changed", tags=("fabricated",))
        assert changed.title == "Changed"
        assert changed.tags == ("fabricated",)
        try:
            event = await next_event
        except BaseException:
            reader_observed = True
            raise
        reader_observed = True
        assert event.summary.id == session_id
    except BaseException as error:
        primary = error
        raise
    finally:
        cleanup = asyncio.create_task(_close_consumer_stream(next_event, stream, reader_observed))
        try:
            await await_retained(cleanup)
        except BaseException as cleanup_error:
            if primary is not None and cleanup_error is primary:
                pass
            elif primary is not None:
                raise BaseExceptionGroup(
                    "consumer_contract_test_and_cleanup_failed", [primary, cleanup_error]
                ) from primary
            else:
                raise


async def _close_consumer_stream(reader, stream, reader_observed: bool) -> None:
    errors: list[BaseException] = []
    if reader is not None:
        if not reader.done():
            reader.cancel()
        outcome = await asyncio.gather(reader, return_exceptions=True)
        if not reader_observed:
            errors.extend(
                value
                for value in outcome
                if isinstance(value, BaseException)
                and not isinstance(value, asyncio.CancelledError)
            )
    if stream is not None:
        try:
            await stream.aclose()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise BaseExceptionGroup("consumer_contract_cleanup_failed", errors)


async def approved_turn(client, sid):
    first = await client.send(sid, "Fabricated question", request_id="one", spec="demo")
    result = await first.result()
    assert result.stop_reason == "confirmation_required"
    pending = await client.pending(sid)
    assert pending is not None
    assert await client.cancel_turn(sid, first.turn_id) is False
    continued = await client.confirm(
        sid,
        pending.invocation_id,
        Answer(approved=True),
    )
    assert continued is not None
    final = await continued.result()
    assert final.stop_reason == "final"
    assert final.messages[-1].content == "Fabricated final"
    assert await client.turn_result(sid, continued.turn_id) == final
    assert await client.cancel(sid) is False
    assert await client.cancel_turn(sid, first.turn_id) is False
    return first


async def committed_reads_and_close(client, sid):
    entries = (await client.entries(sid)).items
    assert entries
    history = await client.history(sid)
    assert history.items
    calls = tuple(
        str(entry.facts["model_call_id"]) for entry in entries if entry.kind == "model_call"
    )
    assert calls
    assert await client.call_detail(sid, calls)
    assert (await client.effects(sid)).items == ()
    original = next(entry for entry in entries if entry.history_ref is not None)
    with pytest.raises(IntegrityError, match="history_link_cycle"):
        await client.link_history(
            sid, original.index, original.history_ref, original.history_ref, key="cycle"
        )
    assert (await client.close(sid)).status == "closed"
    with pytest.raises(Unsupported, match="archive_unconfigured"):
        await client.restore(sid)


@pytest.mark.parametrize("injected", [False, True], ids=["ordinary", "alternate-ports"])
async def test_public_client_conformance_across_complete_injected_ports(
    client_limits, limits, injected
):
    """@covers US-065-AC2 @covers US-065-AC6"""
    audit, guard, strategy = RecordingAudit(), RecordingGuard(), RecordingContext()
    context = fabricated_model_context()
    tool, policy, provider = FabricatedTool(), RecordingPolicy(), RecordingProvider()
    model = RecordingModel(
        scripted_model(
            [
                ModelResponse(
                    tool_calls=(ToolCall(id="call", name=tool.name, arguments={"label": "fake"}),),
                    finish_reason="tool_calls",
                ),
                ModelResponse(content="Fabricated final", finish_reason="stop"),
            ]
        )
    )
    config = ClientSettings(
        model=model,
        storage=VolatileSettings(),
        policy=policy,
        client_limits=client_limits,
        model_limits=limits,
        context_config=context["context_config"],
        estimator=context["estimator"],
        specs=(
            AgentSpec(
                id="demo",
                version="1",
                title="Fabricated",
                instructions="Fabricated",
                model="demo",
                tools=(tool.name,),
            ),
        ),
        tools=(tool,),
        resume="off",
        default_spec="demo",
    )
    async with fabricated_storage() as storage:
        store, history = RecordingStore(storage.store), RecordingHistory(storage.history)
        if injected:
            config = replace(config, storage=ProvidedStorage(store=store, history=history))
            host = await ClientHost.from_ports(
                config,
                ports=ClientPorts(
                    providers=(provider,), guards=(guard,), audit=audit, context=strategy
                ),
            )
        else:
            host = await ClientHost.open(config)
        async with host:
            client = host.client(Actor(id="fabricated-owner"))
            sid = await consumer_contract(client)
            with pytest.raises(UnknownSession, match="unknown_session"):
                await host.client(Actor(id="foreign")).get(sid)
            policy.revoked = True
            with pytest.raises(ExecutionDenied, match="policy_denied"):
                await client.get(sid)
            with pytest.raises(ExecutionDenied, match="policy_denied"):
                await client.create(spec="demo")
        assert tool.runs == ["fake"]
        assert len(model.requests) == 2
        assert policy.requests
        if injected:
            assert {
                "create",
                "get",
                "list",
                "find",
                "update",
                "lookup",
                "snapshot",
                "entries",
                "turn",
                "changes",
                "effects",
            } <= store.calls.keys()
            assert store.calls["aclose"] == 0
            assert history.calls["put"]
            assert history.calls["read"]
            # Writable history stores canonical exchanges via put. bind_exchange
            # is the distinct externally-recorded profile, not this fixture.
            assert history.capabilities.externally_recorded is False
            assert history.calls["bind_exchange"] == 0
            assert provider.calls["before_request"] == 2
            assert provider.calls["before_call"] == 2
            assert provider.calls["after_call"] == 1
            assert len(guard.calls) == 1
            assert audit.events
            assert len(strategy.calls) == 2
            assert await storage.store.get(sid, "fabricated-owner") is not None
