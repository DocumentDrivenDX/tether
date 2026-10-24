"""Tracked foreground server processes with controlled output and an explicit environment."""

import asyncio
import signal
from contextlib import suppress

from tether.types.storage import StoreError

from .files import Files


class Processes:
    def __init__(self) -> None:
        self.initialization: asyncio.subprocess.Process | None = None
        self.server: asyncio.subprocess.Process | None = None
        self.probe: asyncio.subprocess.Process | None = None
        self.closing = False

    async def spawn(self, files: Files, name: str, *args: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            str(files.binaries / name),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": str(files.binaries), "LC_ALL": "C", "TZ": "UTC"},
            start_new_session=True,
        )

    async def initialize(self, files: Files, user: str) -> None:
        if self.closing:
            raise StoreError("embedded_closed")
        self.initialization = await self.spawn(
            files,
            "initdb",
            "-D",
            str(files.data),
            "--auth=trust",
            "--auth-local=trust",
            "--encoding=UTF8",
            "--locale=C",
            "-U",
            user,
        )
        if await self.initialization.wait() != 0:
            raise StoreError("embedded_initialization_failed")

    async def start(self, files: Files, poll_seconds: float, user: str) -> None:
        if self.closing:
            raise StoreError("embedded_closed")
        self.server = await self.spawn(
            files,
            "postgres",
            "-D",
            str(files.data),
            "-h",
            "",
            "-k",
            str(files.runtime),
            "-p",
            "5432",
            "-c",
            "log_min_messages=panic",
            "-c",
            "log_min_error_statement=panic",
        )
        while not self.closing and self.server.returncode is None:
            self.probe = await self.spawn(
                files,
                "pg_isready",
                "-h",
                str(files.runtime),
                "-p",
                "5432",
                "-U",
                user,
                "-t",
                "1",
            )
            if await self.probe.wait() == 0:
                return
            await asyncio.sleep(poll_seconds)
        raise StoreError("embedded_startup_failed")

    async def stop(self) -> None:
        self.closing = True
        for process in (self.initialization, self.probe):
            if process is not None:
                await process.wait()
        if self.server is not None:
            if self.server.returncode is None:
                with suppress(ProcessLookupError):
                    self.server.send_signal(signal.SIGINT)
            await self.server.wait()
