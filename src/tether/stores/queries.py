"""Metadata-only keyset queries; cursors carry position, never authority."""

from __future__ import annotations

import base64
from collections.abc import Iterable
from datetime import UTC

from tether.types import Payload
from tether.types.canonical import canonical, digest
from tether.types.storage import Page, SessionQuery, SessionSummary


class QueryCursor(Payload):
    version: int = 1
    query: str
    last: tuple[str, str]


def query_digest(query: SessionQuery, tier: str = "live") -> str:
    value = query.model_dump(mode="json")
    value["status"] = sorted(query.status) if query.status is not None else None
    return digest({"tier": tier, "query": value})


def position(summary: SessionSummary, query: SessionQuery) -> tuple[str, str]:
    date = summary.updated_at if query.order == "updated_desc" else summary.created_at
    return date.astimezone(UTC).isoformat(), summary.id


def matches(summary: SessionSummary, query: SessionQuery) -> bool:
    exact = summary.owner == query.owner and (query.ref is None or summary.ref == query.ref)
    selected = query.status is None or summary.status in query.status
    tagged = set(query.tags_all) <= set(summary.tags)
    dates = (query.updated_after is None or summary.updated_at > query.updated_after) and (
        query.updated_before is None or summary.updated_at < query.updated_before
    )
    text = query.text is None or query.text.casefold() in (summary.title or "").casefold()
    return exact and selected and tagged and dates and text


def decode_cursor(cursor: str, expected: str) -> tuple[str, str]:
    try:
        if len(cursor) > 4096:
            raise ValueError("cursor_size")
        value = QueryCursor.model_validate_json(
            base64.b64decode(cursor, altchars=b"-_", validate=True)
        )
        if value.version != 1 or value.query != expected:
            raise ValueError("cursor_scope")
        return value.last
    except (ValueError, TypeError):
        raise ValueError("invalid_cursor") from None


def encode_cursor(query: str, last: tuple[str, str]) -> str:
    return base64.urlsafe_b64encode(
        canonical(QueryCursor(query=query, last=last).model_dump(mode="json"))
    ).decode()


def page(
    values: Iterable[SessionSummary],
    query: SessionQuery,
    cursor: str | None,
    limit: int,
    *,
    tier: str = "live",
) -> Page[SessionSummary]:
    for date in (query.updated_after, query.updated_before):
        if date is not None and date.tzinfo is None:
            raise ValueError("invalid_query_datetime")
    fingerprint = query_digest(query, tier)
    after = decode_cursor(cursor, fingerprint) if cursor is not None else None
    descending = query.order != "created_asc"
    selected = sorted(
        (s for s in values if matches(s, query)),
        key=lambda s: position(s, query),
        reverse=descending,
    )
    if after is not None:
        selected = [
            s
            for s in selected
            if (position(s, query) < after if descending else position(s, query) > after)
        ]
    items = tuple(s.model_copy(deep=True) for s in selected[:limit])
    next_cursor = None
    if len(selected) > limit:
        next_cursor = encode_cursor(fingerprint, position(items[-1], query))
    return Page(items=items, next=next_cursor)
