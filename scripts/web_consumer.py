"""Standalone fabricated consumers executed by isolated installed Python only."""

# @covers US-065-AC1 @covers US-069-AC1: run by the base-only installed profile.
CORE = """
import asyncio
import tempfile
from dataclasses import replace
from pathlib import Path
from tether.client import (
    Actor, AgentSpec, ClientHost, ClientLimits, ClientSettings,
    LocalJsonlSettings, ModelExecutionLimits, ModelResponse, VolatileSettings,
)
from tether.client.testing import demo_policy, fabricated_model_context, scripted_model

async def exercise():
    context = fabricated_model_context()
    settings = ClientSettings(
        model=scripted_model([
            ModelResponse(content="Fabricated installed answer", finish_reason="stop"),
        ]),
        storage=VolatileSettings(), policy=demo_policy(),
        client_limits=ClientLimits(
            max_page_size=100, max_call_ids=8, max_read_bytes=1048576,
            max_lookup_entries=1000, read_timeout_seconds=5,
            heartbeat_interval_seconds=1, shutdown_timeout_seconds=5,
            follower_queue_size=32, max_followers=8, max_active_turns=8,
            max_request_bytes=65536,
        ),
        model_limits=ModelExecutionLimits(
            max_response_bytes=8192, max_tool_argument_bytes=512,
            max_attempts=2, request_timeout_seconds=5, max_retry_delay_seconds=.01,
        ),
        context_config=context["context_config"], estimator=context["estimator"],
        specs=(AgentSpec(id="demo", version="1", title="Installed fabricated consumer",
            instructions="Respond with fabricated data only", model="demo"),),
        default_spec="demo", resume="off",
    )
    actor = Actor(id="fabricated-installer")
    with tempfile.TemporaryDirectory() as directory:
        durable = replace(settings, storage=LocalJsonlSettings(Path(directory)), resume="report")
        async with await ClientHost.open(durable) as host:
            client = host.client(actor)
            session = await client.create(spec="demo")
            turn = await client.send(session.id, "Fabricated installed request", spec="demo")
            result = await turn.result()
            if result.messages[-1].content != "Fabricated installed answer":
                raise RuntimeError("installed_result_mismatch")
        if not host.drained:
            raise RuntimeError("installed_host_did_not_drain")
        async with await ClientHost.open(replace(durable, model=scripted_model([]))) as reopened:
            if await reopened.client(actor).turn_result(session.id, turn.turn_id) != result:
                raise RuntimeError("installed_result_recovery_failed")
    print("Installed public host: construction, send, drain, reopen and result recovery passed")

asyncio.run(exercise())
"""

UI = """
import asyncio
from tether_web_ui import UiSettings, create_static_app

async def exercise_ui():
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    app = create_static_app(settings=UiSettings())
    await app({"type": "http", "method": "GET", "path": "/", "headers": []}, receive, send)
    statuses = [
        message["status"] for message in messages
        if message["type"] == "http.response.start"
    ]
    if statuses != [200]:
        raise RuntimeError("installed_static_assets_unavailable:" + repr(statuses))
    print("Installed UI: packaged default assets served")

asyncio.run(exercise_ui())
"""

# @covers US-065-AC8: the historical advanced composition remains executable.
# This fixture deliberately retains the historical implementation imports; new
# ordinary consumer proof is the separate public-only CORE program above.
LEGACY = """
import asyncio
from tether import Tether
from tether.client import Actor, AgentSpec, ClientLimits, ModelExecutionLimits, ModelResponse
from tether.client.testing import fabricated_model_context, fabricated_storage, scripted_model
from tether.engine import Engine
from tether.history.messages import MessageHistoryAdapter

async def exercise_legacy():
    context = fabricated_model_context()
    limits = ClientLimits(
        max_page_size=100, max_call_ids=8, max_read_bytes=1048576,
        max_lookup_entries=1000, read_timeout_seconds=5,
        heartbeat_interval_seconds=1, shutdown_timeout_seconds=5,
        follower_queue_size=32, max_followers=8, max_active_turns=8,
        max_request_bytes=65536,
    )
    async with fabricated_storage() as storage:
        engine = Engine(
            model=scripted_model([
                ModelResponse(content="Fabricated legacy answer", finish_reason="stop"),
            ]),
            history=MessageHistoryAdapter(storage.history), history_provider=storage.history,
            model_limits=ModelExecutionLimits(
                max_response_bytes=8192, max_tool_argument_bytes=512,
                max_attempts=2, request_timeout_seconds=5, max_retry_delay_seconds=.01,
            ),
            specs=(AgentSpec(id="demo", version="1", title="Legacy fixture",
                instructions="Fabricated data only", model="demo"),),
            **context,
        )
        async with Tether(engine=engine, store=storage.store, history=storage.history,
            limits=limits, resume="off", default_spec="demo") as legacy:
            client = legacy.client(Actor(id="fabricated-legacy"))
            session = await client.create(spec="demo")
            handle = await client.send(session.id, "Fabricated request", spec="demo")
            result = await handle.result()
            if result.messages[-1].content != "Fabricated legacy answer":
                raise RuntimeError("installed_legacy_result_mismatch")
        if not legacy.drained:
            raise RuntimeError("installed_legacy_not_drained")
    print("Installed historical Tether composition passed")

asyncio.run(exercise_legacy())
"""
