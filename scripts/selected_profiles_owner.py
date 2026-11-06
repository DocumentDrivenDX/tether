"""Live API and PostgreSQL row-policy checks for two selected owners."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from databricks.sdk import WorkspaceClient
from selected_profiles_identity_credentials import workspace_for_bearer
from selected_profiles_privacy_types import PrivacyTurn


class StaticCredential:
    def __init__(self, value: str) -> None:
        self._value = value

    async def authorization(self) -> str:
        return self._value


async def _remote(origin: str, prefix: str, token: str, *, service: bool) -> tuple[Any, Any]:
    import httpx

    from tether.client.databricks import service_caller_auth
    from tether.client.remote import RemoteClient, RemoteClientSettings

    transport = httpx.AsyncClient(
        base_url=origin, timeout=30, auth=service_caller_auth() if service else None
    )
    settings = RemoteClientSettings(
        base_url=origin,
        api_prefix=prefix,
        credential_provider=lambda: _credential(token),
        request_timeout_seconds=30,
    )
    try:
        client = await RemoteClient.open(settings, http_client=transport)
    except BaseException:
        await transport.aclose()
        raise
    return client, transport


async def _credential(token: str) -> StaticCredential:
    return StaticCredential(token)


async def _visible(
    source: Any,
    actor: Any,
    schema: str,
    session_id: str,
    *,
    require_nonadmin: bool = True,
) -> int:
    from psycopg import sql

    async with source(actor, session_id) as connection:
        role_cursor = await connection.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        role = await role_cursor.fetchone()
        if require_nonadmin and role != (False, False):
            raise RuntimeError("raw_rls_caller_not_nonadmin")
        cursor = await connection.execute(
            sql.SQL("SELECT count(*) FROM {}.sessions WHERE id=%s").format(sql.Identifier(schema)),
            (session_id.encode("utf-8"),),
        )
        row = await cursor.fetchone()
    if row is None or not isinstance(row[0], int):
        raise RuntimeError("raw_rls_query_invalid")
    return row[0]


async def _api_owner_checks(human: Any, service: Any) -> tuple[Any, Any]:
    from tether.client.remote import RemoteProblem

    human_session = await human.create(ref="profile-isolation-" + uuid.uuid4().hex)
    service_session = await service.create(ref="profile-isolation-" + uuid.uuid4().hex)
    try:
        human_page, service_page = await human.list(limit=100), await service.list(limit=100)
        if service_session.id in {item.id for item in human_page.items}:
            raise RuntimeError("cross_owner_session_listed")
        if human_session.id in {item.id for item in service_page.items}:
            raise RuntimeError("cross_owner_session_listed")
        attempts = ((human, service_session.id), (service, human_session.id))
        for client, session_id in attempts:
            await _deny_cross_owner_read(client, session_id, RemoteProblem)
            await _deny_cross_owner_close(client, session_id, RemoteProblem)
        if (await human.get(human_session.id)).summary.id != human_session.id:
            raise RuntimeError("owner_session_unavailable_after_denial")
        if (await service.get(service_session.id)).summary.id != service_session.id:
            raise RuntimeError("owner_session_unavailable_after_denial")
        return human_session, service_session
    except BaseException:
        await human.close(human_session.id)
        await service.close(service_session.id)
        raise


async def _deny_cross_owner_read(
    client: Any, session_id: str, problem_type: type[Exception]
) -> None:
    from tether.client import ExecutionDenied, UnknownSession

    try:
        await client.get(session_id)
    except Exception as error:
        if isinstance(error, (ExecutionDenied, UnknownSession)):
            return
        if not isinstance(error, problem_type) or getattr(error, "status", None) not in {403, 404}:
            raise RuntimeError("cross_owner_read_wrong_refusal") from None
    else:
        raise RuntimeError("cross_owner_session_readable")


async def _deny_cross_owner_close(
    client: Any, session_id: str, problem_type: type[Exception]
) -> None:
    from tether.client import ExecutionDenied, UnknownSession

    try:
        await client.close(session_id)
    except Exception as error:
        if isinstance(error, (ExecutionDenied, UnknownSession)):
            return
        if not isinstance(error, problem_type) or getattr(error, "status", None) not in {403, 404}:
            raise RuntimeError("cross_owner_mutation_wrong_refusal") from None
    else:
        raise RuntimeError("cross_owner_session_mutated")


async def _raw_owner_checks(
    sources: tuple[Any, Any], actors: tuple[Any, Any], schema: str, ids: tuple[str, str]
) -> None:
    human_source, service_source = sources
    human_actor, service_actor = actors
    human_id, service_id = ids
    expected = (
        (human_source, human_actor, human_id, 1, False),
        (service_source, service_actor, service_id, 1, True),
        (service_source, service_actor, human_id, 0, True),
    )
    for source, actor, session_id, count, require_nonadmin in expected:
        if (
            await _visible(
                source,
                actor,
                schema,
                session_id,
                require_nonadmin=require_nonadmin,
            )
            != count
        ):
            raise RuntimeError("raw_rls_owner_visibility_mismatch")
    await _raw_mutation_checks(
        service_source, service_actor, schema, service_id, human_id, human_actor.id
    )


async def _raw_mutation_checks(
    source: Any,
    actor: Any,
    schema: str,
    own_id: str,
    other_id: str,
    other_owner: str,
) -> None:
    from psycopg import sql

    class RollbackQualification(Exception):
        pass

    async with source(actor, own_id) as connection:
        try:
            async with connection.transaction():
                own_insert = await connection.execute(
                    sql.SQL(
                        "INSERT INTO {}.sessions "
                        "(owner,id,summary,projection,created_at,updated_at,status,title_fold,"
                        "tags,entry_count,generation) "
                        "VALUES (%s,%s,%s,%s,now(),now(),'active',%s,ARRAY[]::bytea[],0,1)"
                    ).format(sql.Identifier(schema)),
                    (actor.id.encode("utf-8"), uuid.uuid4().bytes, b"{}", b"{}", b""),
                )
                if own_insert.rowcount != 1:
                    raise RuntimeError("raw_rls_own_insert_failed")
                update = await connection.execute(
                    sql.SQL("UPDATE {}.sessions SET updated_at=updated_at WHERE id=%s").format(
                        sql.Identifier(schema)
                    ),
                    (other_id.encode("utf-8"),),
                )
                if update.rowcount != 0:
                    raise RuntimeError("raw_rls_cross_owner_update_succeeded")
                try:
                    async with connection.transaction():
                        await connection.execute(
                            sql.SQL(
                                "INSERT INTO {}.sessions "
                                "(owner,id,summary,projection,created_at,updated_at,status,title_fold,"
                                "tags,entry_count,generation) "
                                "VALUES (%s,%s,%s,%s,now(),now(),'active',%s,ARRAY[]::bytea[],0,1)"
                            ).format(sql.Identifier(schema)),
                            (
                                other_owner.encode("utf-8"),
                                uuid.uuid4().bytes,
                                b"{}",
                                b"{}",
                                b"",
                            ),
                        )
                except Exception as error:
                    if getattr(error, "sqlstate", None) != "42501":
                        raise RuntimeError("raw_rls_cross_owner_insert_unclassified") from None
                else:
                    raise RuntimeError("raw_rls_cross_owner_insert_succeeded")
                raise RollbackQualification
        except RollbackQualification:
            pass


async def owner_isolation(
    origin: str,
    prefix: str,
    settings: Any,
    service_database_role: str,
    human_workspace: WorkspaceClient,
    service_workspace: WorkspaceClient,
) -> None:
    """@covers US-068-AC2: API and raw RLS isolate two live owners."""
    from tether_databricks_example.postgres import LakebaseConnectionSource, LakebaseSettings

    from tether.client import Actor

    human_auth, service_auth, human_identity = await asyncio.gather(
        asyncio.to_thread(lambda: dict(human_workspace.config.authenticate()).get("Authorization")),
        asyncio.to_thread(
            lambda: dict(service_workspace.config.authenticate()).get("Authorization")
        ),
        asyncio.to_thread(human_workspace.current_user.me),
    )
    human_role = human_identity.user_name
    if (
        not isinstance(human_auth, str)
        or not isinstance(service_auth, str)
        or not isinstance(human_role, str)
        or not human_role
    ):
        raise RuntimeError("selected_profile_credential_missing")
    if not service_database_role or service_database_role == human_role:
        raise RuntimeError("selected_database_owners_not_distinct")
    config = LakebaseSettings(
        workspace_host=human_workspace.config.host,
        endpoint=settings.pg_endpoint,
        host=settings.pg_host,
        database=settings.pg_database,
        infra_role="fabricated-qualification-infra",
        sslrootcert=settings.ca_file,
    )

    def source(workspace: WorkspaceClient) -> LakebaseConnectionSource:
        return LakebaseConnectionSource(
            config,
            database_user=lambda actor: actor.id,
            infrastructure_client=workspace,
            user_client_factory=workspace_for_bearer,
        )

    human_actor = Actor(id=human_role, credential=StaticCredential(human_auth))
    service_actor = Actor(id=service_database_role, credential=StaticCredential(service_auth))
    human, human_http = await _remote(origin, prefix, human_auth, service=False)
    service, service_http = await _remote(origin, prefix, service_auth, service=True)
    sessions = None
    try:
        sessions = await _api_owner_checks(human, service)
        await _raw_owner_checks(
            (source(human_workspace), source(service_workspace)),
            (human_actor, service_actor),
            settings.pg_schema,
            (sessions[0].id, sessions[1].id),
        )
    finally:
        if sessions is not None:
            await human.close(sessions[0].id)
            await service.close(sessions[1].id)
        await human.aclose()
        await service.aclose()
        await human_http.aclose()
        await service_http.aclose()


async def successful_content_turn(
    origin: str, prefix: str, workspace: WorkspaceClient
) -> PrivacyTurn:
    """@covers US-068-AC10: complete fabricated user content and return its invocation ID."""
    from tether.client import Answer

    authorization = await asyncio.to_thread(
        lambda: dict(workspace.config.authenticate()).get("Authorization")
    )
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise RuntimeError("selected_profile_credential_missing")
    client, transport = await _remote(origin, prefix, authorization, service=False)
    session = None
    try:
        session = await client.create(ref="privacy-" + uuid.uuid4().hex)
        marker = " ".join(("fabricated", "content", uuid.uuid4().hex))
        accepted = await client.send(session.id, marker)
        first = await accepted.result()
        pending = await client.pending(session.id)
        if first.stop_reason != "confirmation_required" or pending is None:
            raise RuntimeError("privacy_success_path_not_pending")
        approved = await client.confirm(session.id, pending.invocation_id, Answer(approved=True))
        if approved is None:
            raise RuntimeError("privacy_success_path_not_accepted")
        final = await approved.result()
        if final.stop_reason != "final" or not any(
            item.role == "user" and item.content == marker for item in final.messages
        ):
            raise RuntimeError("privacy_success_path_not_completed")
        return PrivacyTurn(marker, pending.invocation_id, session.id)
    finally:
        try:
            if session is not None:
                await client.close(session.id)
        finally:
            await client.aclose()
            await transport.aclose()


__all__ = ["owner_isolation", "successful_content_turn"]
