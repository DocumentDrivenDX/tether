"""Explicit PostgreSQL durability profiles qualified by the caller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PostgresDurabilityProfile:
    """A named server profile; managed profiles are bound to one endpoint ID."""

    name: Literal["native", "databricks-lakebase-pg18"] = "native"
    endpoint_id: str | None = None

    def __post_init__(self) -> None:
        if self.name not in {"native", "databricks-lakebase-pg18"}:
            raise ValueError("unknown_postgres_durability_profile")
        if self.name == "native" and self.endpoint_id is not None:
            raise ValueError("native_profile_has_no_endpoint")
        if self.name == "databricks-lakebase-pg18" and (
            not self.endpoint_id or not self.endpoint_id.isascii()
        ):
            raise ValueError("managed_profile_requires_endpoint")


NATIVE_POSTGRES_PROFILE = PostgresDurabilityProfile()


__all__ = ["NATIVE_POSTGRES_PROFILE", "PostgresDurabilityProfile"]
