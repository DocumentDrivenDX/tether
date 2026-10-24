"""Owner-authorized lease-serialized close with idempotent closed reads."""

from uuid import uuid4

from tether.engine import Engine
from tether.types.execution import Actor, copy_actor
from tether.types.storage import SessionClosed, SessionSummary, UnknownSession

from .access import SessionAccess


async def close_session(
    access: SessionAccess, engine: Engine, actor: Actor, sid: str
) -> SessionSummary:
    actor = copy_actor(actor)

    async def summary() -> SessionSummary:
        view = await access.store_for(actor).get(sid, actor.id)
        if view is None:
            raise UnknownSession("unknown_session")
        return view.summary

    async def operation() -> SessionSummary:
        current = await summary()
        if current.status == "closed":
            return current
        try:
            async with access.store_for(actor).turn(
                sid, actor.id, turn_id="close:" + str(uuid4())
            ) as log:
                await engine.close_session(actor, log)
        except SessionClosed:
            current = await summary()
            if current.status != "closed":
                raise
        return await summary()

    return await access.metadata(
        actor,
        sid,
        operation,
        session_id=sid,
    )
