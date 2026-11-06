"""Fabricated consumer-owned ports; imports stay at the supported facade."""

from collections import Counter

from pydantic import BaseModel

from tether.client import (
    ContextInput,
    ContextPart,
    ContextPlan,
    Decision,
    Effect,
    GuardVerdict,
    MutationReceipts,
    Replay,
    SessionStore,
    SourceRange,
)


class RecordingReads:
    def __init__(self, delegate: SessionStore, calls: Counter[str] | None = None):
        self.delegate = delegate
        self.calls = calls if calls is not None else Counter()

    async def get(self, session_id, owner):
        self.calls["get"] += 1
        return await self.delegate.get(session_id, owner)

    async def list(self, query, *, cursor=None, limit=50):
        self.calls["list"] += 1
        return await self.delegate.list(query, cursor=cursor, limit=limit)

    async def find(self, owner, ref):
        self.calls["find"] += 1
        return await self.delegate.find(owner, ref)

    async def effects(self, session_id, owner, *, start=0, limit=50):
        self.calls["effects"] += 1
        return await self.delegate.effects(session_id, owner, start=start, limit=limit)

    async def watermark(self, owner):
        self.calls["watermark"] += 1
        return await self.delegate.watermark(owner)

    def changes(self, owner, *, session_id=None, since=None):
        self.calls["changes"] += 1
        return self.delegate.changes(owner, session_id=session_id, since=since)

    async def lookup(self, session_id, owner, *, kind, value, limit=1000, max_bytes=None):
        self.calls["lookup"] += 1
        return await self.delegate.lookup(
            session_id, owner, kind=kind, value=value, limit=limit, max_bytes=max_bytes
        )

    async def snapshot(self, session_id, owner):
        self.calls["snapshot"] += 1
        return await self.delegate.snapshot(session_id, owner)

    async def entries(self, session_id, owner, *, start, limit, reverse=False):
        self.calls["entries"] += 1
        return await self.delegate.entries(
            session_id, owner, start=start, limit=limit, reverse=reverse
        )


class RecordingStore(RecordingReads):
    @property
    def workers(self):
        return self.delegate.workers

    @property
    def capabilities(self):
        return self.delegate.capabilities

    def for_actor(self, actor):
        self.calls["for_actor"] += 1
        return RecordingStore(self.delegate.for_actor(actor), self.calls)

    async def create_with_receipt(self, owner, init):
        assert isinstance(self.delegate, MutationReceipts)
        self.calls["create"] += 1
        return await self.delegate.create_with_receipt(owner, init)

    async def update_with_receipt(self, session_id, owner, patch):
        assert isinstance(self.delegate, MutationReceipts)
        self.calls["update"] += 1
        return await self.delegate.update_with_receipt(session_id, owner, patch)

    async def aclose(self):
        self.calls["aclose"] += 1
        return await self.delegate.aclose()

    async def create(self, owner, init):
        self.calls["create"] += 1
        return await self.delegate.create(owner, init)

    async def update(self, session_id, owner, patch):
        self.calls["update"] += 1
        return await self.delegate.update(session_id, owner, patch)

    def turn(self, session_id, owner, *, turn_id):
        self.calls["turn"] += 1
        return self.delegate.turn(session_id, owner, turn_id=turn_id)

    async def restore(self, session, *, created_at):
        self.calls["restore"] += 1
        return await self.delegate.restore(session, created_at=created_at)

    async def recover(self, *, now):
        self.calls["recover"] += 1
        return await self.delegate.recover(now=now)


class RecordingHistory:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = Counter()
        self.id = delegate.id
        self.capabilities = delegate.capabilities

    async def put(self, *, actor, session_id, key, record):
        self.calls["put"] += 1
        return await self.delegate.put(actor=actor, session_id=session_id, key=key, record=record)

    async def read(self, refs, *, actor):
        self.calls["read"] += 1
        return await self.delegate.read(refs, actor=actor)

    async def bind_exchange(self, *, actor, binding):
        self.calls["bind_exchange"] += 1
        return await self.delegate.bind_exchange(actor=actor, binding=binding)


class RecordingPolicy:
    def __init__(self):
        self.requests = []
        self.revoked = False

    async def authorize(self, request):
        self.requests.append(request)
        return Decision(allowed=not self.revoked, reason="fabricated_policy")


class RecordingModel:
    provider = "scripted"

    def __init__(self, delegate):
        self.delegate = delegate
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        async for item in self.delegate.stream(request):
            yield item


class Arguments(BaseModel):
    label: str


class FabricatedTool:
    name = "record_fabricated"
    version = "1"
    description = "Record fabricated test data"
    input_model: type[BaseModel] = Arguments
    requires = frozenset()
    model_visible = True
    always_ask = True
    effect: Effect = "read_only"
    replay: Replay = "safe"
    timeout_seconds: float | None = None
    definition_hash: str | None = None

    def __init__(self):
        self.traits = {}
        self.runs = []

    def asks_first(self, ctx, args) -> bool:
        return True

    def run(self, ctx, args) -> str:
        self.runs.append(Arguments.model_validate(args).label)
        return "Fabricated tool result"


class RecordingProvider:
    id = "fabricated-provider"
    contract = 3

    def __init__(self):
        self.calls = Counter()

    def tools(self):
        return ()

    def capabilities(self, actor, session):
        self.calls["capabilities"] += 1
        return {}

    def prompt_section(self, session, spec):
        self.calls["prompt_section"] += 1
        return "Fabricated context"

    def hidden_tools(self, session, spec):
        self.calls["hidden_tools"] += 1
        return frozenset()

    def before_request(self, session, spec, messages):
        self.calls["before_request"] += 1
        return messages

    def before_call(self, session, spec, call):
        self.calls["before_call"] += 1

    def after_call(self, session, spec, call, message):
        self.calls["after_call"] += 1
        return message


class RecordingGuard:
    id = "fabricated-guard"

    def __init__(self):
        self.calls = []

    async def check(self, value, session):
        self.calls.append((value, session.id))
        return GuardVerdict(outcome="ok")


class RecordingAudit:
    def __init__(self):
        self.events = []

    async def emit(self, *, context, kind, detail):
        self.events.append((context, kind, detail))


class RecordingContext:
    id = "fabricated-context"
    version = "1"

    def __init__(self):
        self.calls = []

    async def plan(self, context: ContextInput) -> ContextPlan:
        self.calls.append(context)
        return ContextPlan(
            strategy_id=self.id,
            strategy_version=self.version,
            parts=tuple(
                ContextPart(kind="source", ranges=(SourceRange(start=item.index, end=item.index),))
                for item in context.sources
            ),
        )
