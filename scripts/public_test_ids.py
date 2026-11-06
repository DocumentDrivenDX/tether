"""Shared privacy guard for generated pytest node IDs."""

from __future__ import annotations

import re

UNSAFE_TEST_ID = re.compile(
    r"\bBearer[ \t]+\S+|[A-Za-z][A-Za-z0-9+.-]*://[^/\s?#]*@", re.IGNORECASE
)


def validate_public_test_id(value: str) -> None:
    if UNSAFE_TEST_ID.search(value):
        raise ValueError("test_id_contains_credential_shape")


__all__ = ["UNSAFE_TEST_ID", "validate_public_test_id"]
