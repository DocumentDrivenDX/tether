"""Inspect release artifacts, then import the built wheel in a clean base environment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from dependency_policy import PROVIDER_DISTRIBUTIONS
from process import run_command

LICENSES = {"LICENSE-MIT", "LICENSE-APACHE", "NOTICE"}
SMOKE = """
import importlib.metadata as md
import importlib.resources as resources
import json
from pathlib import Path
import sys
import socket
import asyncio
def deny_network(*args, **kwargs):
    raise AssertionError("base import attempted network I/O")
socket.socket.connect = deny_network
socket.create_connection = deny_network
import tether
from pydantic_ai import Agent
from tether.types import ModelRequest, ModelResponse, RequestTags, Message, ModelExecutionLimits
from tether.models import complete
from tether.models.testing import ScriptedModel, RecordingModel, ReplayModel, ListAttemptRecorder
from tether.runtime.pydantic_ai.bridge import run_agent
assert Path(tether.__file__).is_relative_to(Path(sys.prefix))
assert resources.files('tether').joinpath('py.typed').is_file()
installed = {d.metadata['Name'].lower().replace('_', '-') for d in md.distributions()}
sdks = installed.intersection(json.loads(sys.argv[1]))
extra = sys.argv[2]
assert sdks == ({'openai'} if extra == 'openai' else set())
if extra:
    from tether.adapters.openai_compat import OpenAICompatModel
async def smoke():
    tags = RequestTags(session_id='fabricated-session', turn_id='fabricated-turn',
        model_call_id='fabricated-call', principal='fabricated', spec_id='demo', spec_version='1')
    request = ModelRequest(model='demo', tags=tags, system='Fabricated instructions.',
        messages=(Message(role='user', content='Fabricated request.'),), max_output_tokens=64,
        identity_mode='anonymous', attempts=ListAttemptRecorder(tags))
    response = ModelResponse(content='Fabricated answer.', finish_reason='stop')
    lines = []
    async def writer(line): lines.append(line)
    recording = RecordingModel(ScriptedModel([response]), writer, fabricated=True)
    assert await complete(recording, request) == response
    assert await complete(ReplayModel(''.join(lines)), request) == response
    async def prepare(request): return request
    async def authorize_history(): pass
    limits = ModelExecutionLimits(max_response_bytes=8192, max_tool_argument_bytes=512,
        max_attempts=1, request_timeout_seconds=10, max_retry_delay_seconds=.01)
    result = await run_agent(ScriptedModel([response]), request, limits,
        prepare=prepare, authorize_history=authorize_history)
    assert result.response == response
    from pydantic import BaseModel
    from tether.engine import Engine
    from tether.testing.execution import MemoryHistory, MemoryTurnLog
    from tether.types import ToolCall
    from tether.types.execution import Actor, AgentSpec, Answer, SessionState
    class Args(BaseModel):
        count: int
    class FabricatedTool:
        name, version, description = 'count', '1', 'Fabricated count'
        input_model = Args
        requires, traits = frozenset(), {}
        model_visible, always_ask = True, False
        effect, replay = 'effectful', 'unsafe'
        timeout_seconds = definition_hash = None
        calls = 0
        def asks_first(self, ctx, args): return True
        async def run(self, ctx, args):
            self.calls += 1
            assert args.count == 3
            return 'Fabricated tool result'
    tool, actor = FabricatedTool(), Actor(id='fabricated')
    log = MemoryTurnLog(SessionState(id='fabricated', owner=actor.id, approval='manual'))
    proposed = ModelResponse(finish_reason='tool_calls', tool_calls=(
        ToolCall(id='fabricated-call', name='count', arguments={'count': '3'}),))
    engine = Engine(model=ScriptedModel([proposed, response]), history=MemoryHistory(),
        model_limits=limits, attempt_factory=ListAttemptRecorder, tools=(tool,))
    spec = AgentSpec(id='demo', version='1', title='Demo', instructions='Fabricated.',
        model='demo', tools=('count',))
    pending = await engine.run_turn(actor, log, 'Fabricated request', spec=spec)
    assert pending.stop_reason == 'confirmation_required' and tool.calls == 0
    finished = await engine.answer(actor, log, pending.pending[0].invocation_id,
        Answer(approved=True))
    assert finished.stop_reason == 'final' and tool.calls == 1
    assert await engine.aclose()
    import tempfile
    from tether.history.jsonl import JsonlHistory
    from tether.history.messages import MessageHistoryAdapter
    from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
    from tether.types.storage import SessionInit
    from tether.types.client import ClientLimits
    from tether import Tether
    client_limits = ClientLimits(max_page_size=100, max_call_ids=8,
        max_read_bytes=1048576, max_lookup_entries=1000, read_timeout_seconds=10,
        heartbeat_interval_seconds=1, shutdown_timeout_seconds=10,
        follower_queue_size=8, max_followers=8, max_active_turns=8, max_request_bytes=65536)
    with tempfile.TemporaryDirectory(prefix='tether-installed-') as directory:
        root = await JsonlRoot.open(Path(directory))
        store = await JsonlSessionStore.open(root)
        provider = await JsonlHistory.open(root)
        session = await store.create(actor.id, SessionInit(settings={'approval': 'manual'}))
        first = Engine(model=ScriptedModel([proposed]), history=MessageHistoryAdapter(provider),
            history_provider=provider, model_limits=limits, tools=(tool,), specs=(spec,))
        async with store.turn(session.id, actor.id, turn_id='installed-first') as durable_log:
            waiting = await first.run_turn(
                actor, durable_log, 'Fabricated durable request', spec=spec)
        assert waiting.stop_reason == 'confirmation_required' and tool.calls == 1
        invocation = waiting.pending[0].invocation_id
        async with Tether(engine=first, store=store, history=provider,
                          limits=client_limits) as app:
            pending_view = await app.client(actor).pending(session.id)
            assert pending_view.arguments == {'count': 3}
            assert pending_view.history_status == 'ready'
            assert (await app.client(actor).snapshot(session.id)).view.pending.arguments is None
        assert app.drained
        assert await first.aclose()
        assert await root.aclose()
        root = await JsonlRoot.open(Path(directory))
        try:
            store = await JsonlSessionStore.open(root)
            provider = await JsonlHistory.open(root)
            second = Engine(
                model=ScriptedModel([response]), history=MessageHistoryAdapter(provider),
                history_provider=provider, model_limits=limits, tools=(tool,), specs=(spec,))
            async with store.turn(session.id, actor.id, turn_id='installed-answer') as durable_log:
                finished = await second.answer(
                    actor, durable_log, invocation, Answer(approved=True))
            assert finished.stop_reason == 'final' and tool.calls == 2
            snapshot = await store.snapshot(session.id, actor.id)
            assert not snapshot.view.summary.pending
            assert snapshot.view.summary.unresolved_effect_count == 0
            assert await second.aclose()
            reader = Engine(model=ScriptedModel([]), history=MessageHistoryAdapter(provider),
                history_provider=provider, model_limits=limits, tools=(tool,), specs=(spec,))
            async with Tether(engine=reader, store=store, history=provider,
                              limits=client_limits) as app:
                client = app.client(actor)
                assert (await client.get(session.id)).summary.id == session.id
                entries = await client.entries(session.id)
                calls = tuple(e.facts['model_call_id'] for e in entries.items
                              if e.kind == 'model_call')
                details = await client.call_detail(session.id, calls)
                assert len(details) == 2
                assert all(detail.status == 'ready' for detail in details)
                assert all(slot.value.status == 'ready'
                           for slot in (await client.history(session.id)).items)
            assert app.drained
        finally:
            assert await root.aclose()
asyncio.run(smoke())
print(json.dumps(dict(wheel_version=md.version('tether'),
    agent_runtime=md.version('pydantic-ai-slim'), provider_sdks=len(sdks),
    extra=extra or 'base', offline_agent_and_replay=True, offline_gated_agent=True)))
"""


def inspect_artifacts(directory: Path) -> Path:
    wheels = sorted(directory.glob("tether-*.whl"))
    sources = sorted(directory.glob("tether-*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("expected one fresh tether wheel and one source archive")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        if "tether/py.typed" not in names or not LICENSES.issubset({Path(x).name for x in names}):
            raise ValueError("wheel missing typing marker or license files")
        metadata = wheel.read(next(x for x in names if x.endswith(".dist-info/METADATA"))).decode()
        if "License-Expression: MIT OR Apache-2.0" not in metadata:
            raise ValueError("wheel missing dual-license expression")
    with tarfile.open(sources[0]) as source:
        names = source.getnames()
        if not any(x.endswith("/src/tether/py.typed") for x in names) or not LICENSES.issubset(
            {Path(x).name for x in names}
        ):
            raise ValueError("source archive missing typing marker or license files")
    return wheels[0].resolve()


async def run(command: list[str]) -> str:
    result = await run_command(command)
    if result.returncode:
        raise ValueError(result.stderr)
    return result.stdout


async def clean_import(wheel: Path, root: Path, extra: str | None = None) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv is required for the installed-base check")
    environment = root / "base"
    await run([uv, "venv", "--python", sys.executable, str(environment)])
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    requirements = root / "base.txt"
    export = [
        uv,
        "export",
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--format",
        "requirements-txt",
    ]
    if extra:
        export.extend(["--extra", extra])
    exported = await run(export)
    wheel_data = await asyncio.to_thread(wheel.read_bytes)
    wheel_hash = hashlib.sha256(wheel_data).hexdigest()
    await asyncio.to_thread(
        requirements.write_text,
        exported + f"\ntether @ {wheel.as_uri()} --hash=sha256:{wheel_hash}\n",
        encoding="utf-8",
    )
    await run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--no-deps",
            "-r",
            str(requirements),
        ]
    )
    print(
        (
            await run(
                [
                    str(python),
                    "-I",
                    "-c",
                    SMOKE,
                    json.dumps(sorted(PROVIDER_DISTRIBUTIONS)),
                    extra or "",
                ]
            )
        ).strip()
    )


async def main() -> int:
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("dist")
    extra = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        if extra not in (None, "openai"):
            raise ValueError("unsupported_extra")
        wheel = inspect_artifacts(directory)
        with tempfile.TemporaryDirectory(prefix="tether-base-") as name:
            await clean_import(wheel, Path(name), extra)
    except ValueError as error:
        print(f"Distribution check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
