"""Opt-in reference tools. Synchronous I/O is offloaded by the engine's worker owner."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from contextlib import suppress
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from tether.types.execution import Effect, Replay, Tool, ToolContext

from .web_tools import load_page, search_web

MAX_BYTES = 65536


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BashArguments(Arguments):
    command: str = Field(min_length=1, max_length=16384)
    timeout_seconds: int = Field(default=10, ge=1, le=20)


class ReadArguments(Arguments):
    path: str = Field(min_length=1, max_length=4096)


class WriteArguments(ReadArguments):
    content: str = Field(max_length=32768)


class SearchArguments(Arguments):
    query: str = Field(min_length=1, max_length=2048)


class PageArguments(Arguments):
    url: str = Field(min_length=1, max_length=8192)


class CommonTool:
    version = "1"
    requires: frozenset[str] = frozenset()
    model_visible = True
    always_ask = True
    effect: Effect = "effectful"
    replay: Replay = "unsafe"
    timeout_seconds: float | None = 25
    definition_hash: str | None = None
    name: str
    description: str
    input_model: type[BaseModel]

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        # The workspace participates in the pinned tool definition / approval binding.
        self.traits: dict[str, JsonValue] = {"workspace": str(workspace)}

    def asks_first(self, ctx: ToolContext, args: BaseModel) -> bool:
        return True

    def local_path(self, value: str) -> Path:
        path = (self.workspace / value).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("path_outside_workspace")
        return path


class BashTool(CommonTool):
    name = "bash"
    description = "Run bash in the workspace with user permissions; this is not a sandbox."
    input_model: type[BaseModel] = BashArguments

    async def run(self, ctx: ToolContext, args: BaseModel) -> str:
        request = BashArguments.model_validate(args)
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                request.command,
                cwd=self.workspace,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        )
        cancelled = False
        while not spawning.done():
            try:
                await asyncio.shield(spawning)
            except asyncio.CancelledError:
                cancelled = True
        process = spawning.result()
        if cancelled:
            await stop_process(process)
            raise asyncio.CancelledError
        if process.stdout is None:
            raise RuntimeError("subprocess_pipe_required")
        output = bytearray()
        status = "complete"
        try:
            async with asyncio.timeout(request.timeout_seconds):
                while chunk := await process.stdout.read(4096):
                    remaining = MAX_BYTES - len(output)
                    output.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        status = "output_limit"
                        break
                if status == "complete":
                    await process.wait()
        except TimeoutError:
            status = "timeout"
        finally:
            await stop_process(process)
        return json.dumps(
            {
                "status": status,
                "exit_code": process.returncode,
                "output": output.decode("utf-8", errors="replace"),
            }
        )


async def stop_process(process: asyncio.subprocess.Process) -> None:
    # Kill descendants even if the shell exited while they retain its output pipe.
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


class ReadFileTool(CommonTool):
    name = "read_file"
    description = "Read a UTF-8 file under the workspace (maximum 64 KiB)."
    input_model: type[BaseModel] = ReadArguments
    effect: Effect = "read_only"
    replay: Replay = "safe"

    def run(self, ctx: ToolContext, args: BaseModel) -> str:
        path = self.local_path(ReadArguments.model_validate(args).path)
        if not path.is_file():
            raise ValueError("regular_file_required")
        with path.open("rb") as stream:
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("file_bytes_exceeded")
        return content.decode("utf-8")


class WriteFileTool(CommonTool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 file under the workspace; parent must exist."
    input_model: type[BaseModel] = WriteArguments

    def run(self, ctx: ToolContext, args: BaseModel) -> str:
        request = WriteArguments.model_validate(args)
        data = request.content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError("file_bytes_exceeded")
        path = self.local_path(request.path)
        if path.exists() and not path.is_file():
            raise ValueError("regular_file_required")
        path.write_bytes(data)
        return json.dumps({"path": str(path), "bytes_written": len(data)})


class SearchTool(CommonTool):
    name = "web_search"
    description = "Search DuckDuckGo's HTML endpoint; returns titles, URLs and snippets."
    input_model: type[BaseModel] = SearchArguments
    effect: Effect = "read_only"
    replay: Replay = "safe"

    def run(self, ctx: ToolContext, args: BaseModel) -> str:
        return search_web(SearchArguments.model_validate(args).query)


class PageTool(CommonTool):
    name = "web_page_load"
    description = "Load HTTP(S) text/HTML (no JavaScript); returns bounded extracted text."
    input_model: type[BaseModel] = PageArguments
    effect: Effect = "read_only"
    replay: Replay = "safe"

    def run(self, ctx: ToolContext, args: BaseModel) -> str:
        return load_page(PageArguments.model_validate(args).url)


def common_tools(workspace: Path) -> tuple[Tool, ...]:
    """Workspace must already be an absolute, resolved directory."""
    return tuple(
        tool(workspace) for tool in (BashTool, ReadFileTool, WriteFileTool, SearchTool, PageTool)
    )
