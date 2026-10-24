"""Bounded owner-current resolution command, serialized with every mutation."""

from uuid import uuid4

from tether.engine import Engine
from tether.engine.resolution import prior_resolution
from tether.types.canonical import canonical
from tether.types.client import ReadLimit
from tether.types.execution import Actor, NotUnresolved, Resolution, copy_actor
from tether.types.storage import SessionSummary, UnknownSession

from .access import SessionAccess


async def resolve(
    access: SessionAccess, engine: Engine, actor: Actor, sid: str, value: Resolution
) -> SessionSummary:
    actor, value = copy_actor(actor), value.model_copy(deep=True)
    access.check()
    if len(canonical(value.model_dump(mode="json"))) > access.limits.max_request_bytes:
        raise ReadLimit("request_bytes")

    async def operation() -> SessionSummary:
        view = await access.store_for(actor).get(sid, actor.id)
        if view is None:
            raise UnknownSession("unknown_session")
        if view.summary.status == "closed":
            await access.authorize(actor, value.invocation_id, "resolve_effect", session_id=sid)
            entries = await access.store_for(actor).lookup(
                sid,
                actor.id,
                kind="invocation",
                value=value.invocation_id,
                limit=access.limits.max_lookup_entries,
                max_bytes=access.limits.max_read_bytes,
            )
            if not prior_resolution(entries, value):
                raise NotUnresolved("not_unresolved")
            return view.summary
        async with access.store_for(actor).turn(
            sid, actor.id, turn_id="resolve:" + str(uuid4())
        ) as log:
            await engine.resolve(actor, log, value)
        view = await access.store_for(actor).get(sid, actor.id)
        if view is None:
            raise UnknownSession("unknown_session")
        return view.summary

    return await access.metadata(
        actor,
        sid,
        operation,
        session_id=sid,
    )
