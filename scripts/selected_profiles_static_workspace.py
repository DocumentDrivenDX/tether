"""Finite-timeout static-token clients for qualification-only SDK calls."""

from __future__ import annotations

from collections.abc import Callable

from selected_profiles_identity_credentials import workspace_for_bearer
from tether_databricks_example.postgres import (
    LakebaseConnectionSource,
    LakebaseSettings,
    WorkspacePostgresClient,
)

from tether.client import Actor


def managed_connection_source(
    settings: LakebaseSettings,
    *,
    database_user: Callable[[Actor], str],
    infrastructure_client: WorkspacePostgresClient,
) -> LakebaseConnectionSource:
    return LakebaseConnectionSource(
        settings,
        database_user=database_user,
        infrastructure_client=infrastructure_client,
        user_client_factory=workspace_for_bearer,
    )
