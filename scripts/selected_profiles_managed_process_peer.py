"""Private stdin-driven process peer for the selected managed PostgreSQL proof."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Mapping
from typing import Any

from tether.client import Actor, LeaseLost, NewEntry, TurnInProgress
from tether.client.postgres import PostgresDurabilityProfile, PostgresResources, StorageLimits

_MAX_INPUT_BYTES = 16 * 1024
_MAX_COMMAND_BYTES = 1024


class _Credential:
    def __init__(self, authorization: str) -> None:
        self._authorization = authorization

    async def authorization(self) -> str:
        return self._authorization


def _emit(event: str, **values: object) -> None:
    allowed: dict[str, object] = {"event": event}
    allowed.update(values)
    sys.stdout.write(json.dumps(allowed, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _config() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES or not raw.endswith(b"\n"):
        raise ValueError("peer_input_invalid")
    value = json.loads(raw)
    required = {
        "mode",
        "owner",
        "session_id",
        "turn_id",
        "workspace_host",
        "workspace_authorization",
        "endpoint",
        "host",
        "database",
        "schema",
        "infra_role",
        "ca_file",
        "port",
        "lease_seconds",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("peer_input_invalid")
    string_fields = required - {"port", "lease_seconds"}
    if any(not isinstance(value[key], str) or not value[key] for key in string_fields):
        raise ValueError("peer_input_invalid")
    if value["mode"] not in {"hold", "contender", "unknown"}:
        raise ValueError("peer_input_invalid")
    if type(value["port"]) is not int or type(value["lease_seconds"]) is not int:
        raise ValueError("peer_input_invalid")
    return value


def _connection_source(config: Mapping[str, Any]):
    from selected_profiles_identity_credentials import workspace_for_bearer
    from selected_profiles_static_workspace import (
        managed_connection_source,
    )
    from tether_databricks_example.postgres import LakebaseSettings

    _scheme, separator, bearer = config["workspace_authorization"].partition(" ")
    if not separator or _scheme.lower() != "bearer" or not bearer:
        raise ValueError("peer_input_invalid")
    infrastructure = workspace_for_bearer(config["workspace_host"], bearer)
    settings = LakebaseSettings(
        workspace_host=config["workspace_host"],
        endpoint=config["endpoint"],
        host=config["host"],
        database=config["database"],
        infra_role=config["infra_role"],
        port=config["port"],
        sslrootcert=config["ca_file"],
    )
    source = managed_connection_source(
        settings,
        database_user=lambda actor: actor.id,
        infrastructure_client=infrastructure,
    )
    return source


def _actor(config: Mapping[str, Any]) -> Actor:
    return Actor(
        id=config["owner"],
        credential=_Credential(config["workspace_authorization"]),
    )


def _started(turn_id: str) -> NewEntry:
    return NewEntry(
        kind="turn_started",
        turn_id=turn_id,
        facts={"spec_id": "qualification", "spec_version": "1", "spec_digest": "fabricated"},
    )


async def _close_resources(resources: PostgresResources) -> None:
    if not await resources.aclose() and not await resources.wait_closed():
        raise RuntimeError("peer_resources_drain_incomplete")


def _storage_limits(lease_seconds: int) -> StorageLimits:
    return StorageLimits(lease_seconds=lease_seconds, io_seconds=20, shutdown_seconds=5)


async def _open_resources(
    config: Mapping[str, Any],
) -> tuple[PostgresResources, Actor]:
    source = _connection_source(config)
    actor = _actor(config)
    resources = await PostgresResources.open(
        source,
        actor=actor,
        schema=config["schema"],
        limits=_storage_limits(config["lease_seconds"]),
        durability_profile=PostgresDurabilityProfile(
            "databricks-lakebase-pg18", endpoint_id=config["endpoint"]
        ),
    )
    return resources, actor


async def _read_command() -> str | None:
    raw = await asyncio.to_thread(sys.stdin.buffer.readline, _MAX_COMMAND_BYTES + 1)
    if not raw:
        return None
    if len(raw) > _MAX_COMMAND_BYTES or not raw.endswith(b"\n"):
        return "invalid"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return "invalid"
    if not isinstance(value, dict) or set(value) != {"command"}:
        return "invalid"
    command = value["command"]
    return command if isinstance(command, str) else "invalid"


async def _perform_operation(log: Any, command: str, turn_id: str) -> bool:
    try:
        if command == "append_a":
            await log.append([NewEntry(kind="message", key="stale-a")])
        elif command == "append_b":
            await log.append([NewEntry(kind="message", key="current-b")])
        elif command == "heartbeat":
            await log.heartbeat()
        else:
            _emit("refused", command="unknown", reason="protocol")
            return False
    except Exception as error:
        code = "lease_lost" if isinstance(error, LeaseLost) else "operation_refused"
        _emit("refused", command=command, reason=code)
    else:
        _emit("ok", command=command)
    return True


async def _execute_command(log: Any, command: str, turn_id: str) -> bool:
    if command == "stop":
        _emit("stopped")
        return False
    if command == "close_log":
        return False
    if command == "finish_b":
        await log.append(
            [NewEntry(kind="turn_ended", turn_id=turn_id, facts={"stop_reason": "final"})]
        )
        return False
    return await _perform_operation(log, command, turn_id)


async def _commands(log: Any, turn_id: str) -> str | None:
    while True:
        command = await _read_command()
        if command is None:
            return None
        if command == "invalid":
            _emit("refused", command="invalid", reason="protocol")
            return None
        if not await _execute_command(log, command, turn_id):
            return command if command in {"close_log", "finish_b"} else None


async def _hold(config: Mapping[str, Any], resources: PostgresResources, actor: Actor) -> None:
    released: str | None = None
    release_event: str | None = None
    store = resources.storage.store.for_actor(actor)
    try:
        async with store.turn(config["session_id"], actor.id, turn_id=config["turn_id"]) as log:
            await log.append([_started(config["turn_id"])])
            if config["mode"] == "unknown":
                await log.append(
                    [
                        NewEntry(
                            kind="tool_call",
                            key="intent:qualification-unknown",
                            turn_id=config["turn_id"],
                            facts={
                                "invocation_id": "qualification-unknown",
                                "tool": "qualification-tool",
                                "effect": "effectful",
                                "status": "started",
                            },
                        )
                    ]
                )
                _emit("intent_committed")
            else:
                _emit("turn_held")
            released = await _commands(log, config["turn_id"])
    except LeaseLost:
        if released != "close_log":
            raise
        release_event = "close_refused"
    else:
        if released == "close_log":
            release_event = "closed"
    if release_event == "closed":
        _emit("closed")
    elif release_event == "close_refused":
        _emit("close_refused", reason="lease_lost")


async def _probe_once(
    config: Mapping[str, Any], resources: PostgresResources, actor: Actor
) -> None:
    store = resources.storage.store.for_actor(actor)
    try:
        async with store.turn(config["session_id"], actor.id, turn_id=config["turn_id"]):
            _emit("unexpected_acquire")
    except TurnInProgress:
        _emit("in_progress")


async def _takeover(config: Mapping[str, Any], resources: PostgresResources, actor: Actor) -> None:
    finished = False
    store = resources.storage.store.for_actor(actor)
    while True:
        try:
            async with store.turn(config["session_id"], actor.id, turn_id=config["turn_id"]) as log:
                await log.append([_started(config["turn_id"])])
                _emit("taken_over")
                command_result = await _commands(log, config["turn_id"])
            finished = command_result == "finish_b"
            break
        except TurnInProgress:
            # This poll observes the database's natural lease transition;
            # the delay only avoids a hot loop and is not a pass threshold.
            await asyncio.sleep(0.25)
    if finished:
        _emit("finished")


async def _serve(config: dict[str, Any]) -> None:
    resources, actor = await _open_resources(config)
    try:
        # Readiness means SDK/database resources are prepared, not merely that
        # the command loop imported. No lease is active before this event.
        _emit("ready")
        if config["mode"] == "contender":
            await _serve_contender(config, resources, actor)
            return
        activation = await _read_command()
        if activation != "activate":
            raise ValueError("peer_activation_invalid")
        await _hold(config, resources, actor)
    finally:
        await _close_resources(resources)


async def _serve_contender(
    config: Mapping[str, Any], resources: PostgresResources, actor: Actor
) -> None:
    while True:
        command = await _read_command()
        if command is None:
            return
        if command == "invalid":
            _emit("refused", command="invalid", reason="protocol")
            return
        if command == "probe":
            await _probe_once(config, resources, actor)
        elif command == "takeover":
            await _takeover(config, resources, actor)
            return
        elif command == "stop":
            _emit("stopped")
            return
        else:
            _emit("refused", command="unknown", reason="protocol")
            return


if __name__ == "__main__":
    try:
        # Read the bounded credential/config frame before starting asyncio so
        # stdin cannot block the event loop during setup.
        asyncio.run(_serve(_config()))
    except BaseException:
        _emit("failed", reason="setup_or_cleanup")
        raise SystemExit(1) from None
