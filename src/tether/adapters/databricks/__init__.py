"""Explicit Databricks CLI-profile authentication and bounded model discovery.

The compatible model adapter still owns inference. This connector never runs a
model, reads a credential file itself, or returns credentials in its metadata.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
from contextlib import suppress
from urllib.parse import urlencode, urlsplit

from pydantic import JsonValue, TypeAdapter

MAX_BYTES = 1024 * 1024


class CliProfile:
    def __init__(self, name: str, *, expected_host: str | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("Invalid Databricks CLI profile name")
        self.name, self.expected_host = name, expected_host

    async def _command(self, *arguments: str) -> dict[str, JsonValue]:
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "databricks",
                *arguments,
                "--profile",
                self.name,
                "-o",
                "json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        )
        cancelled = False
        while not spawning.done():
            try:
                await asyncio.shield(spawning)
            except asyncio.CancelledError:
                cancelled = True
        try:
            process = spawning.result()
        except FileNotFoundError:
            raise RuntimeError(
                "Install the Databricks CLI and sign in to the selected profile"
            ) from None
        if cancelled:
            await _stop(process)
            raise asyncio.CancelledError
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Databricks CLI pipes unavailable")
        try:
            async with asyncio.timeout(25):
                output, errors = await asyncio.gather(
                    process.stdout.read(MAX_BYTES + 1), process.stderr.read(MAX_BYTES + 1)
                )
                if len(output) > MAX_BYTES or len(errors) > MAX_BYTES:
                    raise RuntimeError("Databricks CLI response exceeds its byte limit")
                await process.wait()
                if process.returncode:
                    raise RuntimeError(
                        "Databricks CLI request failed; check the selected login profile"
                    )
                try:
                    return TypeAdapter(dict[str, JsonValue]).validate_json(output)
                except ValueError:
                    raise RuntimeError("Invalid Databricks CLI JSON response") from None
        finally:
            await _stop(process)

    async def host(self) -> str:
        description = await self._command("auth", "describe")
        details = description.get("details")
        host = details.get("host") if isinstance(details, dict) else None
        if not isinstance(host, str):
            raise RuntimeError("Databricks CLI did not supply a workspace host")
        parsed = urlsplit(host)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Databricks CLI host must be an HTTPS workspace origin")
        host = host.rstrip("/")
        if self.expected_host is not None and host != self.expected_host:
            raise RuntimeError("Databricks CLI profile host changed; reconnect explicitly")
        return host

    async def authorization(self) -> str | None:
        await self.host()
        result = await self._command("auth", "token", "--timeout", "15s")
        token = result.get("access_token")
        if (
            not isinstance(token, str)
            or not token
            or not token.isascii()
            or any(ord(character) <= 32 or ord(character) == 127 for character in token)
        ):
            raise RuntimeError("Databricks CLI profile has no usable OAuth access token")
        return " ".join(("Bearer", token))

    async def models(self, schemas: tuple[str, ...] = ("system.ai",)) -> tuple[str, ...]:
        names: set[str] = set()
        for schema in schemas:
            names.update(await self._schema_models(schema))
        if not names:
            raise RuntimeError("No accessible chat model services were discovered")
        return tuple(sorted(names))

    async def _schema_models(self, schema: str) -> set[str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", schema):
            raise ValueError("Gateway schema must be catalog.schema")
        names: set[str] = set()
        token, seen = "", set[str]()
        for _ in range(8):
            query = urlencode(
                {
                    "parent": "schemas/" + schema,
                    "view": "BASIC",
                    "page_size": 200,
                    "page_token": token,
                }
            )
            await self.host()
            page = await self._command(
                "api", "get", "/api/2.1/unity-catalog/model-services?" + query
            )
            entries = page.get("model_services", [])
            if not isinstance(entries, list):
                raise ValueError("Invalid Databricks model-service response")
            names.update(model_names(entries))
            next_token = page.get("next_page_token")
            if not next_token:
                return names
            if not isinstance(next_token, str) or next_token in seen or len(next_token) > 8192:
                raise ValueError("Invalid Databricks model-service pagination")
            seen.add(next_token)
            token = next_token
        raise ValueError("Databricks discovery exceeds eight pages per schema")


def model_names(entries: list[JsonValue]) -> set[str]:
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Databricks model service")
        name, supported = entry.get("name"), entry.get("supported_api_types", [])
        if not isinstance(name, str) or not name.startswith("model-services/"):
            raise ValueError("Invalid Databricks model-service name")
        if not isinstance(supported, list):
            raise ValueError("Invalid Databricks model-service APIs")
        if "mlflow/v1/chat/completions" not in supported:
            continue
        value = name.removeprefix("model-services/")
        if len(value) > 767 or len(value.split(".")) != 3:
            raise ValueError("Invalid Databricks model-service name")
        names.add(value)
    return names


async def _stop(process: asyncio.subprocess.Process) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGSTOP)
        os.killpg(process.pid, signal.SIGKILL)
    cleanup = asyncio.create_task(process.communicate())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError
