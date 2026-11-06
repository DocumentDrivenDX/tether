"""Run live checks against one explicitly configured Databricks Apps profile."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

import httpx
from qualify_live_apps_config import safe_origin
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_web_platform_logs import restart_app as _restart_app
from qualify_web_platform_observation import (
    assert_active_turn as _assert_active_turn,
)
from qualify_web_platform_observation import (
    observe_operation,
    run_owned_thread,
)
from qualify_web_platform_observation import (
    start_event_feed as _start_event_feed,
)
from qualify_web_platform_observation import (
    stop_event_feed as _stop_event_feed,
)
from selected_profiles_process import await_retained

from tether.client.databricks import (
    DatabricksCliProfile,
    DatabricksCliSettings,
    service_caller_auth,
)

if TYPE_CHECKING:
    from tether.client.remote import RemoteClient, RemoteClientSettings


@dataclass(frozen=True)
class Profile:
    app: str
    url: str
    pg_host: str
    pg_database: str
    pg_schema: str
    pg_endpoint: str
    ca_file: str
    api_prefix: str
    hold_seconds: float
    scenario: str = "basic"


def profile_from_environment(values: Mapping[str, str]) -> Profile:
    names = {
        "app": "TETHER_QUALIFICATION_APP",
        "url": "TETHER_QUALIFICATION_APP_URL",
        "pg_host": "TETHER_PG_HOST",
        "pg_database": "TETHER_PG_DATABASE",
        "pg_schema": "TETHER_PG_SCHEMA",
        "pg_endpoint": "TETHER_PG_ENDPOINT",
        "ca_file": "TETHER_PG_SSLROOTCERT",
    }
    missing = [env for env in names.values() if not values.get(env)]
    if missing:
        raise ValueError("qualification_settings_missing")
    try:
        url = safe_origin(values[names["url"]])
    except (RuntimeError, ValueError) as error:
        raise ValueError("qualification_app_url_invalid") from error
    schema = values[names["pg_schema"]]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", schema):
        raise ValueError("qualification_schema_invalid")
    prefix = values.get("TETHER_API_PREFIX", "/api/v1")
    if not prefix.startswith("/") or prefix.endswith("/") or "?" in prefix:
        raise ValueError("qualification_api_prefix_invalid")
    try:
        hold_seconds = float(values.get("TETHER_QUALIFICATION_HOLD_SECONDS", "0"))
    except ValueError as error:
        raise ValueError("qualification_hold_invalid") from error
    if not 0 <= hold_seconds <= 240:
        raise ValueError("qualification_hold_invalid")
    scenario = values.get("TETHER_QUALIFICATION_SCENARIO", "basic")
    if scenario not in {"basic", "shared-session"}:
        raise ValueError("qualification_scenario_invalid")
    return Profile(
        app=values[names["app"]],
        url=url,
        pg_host=values[names["pg_host"]],
        pg_database=values[names["pg_database"]],
        pg_schema=schema,
        pg_endpoint=values[names["pg_endpoint"]],
        ca_file=values[names["ca_file"]],
        api_prefix=prefix,
        hold_seconds=hold_seconds,
        scenario=scenario,
    )


def _cli(profile: str, *arguments: str, timeout_seconds: float = 120) -> str:
    executable = shutil.which("databricks")
    if executable is None:
        raise RuntimeError("databricks_cli_unavailable")
    result = subprocess.run(  # noqa: S603
        [executable, *arguments, "--profile", profile, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return result.stdout


def _check_app(profile: str, settings: Profile) -> None:
    import json

    app = json.loads(_cli(profile, "apps", "get", settings.app))
    if app.get("app_status", {}).get("state") != "RUNNING":
        raise RuntimeError("qualification_app_not_running")
    deployments = json.loads(_cli(profile, "apps", "list-deployments", settings.app))
    values = (
        deployments.get("deployments", deployments)
        if isinstance(deployments, dict)
        else deployments
    )
    if not isinstance(values, list) or not values:
        raise RuntimeError("qualification_deployment_missing")
    if values[0].get("status", {}).get("state") != "SUCCEEDED":
        raise RuntimeError("qualification_deployment_not_succeeded")


async def _database_probe(
    profile_name: str,
    settings: Profile,
    watchdog_seconds: float | None,
) -> None:
    from databricks.sdk import WorkspaceClient
    from psycopg import AsyncConnection
    from psycopg.conninfo import make_conninfo

    workspace = WorkspaceClient(profile=profile_name)
    identity = await run_owned_thread(
        workspace.current_user.me,
        watchdog_seconds=watchdog_seconds,
        reason="qualification_workspace_identity_not_observed",
    )
    credential = await run_owned_thread(
        partial(workspace.postgres.generate_database_credential, endpoint=settings.pg_endpoint),
        watchdog_seconds=watchdog_seconds,
        reason="qualification_database_credential_not_observed",
    )
    token = credential.token
    if not isinstance(token, str) or not token:
        raise RuntimeError("database_credential_unavailable")
    user = identity.user_name
    if not isinstance(user, str) or "@" not in user or " " in user:
        raise RuntimeError("database_profile_user_unavailable")
    conninfo = make_conninfo(
        host=settings.pg_host,
        dbname=settings.pg_database,
        user=user,
        password=token,
        sslmode="verify-full",
        sslrootcert=settings.ca_file,
    )
    async with (
        await AsyncConnection.connect(conninfo) as connection,
        connection.cursor() as cursor,
    ):
        await cursor.execute(
            "SELECT current_setting('server_version_num'), "
            "current_setting('fsync'), current_setting('full_page_writes'), "
            "current_setting('synchronous_commit')"
        )
        row = await cursor.fetchone()
        if row is None or not (int(row[0]) // 10000 == 18 and row[1:] == ("off", "off", "on")):
            raise RuntimeError("managed_postgres_profile_mismatch")
        await cursor.execute(
            "SELECT count(*) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relkind='r' AND c.relrowsecurity "
            "AND c.relforcerowsecurity",
            (settings.pg_schema,),
        )
        result = await cursor.fetchone()
        if result is None or result[0] < 10:
            raise RuntimeError("owner_rls_catalog_incomplete")


async def _open_remote(
    settings: RemoteClientSettings,
) -> tuple[RemoteClient, httpx.AsyncClient]:
    from tether.client.remote import RemoteClient

    http = httpx.AsyncClient(
        base_url=settings.base_url.rstrip("/"),
        timeout=settings.request_timeout_seconds,
        auth=service_caller_auth(),
    )
    try:
        return await RemoteClient.open(settings, http_client=http), http
    except BaseException:
        await http.aclose()
        raise


async def _restart_with_accepted_turn(
    client: RemoteClient,
    transport: httpx.AsyncClient,
    profile_name: str,
    settings: Profile,
    session_id: str,
    feed_since: int,
    watchdog_seconds: float | None,
) -> tuple[RemoteClient, httpx.AsyncClient]:
    from tether.client.remote import RemoteClientSettings

    expected_title = "Temporary web qualification feed ready"
    updated = await client.update(session_id, title=expected_title)
    queue, event_task = await _start_event_feed(
        client,
        session_id,
        since=feed_since,
        expected_seq=updated.last_seq,
        expected_title=expected_title,
        watchdog_seconds=watchdog_seconds,
    )
    try:
        handle = await client.send(
            session_id,
            "Answer with the configured qualification sentence.",
            request_id="web-qualification-" + uuid.uuid4().hex,
        )
        turn_id = handle.turn_id
        await _assert_active_turn(
            client, session_id, turn_id, queue, event_task, handle, watchdog_seconds
        )
    finally:

        async def close_observers() -> None:
            results = await asyncio.gather(
                _stop_event_feed(event_task),
                client.aclose(),
                transport.aclose(),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

        await await_retained(asyncio.create_task(close_observers()))
    await run_owned_thread(
        partial(_restart_app, profile_name, settings.app, watchdog_seconds, _cli),
        watchdog_seconds=watchdog_seconds,
        reason="qualification_app_state_not_observed",
    )
    token = DatabricksCliProfile(DatabricksCliSettings(profile=profile_name))

    async def credential_provider() -> DatabricksCliProfile:
        return token

    new_settings = RemoteClientSettings(
        base_url=settings.url,
        api_prefix=settings.api_prefix,
        credential_provider=credential_provider,
        request_timeout_seconds=30,
    )
    reopened, reopened_transport = await _open_remote(new_settings)
    recovered = await reopened.turn_result(session_id, turn_id)
    # @covers US-068-AC8
    if recovered is None or recovered.stop_reason not in {"cancelled", "interrupted"}:

        async def close_reopened() -> None:
            await asyncio.gather(reopened.aclose(), reopened_transport.aclose())

        cleanup = asyncio.create_task(close_reopened())
        await await_retained(cleanup)
        raise RuntimeError("accepted_turn_restart_outcome_not_terminal")
    return reopened, reopened_transport


async def _remote_probe(
    profile_name: str,
    settings: Profile,
    *,
    restart_app: bool,
    watchdog_seconds: float | None = None,
) -> None:
    from tether.client.remote import RemoteClientSettings

    token = DatabricksCliProfile(DatabricksCliSettings(profile=profile_name))

    async def credential_provider() -> DatabricksCliProfile:
        return token

    settings_obj = RemoteClientSettings(
        base_url=settings.url,
        api_prefix=settings.api_prefix,
        credential_provider=credential_provider,
        request_timeout_seconds=30,
    )
    client, transport = await _open_remote(settings_obj)
    client_open = True
    session = None
    session_closed = False
    try:
        session = await client.create(
            ref="web-qualification-" + uuid.uuid4().hex,
            title="Temporary web qualification",
        )
        if restart_app:
            client, transport = await _restart_with_accepted_turn(
                client,
                transport,
                profile_name,
                settings,
                session.id,
                session.last_seq,
                watchdog_seconds,
            )
            client_open = True
        else:
            handle = await client.send(
                session.id,
                "Answer with the configured qualification sentence.",
                request_id="web-qualification-" + uuid.uuid4().hex,
            )
            result = await observe_operation(
                handle.result(),
                watchdog_seconds=watchdog_seconds,
                reason="qualification_turn_terminal_not_observed",
            )
            if result.stop_reason != "final" or len(result.messages) < 2:
                raise RuntimeError("remote_turn_did_not_complete")
        view = await client.get(session.id)
        entries = await client.entries(session.id, limit=20)
        if view.summary.id != session.id or not entries.items:
            raise RuntimeError("remote_session_readback_failed")
        await client.close(session.id)
        session_closed = True
    finally:

        async def cleanup_client() -> None:
            try:
                if client_open and session is not None and not session_closed:
                    await client.close(session.id)
            finally:
                operations = [transport.aclose()]
                if client_open:
                    operations.append(client.aclose())
                results = await asyncio.gather(*operations, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        raise result

        await await_retained(asyncio.create_task(cleanup_client()))


async def qualify(
    profile_name: str,
    settings: Profile,
    *,
    restart_app: bool,
    watchdog_seconds: float | None,
) -> None:
    import httpx

    await run_owned_thread(
        partial(_check_app, profile_name, settings),
        watchdog_seconds=watchdog_seconds,
        reason="qualification_app_state_not_observed",
    )
    print("app deployment: passed", flush=True)
    async with httpx.AsyncClient(http2=True, timeout=20) as http:
        response = await http.get(settings.url + settings.api_prefix + "/openapi.json")
        if response.status_code != 401 or response.http_version != "HTTP/2":
            raise RuntimeError("apps_ingress_http2_auth_gate_failed")
    print("HTTP/2 ingress and unauthenticated command gate: passed", flush=True)
    await _database_probe(profile_name, settings, watchdog_seconds)
    print("managed PostgreSQL 18 durability and owner RLS catalog: passed", flush=True)
    await _remote_probe(
        profile_name, settings, restart_app=restart_app, watchdog_seconds=watchdog_seconds
    )
    print("remote client create/turn/read/close: passed", flush=True)
    if restart_app:
        print("app restart and durable turn-result recovery: passed", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--restart",
        action="store_true",
        help=(
            "request restart recovery; currently returns incomplete because the public Apps "
            "log stream has no correlated attachment acknowledgment"
        ),
    )
    parser.add_argument(
        "--observation-watchdog-seconds",
        type=float,
        default=None,
        help="optional operational observation watchdog; exhaustion returns incomplete",
    )
    args = parser.parse_args()
    try:
        settings = profile_from_environment(os.environ)  # noqa: TID251
        asyncio.run(
            qualify(
                args.profile,
                settings,
                restart_app=args.restart,
                watchdog_seconds=args.observation_watchdog_seconds,
            )
        )
    except LiveQualificationIncomplete as error:
        print(f"Live profile check incomplete: {error.reason}", file=sys.stderr)
        return 2
    except Exception as error:
        # SDK and transport exceptions can contain hosts, user ids, or tokens.
        safe_codes = {
            "qualification_settings_missing",
            "qualification_app_url_invalid",
            "qualification_schema_invalid",
            "qualification_api_prefix_invalid",
            "databricks_cli_unavailable",
            "qualification_app_not_running",
            "qualification_deployment_missing",
            "qualification_deployment_not_succeeded",
            "apps_ingress_http2_auth_gate_failed",
            "database_credential_unavailable",
            "database_profile_user_unavailable",
            "managed_postgres_profile_mismatch",
            "owner_rls_catalog_incomplete",
            "remote_turn_did_not_complete",
            "remote_session_readback_failed",
            "bounded_shutdown_report_incomplete",
            "turn_result_not_retained_after_restart",
            "apps_backend_http2_not_observed",
            "qualification_hold_invalid",
            "qualification_scenario_invalid",
            "qualification_turn_completed_before_restart",
            "accepted_turn_restart_outcome_not_terminal",
        }
        detail = (
            str(error)
            if type(error) is RuntimeError and str(error) in safe_codes
            else type(error).__name__
        )
        print(f"Live profile check failed: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
