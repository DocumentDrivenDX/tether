"""Optional PostgreSQL18 local process composition; no pool or migration is implicit."""

from .config import EmbeddedConfig, EmbeddedEndpoint
from .server import EmbeddedPostgres

__all__ = ["EmbeddedConfig", "EmbeddedEndpoint", "EmbeddedPostgres"]
