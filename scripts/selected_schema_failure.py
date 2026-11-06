"""Pure classification for the supported selected-schema refusal outcomes."""

from __future__ import annotations

from tether.client import IntegrityError


def missing_schema_refusal_code(error: BaseException) -> str | None:
    """Return a code only for the typed adapter mismatch or known PostgreSQL states."""
    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate in {"3F000", "42P01"}:
        return str(sqlstate)
    if isinstance(error, IntegrityError) and str(error) == "database_schema_mismatch":
        return "database_schema_mismatch"
    return None


__all__ = ["missing_schema_refusal_code"]
