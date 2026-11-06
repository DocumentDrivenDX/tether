"""Explicit service-principal caller authentication for qualification only."""

from __future__ import annotations

import asyncio

from databricks.sdk import WorkspaceClient
from tether_databricks import ServiceCallerIdentity


class DatabricksServiceCallerAuthenticator:
    """Verify a request token against an explicit Databricks service principal."""

    def __init__(
        self,
        *,
        workspace_host: str,
        allowed_principal_id: str,
        actor_id: str,
    ) -> None:
        if not workspace_host or not allowed_principal_id or not actor_id:
            raise ValueError("service_caller_configuration_required")
        self._workspace_host = workspace_host
        self._allowed_principal_id = allowed_principal_id
        self._actor_id = actor_id

    async def __call__(self, bearer_token: str, /) -> ServiceCallerIdentity | None:
        if not bearer_token or bearer_token != bearer_token.strip():
            return None
        try:
            user = await asyncio.to_thread(self._current_user, bearer_token)
        except Exception:
            return None
        principal_id = getattr(user, "id", None)
        active = getattr(user, "active", None)
        user_name = getattr(user, "user_name", None)
        if (
            principal_id != self._allowed_principal_id
            or active is not True
            or not isinstance(user_name, str)
            or not user_name
        ):
            return None
        return ServiceCallerIdentity(
            actor_id=self._actor_id,
            principal=self._allowed_principal_id,
            forwarded_email=user_name,
        )

    def _current_user(self, bearer_token: str):
        client = WorkspaceClient(
            host=self._workspace_host,
            token=bearer_token,
            auth_type="pat",
        )
        return client.current_user.me()


__all__ = ["DatabricksServiceCallerAuthenticator"]
