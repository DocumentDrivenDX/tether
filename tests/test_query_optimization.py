"""The metadata query fast path preserves the original keyset ordering contract."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tether.stores.queries import QueryCursor, encode_cursor, page, query_digest
from tether.types.canonical import canonical
from tether.types.storage import SessionQuery, SessionSummary


def summary(
    identity: str,
    created: datetime,
    updated: datetime,
    *,
    owner: str = "owner",
    tags: tuple[str, ...] = (),
    title: str | None = None,
) -> SessionSummary:
    return SessionSummary(
        id=identity,
        owner=owner,
        title=title,
        tags=tags,
        created_at=created,
        updated_at=updated,
    )


def legacy_position(value: SessionSummary, query: SessionQuery) -> tuple[str, str]:
    selected = value.updated_at if query.order == "updated_desc" else value.created_at
    return selected.astimezone(UTC).isoformat(), value.id


def legacy_match(value: SessionSummary, query: SessionQuery) -> bool:
    exact = value.owner == query.owner and (query.ref is None or value.ref == query.ref)
    selected = query.status is None or value.status in query.status
    tagged = set(query.tags_all) <= set(value.tags)
    dates = (query.updated_after is None or value.updated_at > query.updated_after) and (
        query.updated_before is None or value.updated_at < query.updated_before
    )
    text = query.text is None or query.text.casefold() in (value.title or "").casefold()
    return exact and selected and tagged and dates and text


def legacy_cursor(fingerprint: str, last: tuple[str, str]) -> str:
    payload = QueryCursor(query=fingerprint, last=last).model_dump(mode="json")
    return base64.urlsafe_b64encode(canonical(payload)).decode()


def legacy_page(
    values: tuple[SessionSummary, ...],
    query: SessionQuery,
    cursor: str | None,
    limit: int,
) -> tuple[tuple[SessionSummary, ...], str | None]:
    fingerprint = query_digest(query)
    after = None
    if cursor is not None:
        raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
        after = QueryCursor.model_validate_json(raw).last
    descending = query.order != "created_asc"
    selected = sorted(
        (value for value in values if legacy_match(value, query)),
        key=lambda value: legacy_position(value, query),
        reverse=descending,
    )
    if after is not None:
        selected = [
            value
            for value in selected
            if (
                legacy_position(value, query) < after
                if descending
                else legacy_position(value, query) > after
            )
        ]
    items = tuple(value.model_copy(deep=True) for value in selected[:limit])
    following = (
        legacy_cursor(fingerprint, legacy_position(items[-1], query))
        if len(selected) > limit
        else None
    )
    return items, following


@pytest.fixture
def varied_summaries() -> tuple[SessionSummary, ...]:
    zone = ZoneInfo("America/New_York")
    folded_early = datetime(2025, 11, 2, 1, 30, tzinfo=zone, fold=0)
    folded_late = datetime(2025, 11, 2, 1, 30, tzinfo=zone, fold=1)
    same_instant = datetime(2025, 11, 2, 5, 30, tzinfo=UTC)
    return (
        summary(
            "a",
            folded_early,
            datetime(2025, 11, 2, 8, 0, tzinfo=UTC),
            tags=("keep", "shared"),
            title="Alpha",
        ),
        summary(
            "b",
            same_instant,
            datetime(2025, 11, 2, 8, 0, 0, 1, tzinfo=UTC),
            tags=("shared", "keep"),
            title="Beta",
        ),
        summary(
            "c",
            folded_late,
            datetime(2025, 11, 2, 4, 0, tzinfo=UTC),
            tags=("keep",),
            title="Gamma",
        ),
        summary(
            "d",
            datetime(2025, 11, 2, 6, 30, 0, 1, tzinfo=UTC),
            datetime(2025, 11, 2, 9, 0, tzinfo=UTC),
            tags=("shared",),
            title="Delta",
        ),
        summary(
            "e",
            datetime(2025, 11, 2, 6, 30, tzinfo=timezone(timedelta(hours=2))),
            datetime(2025, 11, 2, 7, 30, tzinfo=timezone(timedelta(hours=2))),
            tags=("keep", "shared", "extra"),
            title="Epsilon",
        ),
        summary(
            "minimum",
            datetime.min.replace(tzinfo=UTC),
            datetime.min.replace(tzinfo=UTC),
            tags=("keep", "shared"),
            title="Minimum",
        ),
        summary(
            "maximum",
            datetime.max.replace(tzinfo=UTC),
            datetime.max.replace(tzinfo=UTC),
            tags=("keep", "shared"),
            title="Maximum",
        ),
        summary(
            "foreign",
            same_instant,
            same_instant,
            owner="another-owner",
            tags=("keep", "shared"),
        ),
    )


@pytest.mark.parametrize("order", ["created_asc", "created_desc", "updated_desc"])
@pytest.mark.parametrize("tags", [(), ("keep",), ("keep", "shared")])
def test_native_datetime_path_matches_legacy_page_walk_and_cursor_bytes(
    varied_summaries, order, tags
):
    query = SessionQuery(owner="owner", order=order, tags_all=tags)
    cursor = None
    for _ in range(len(varied_summaries) + 1):
        expected_items, expected_cursor = legacy_page(varied_summaries, query, cursor, 2)
        actual = page(varied_summaries, query, cursor, 2)
        assert [item.id for item in actual.items] == [item.id for item in expected_items]
        assert actual.next == expected_cursor
        if actual.items:
            assert actual.next == (
                legacy_cursor(query_digest(query), legacy_position(actual.items[-1], query))
                if expected_cursor is not None
                else None
            )
            returned = actual.items[0]
            source = next(value for value in varied_summaries if value.id == returned.id)
            assert returned is not source
            assert returned.usage is not source.usage
            assert returned.usage == source.usage
        if expected_cursor is None:
            break
        cursor = expected_cursor
    else:
        pytest.fail("page_walk_did_not_terminate")


@pytest.mark.parametrize(
    "date_text",
    [
        "not-a-date",
        "2025-11-02T05:30:00Z",  # Valid ISO, but not the canonical emitted spelling.
        "2025-11-02T05:30:00",  # Naive ISO spelling keeps old string comparison.
        "2025-11-02T07:30:00+02:00",  # Equivalent instant with a non-UTC offset.
        "2025-11-02T05:30:00.000000+00:00",  # Extra zero microsecond spelling.
        "0001-01-01T00:00:00+14:00",  # UTC normalization overflows datetime.min.
    ],
)
def test_noncanonical_and_overflow_cursor_dates_keep_legacy_string_fallback(
    varied_summaries, date_text
):
    query = SessionQuery(owner="owner", order="updated_desc")
    cursor = encode_cursor(query_digest(query), (date_text, "cursor-id"))
    expected, expected_next = legacy_page(varied_summaries, query, cursor, 3)
    actual = page(varied_summaries, query, cursor, 3)
    assert [item.id for item in actual.items] == [item.id for item in expected]
    assert actual.next == expected_next


def test_empty_and_duplicate_tag_filters_match_legacy_results(varied_summaries):
    for tags in ((), ("keep",), ("keep", "keep", "shared")):
        query = SessionQuery(owner="owner", tags_all=tags)
        expected, expected_next = legacy_page(varied_summaries, query, None, 20)
        actual = page(varied_summaries, query, None, 20)
        assert [item.id for item in actual.items] == [item.id for item in expected]
        assert actual.next == expected_next
