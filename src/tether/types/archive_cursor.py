"""Opaque source cursors wrapped in owner/query/tier-bound Client identities."""

import base64

from tether.types import Payload
from tether.types.canonical import canonical, digest
from tether.types.storage import SessionQuery


class ArchiveCursor(Payload):
    version: int = 1
    owner: str
    query: str
    after: str


def fingerprint(query: SessionQuery) -> str:
    value = query.model_dump(mode="json")
    value["status"] = sorted(query.status) if query.status is not None else None
    return digest({"tier": "archive", "query": value})


def unwrap(cursor: str | None, query: SessionQuery) -> str | None:
    if cursor is None:
        return None
    try:
        if not cursor.startswith("archive:"):
            raise ValueError("cursor_tier")
        value = ArchiveCursor.model_validate_json(
            base64.b64decode(cursor.removeprefix("archive:"), altchars=b"-_", validate=True)
        )
        if (value.version, value.owner, value.query) != (1, query.owner, fingerprint(query)):
            raise ValueError("cursor_scope")
        return value.after
    except (ValueError, TypeError):
        raise ValueError("invalid_cursor") from None


def wrap(cursor: str | None, query: SessionQuery) -> str | None:
    if cursor is None:
        return None
    value = ArchiveCursor(owner=query.owner, query=fingerprint(query), after=cursor)
    return "archive:" + base64.urlsafe_b64encode(canonical(value.model_dump(mode="json"))).decode()
