"""Bounded public-port proof of managed PostgreSQL process fencing and recovery."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from qualify_web_platform import Profile, profile_from_environment
from selected_profiles_managed_observation import (
    ObservationWatchdogExpired,
    acquire_or_close_on_cancellation,
    run_with_observation_watchdog,
)
from selected_profiles_managed_observation import (
    database_observation as _database_observation,
)
from selected_profiles_managed_validators import (
    fencing_projection_valid as _fencing_projection_valid,
)
from selected_profiles_managed_validators import (
    lease_refusal as _lease_refusal,
)
from selected_profiles_managed_validators import (
    recovered_exactly as _recovered_exactly,
)
from selected_profiles_managed_validators import (
    unknown_intent_valid as _unknown_intent_valid,
)
from selected_profiles_process import await_retained, wait_for_process_group_exit

from tether.client import Actor, SessionInit, SessionStore
from tether.client.postgres import (
    ConnectionSource,
    PostgresDurabilityProfile,
    PostgresResources,
    StorageLimits,
)

ROOT = Path(__file__).resolve().parents[1]
PEER = Path(__file__).with_name("selected_profiles_managed_process_peer.py")
_MAX_INPUT_BYTES = 16 * 1024
_MAX_EVENT_BYTES = 4096
_STORAGE_IO_SECONDS = 20
_LEASE_SECONDS = 30


class ManagedPeerInconclusive(RuntimeError):
    """A temporal precondition was not observed; no result is inferred."""


class _Credential:
    def __init__(self, authorization: str) -> None:
        self._authorization = authorization

    async def authorization(self) -> str:
        return self._authorization


@dataclass(frozen=True, slots=True)
class _Context:
    workspace: WorkspaceClient
    profile: Profile
    owner: str
    infra_role: str
    port: int
    authorization: str = field(repr=False)
    source: ConnectionSource
    actor: Actor
    resources: PostgresResources
    store: SessionStore


@dataclass(slots=True)
class _Peer:
    process: asyncio.subprocess.Process
    _cleanup_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def command(self, command: str) -> dict[str, object]:
        if self.process.stdin is None:
            raise RuntimeError("managed_peer_stdin_unavailable")
        payload = json.dumps({"command": command}, separators=(",", ":")).encode() + b"\n"
        if len(payload) > 1024:
            raise RuntimeError("managed_peer_command_invalid")
        self.process.stdin.write(payload)
        await self.process.stdin.drain()
        return await _read_event(self.process)

    async def kill_and_reap(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._kill_group_and_drain())
        await await_retained(self._cleanup_task)

    async def _kill_group_and_drain(self) -> None:
        pgid = self.process.pid
        with suppress(ProcessLookupError):
            # Descendants may still own captured streams after leader exit.
            os.killpg(pgid, signal.SIGKILL)
        drain = asyncio.create_task(self.process.communicate())
        await await_retained(drain)
        group_wait = asyncio.create_task(asyncio.to_thread(wait_for_process_group_exit, pgid))
        await await_retained(group_wait)


async def _reap_peers(*peers: _Peer | None) -> None:
    results = await asyncio.gather(
        *(peer.kill_and_reap() for peer in peers if peer is not None),
        return_exceptions=True,
    )
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("managed_peer_cleanup_failed")


async def _close_resources(resources: PostgresResources) -> None:
    if not await resources.aclose() and not await resources.wait_closed():
        raise RuntimeError("managed_resources_drain_incomplete")


def _storage_limits() -> StorageLimits:
    return StorageLimits(
        lease_seconds=_LEASE_SECONDS, io_seconds=_STORAGE_IO_SECONDS, shutdown_seconds=5
    )


@asynccontextmanager
async def _managed_context(
    workspace: WorkspaceClient, values: dict[str, str]
) -> AsyncIterator[_Context]:
    from selected_profiles_static_workspace import managed_connection_source
    from tether_databricks_example.postgres import LakebaseSettings

    profile = profile_from_environment(values)
    owner = values.get("TETHER_SERVICE_CALLER_DATABASE_ROLE", "")
    infra_role = values.get("TETHER_PG_INFRA_ROLE", "")
    if not owner or not infra_role or owner == infra_role:
        raise RuntimeError("managed_process_caller_invalid")
    identity_task = asyncio.create_task(asyncio.to_thread(workspace.current_user.me))
    identity = await await_retained(identity_task)
    if getattr(identity, "user_name", None) != owner:
        raise RuntimeError("managed_process_caller_mismatch")
    authorization_task = asyncio.create_task(asyncio.to_thread(_workspace_authorization, workspace))
    authorization = await await_retained(authorization_task)
    source = managed_connection_source(
        LakebaseSettings(
            workspace_host=workspace.config.host,
            endpoint=profile.pg_endpoint,
            host=profile.pg_host,
            database=profile.pg_database,
            infra_role=infra_role,
            port=int(values.get("TETHER_PG_PORT", "5432")),
            sslrootcert=profile.ca_file,
        ),
        database_user=lambda caller: caller.id,
        infrastructure_client=workspace,
    )
    actor = Actor(id=owner, credential=_Credential(authorization))
    resources = await acquire_or_close_on_cancellation(
        PostgresResources.open(
            source,
            actor=actor,
            schema=profile.pg_schema,
            limits=_storage_limits(),
            durability_profile=PostgresDurabilityProfile(
                "databricks-lakebase-pg18", endpoint_id=profile.pg_endpoint
            ),
        ),
        _close_resources,
    )
    try:
        context = _Context(
            workspace=workspace,
            profile=profile,
            owner=owner,
            infra_role=infra_role,
            port=int(values.get("TETHER_PG_PORT", "5432")),
            authorization=authorization,
            source=source,
            actor=actor,
            resources=resources,
            store=resources.storage.store.for_actor(actor),
        )
        yield context
    finally:
        close = asyncio.create_task(_close_resources(resources))
        await await_retained(close)


def _workspace_authorization(workspace: WorkspaceClient) -> str:
    headers = dict(workspace.config.authenticate())
    value = headers.get("Authorization") or headers.get("authorization")
    if not isinstance(value, str) or not value.startswith("Bearer "):
        raise RuntimeError("managed_process_credential_unavailable")
    return value


def _peer_environment() -> dict[str, str]:
    example_source = ROOT / "examples" / "databricks-app" / "src"
    package_source = ROOT / "src"
    return {
        "PATH": os.defpath,
        "PYTHONPATH": os.pathsep.join((str(example_source), str(package_source))),
        "PYTHONUTF8": "1",
    }


def _peer_config(
    context: _Context, *, mode: str, session_id: str, turn_id: str
) -> dict[str, object]:
    values = {
        "mode": mode,
        "owner": context.owner,
        "session_id": session_id,
        "turn_id": turn_id,
        "workspace_host": context.workspace.config.host,
        "workspace_authorization": context.authorization,
        "endpoint": context.profile.pg_endpoint,
        "host": context.profile.pg_host,
        "database": context.profile.pg_database,
        "schema": context.profile.pg_schema,
        "infra_role": context.infra_role,
        "ca_file": context.profile.ca_file,
        "port": context.port,
        "lease_seconds": _LEASE_SECONDS,
    }
    return values


async def _launch(context: _Context, *, mode: str, session_id: str, turn_id: str) -> _Peer:
    payload = (
        json.dumps(
            _peer_config(context, mode=mode, session_id=session_id, turn_id=turn_id),
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    if len(payload) > _MAX_INPUT_BYTES:
        raise RuntimeError("managed_peer_config_too_large")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(PEER),
            cwd=ROOT,
            env=_peer_environment(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            limit=_MAX_EVENT_BYTES,
        )
    )
    cancelled = False
    while not spawn.done():
        try:
            await asyncio.shield(spawn)
        except asyncio.CancelledError:
            cancelled = True
    process = spawn.result()
    peer = _Peer(process)
    if cancelled:
        await peer.kill_and_reap()
        raise asyncio.CancelledError
    try:
        if process.stdin is None:
            raise RuntimeError("managed_peer_stdin_unavailable")
        process.stdin.write(payload)
        await process.stdin.drain()
        event = await _read_event(process)
    except BaseException:
        await peer.kill_and_reap()
        raise
    if event.get("event") != "ready":
        await peer.kill_and_reap()
        raise RuntimeError("managed_peer_start_refused")
    return peer


async def _read_event(process: asyncio.subprocess.Process) -> dict[str, object]:
    if process.stdout is None:
        raise RuntimeError("managed_peer_stdout_unavailable")
    raw = await process.stdout.readline()
    if not raw or len(raw) > _MAX_EVENT_BYTES or not raw.endswith(b"\n"):
        raise RuntimeError("managed_peer_event_invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("managed_peer_event_invalid") from None
    if not isinstance(value, dict) or not isinstance(value.get("event"), str):
        raise RuntimeError("managed_peer_event_invalid")
    if set(value) - {"event", "command", "reason"}:
        raise RuntimeError("managed_peer_event_invalid")
    if any(not isinstance(item, str) or len(item) > 64 for item in value.values()):
        raise RuntimeError("managed_peer_event_invalid")
    return cast(dict[str, object], value)


async def _session(context: _Context, label: str) -> str:
    value = await context.store.create(
        context.owner, SessionInit(ref="managed-process-" + label + "-" + uuid4().hex)
    )
    return value.id


async def _verify_stale_owner(original: _Peer, contender: _Peer) -> None:
    stale_append = await original.command("append_a")
    if not _lease_refusal(stale_append, "append_a"):
        raise RuntimeError("managed_peer_stale_write_not_fenced")
    if (await contender.command("heartbeat")).get("event") != "ok":
        raise RuntimeError("managed_peer_successor_heartbeat_failed")
    stale_heartbeat = await original.command("heartbeat")
    if not _lease_refusal(stale_heartbeat, "heartbeat"):
        raise RuntimeError("managed_peer_stale_write_not_fenced")
    if (await contender.command("heartbeat")).get("event") != "ok":
        raise RuntimeError("managed_peer_successor_heartbeat_failed")
    stale_release = await original.command("close_log")
    if stale_release.get("event") not in {"closed", "close_refused"}:
        raise RuntimeError("managed_peer_stale_release_failed")


async def natural_expiry_fencing(context: _Context) -> dict[str, object]:
    """Prove natural lease expiry fences a stale writer across real processes."""
    session_id = await _session(context, "fencing")
    original_turn = "phase-a-" + uuid4().hex
    replacement_turn = "phase-b-" + uuid4().hex
    original: _Peer | None = None
    contender: _Peer | None = None
    try:
        original = await _launch(context, mode="hold", session_id=session_id, turn_id=original_turn)
        contender = await _launch(
            context, mode="contender", session_id=session_id, turn_id=replacement_turn
        )
        if (await original.command("activate")).get("event") != "turn_held":
            raise RuntimeError("managed_peer_hold_activation_failed")
        if (await original.command("heartbeat")).get("event") != "ok":
            raise RuntimeError("managed_peer_initial_heartbeat_failed")
        blocked = await contender.command("probe")
        if blocked.get("event") != "in_progress":
            if blocked.get("event") == "unexpected_acquire":
                raise ManagedPeerInconclusive("managed_peer_initial_state_unobserved")
            raise RuntimeError("managed_peer_initial_state_invalid")
        taken = await contender.command("takeover")
        if taken.get("event") != "taken_over":
            raise RuntimeError("managed_peer_natural_takeover_failed")
        await _verify_stale_owner(original, contender)
        current_append = await contender.command("append_b")
        current_heartbeat = await contender.command("heartbeat")
        if current_append.get("event") != "ok" or current_heartbeat.get("event") != "ok":
            raise RuntimeError("managed_peer_current_owner_lost")
        await _verify_fencing_projection(context, session_id, original_turn)
        if (await contender.command("finish_b")).get("event") != "finished":
            raise RuntimeError("managed_peer_current_finish_failed")
        return {
            "initial_competitor_blocked": True,
            "stale_append_lease_lost": True,
            "stale_heartbeat_lease_lost": True,
            "stale_release_preserved_successor": True,
            "public_projection_verified": True,
        }
    finally:
        cleanup = asyncio.create_task(_reap_peers(original, contender))
        await await_retained(cleanup)


async def _verify_fencing_projection(
    context: _Context, session_id: str, original_turn: str
) -> None:
    page = await context.store.entries(session_id, context.owner, start=0, limit=100)
    if not _fencing_projection_valid(page.items, original_turn):
        raise RuntimeError("managed_peer_public_projection_invalid")


async def killed_worker_recovery(context: _Context) -> dict[str, object]:
    """Prove a killed process leaves a recoverable unknown-effect barrier."""
    session_id = await _session(context, "killed")
    turn_id = "killed-" + uuid4().hex
    peer = await _launch(context, mode="unknown", session_id=session_id, turn_id=turn_id)
    try:
        if (await peer.command("activate")).get("event") != "intent_committed":
            raise RuntimeError("managed_peer_unknown_activation_failed")
        if peer.process.returncode is not None:
            raise RuntimeError("managed_peer_exited_before_kill")
        await peer.kill_and_reap()
        if peer.process.returncode != -signal.SIGKILL:
            raise RuntimeError("managed_peer_sigkill_not_observed")
        await _verify_killed_recovery(context, session_id, turn_id)
        return {
            "child_killed": True,
            "owner_recovered_interrupted": True,
            "one_unknown_effect": True,
            "no_inferred_result": True,
            "repeat_recovery_empty": True,
        }
    finally:
        if peer.process.returncode is None:
            await peer.kill_and_reap()


async def _verify_killed_recovery(context: _Context, session_id: str, turn_id: str) -> None:
    recovered = await _recover_until_visible(context)
    if not _recovered_exactly(recovered, session_id, turn_id):
        raise RuntimeError("managed_peer_recovery_missing")
    view = await context.store.get(session_id, context.owner)
    if (
        view is None
        or view.summary.status != "interrupted"
        or view.summary.unresolved_effect_count != 1
    ):
        raise RuntimeError("managed_peer_recovery_projection_invalid")
    effects = (await context.store.effects(session_id, context.owner, limit=10)).items
    if (
        len(effects) != 1
        or effects[0].reason != "outcome_unknown"
        or effects[0].invocation_id != "qualification-unknown"
    ):
        raise RuntimeError("managed_peer_unknown_effect_missing")
    page = await context.store.entries(session_id, context.owner, start=0, limit=100)
    if not _unknown_intent_valid(page.items, turn_id):
        raise RuntimeError("managed_peer_inferred_effect_result")
    if await context.resources.recover_owner(context.actor):
        raise RuntimeError("managed_peer_recovery_not_idempotent")


async def _recovery_poll_pause() -> None:
    await asyncio.sleep(0.25)


async def _recover_until_visible(context: _Context):
    while True:
        recovered = await context.resources.recover_owner(context.actor)
        if recovered:
            return recovered
        await _recovery_poll_pause()


async def qualify_managed_processes(
    workspace: WorkspaceClient,
    values: dict[str, str],
    *,
    observation_timeout_seconds: float | None = None,
) -> dict[str, object]:
    """Run both process controls to state completion, optionally bounded by the caller."""

    async def operation() -> dict[str, object]:
        async with _managed_context(workspace, values) as context:
            database = await _database_observation(context)
            fencing = await natural_expiry_fencing(context)
            recovery = await killed_worker_recovery(context)
        return {
            "database": database,
            "natural_expiry_fencing": fencing,
            "killed_worker_recovery": recovery,
        }

    try:
        return await run_with_observation_watchdog(observation_timeout_seconds, operation)
    except ObservationWatchdogExpired:
        raise ManagedPeerInconclusive("managed_peer_observation_watchdog") from None


__all__ = ["killed_worker_recovery", "natural_expiry_fencing", "qualify_managed_processes"]
