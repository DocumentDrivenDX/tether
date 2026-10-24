"""Explicit local server configuration; runtime paths never come from environment settings."""

from pathlib import Path

from tether.types import Payload, PositiveInt, PositiveSeconds


class EmbeddedConfig(Payload):
    bootstrap_user: str
    data_dir: Path
    runtime_dir: Path
    startup_seconds: PositiveSeconds
    shutdown_seconds: PositiveSeconds
    poll_seconds: PositiveSeconds
    max_roots: PositiveInt


class EmbeddedEndpoint(Payload):
    host: str
    port: int = 5432
    dbname: str = "postgres"
    user: str
