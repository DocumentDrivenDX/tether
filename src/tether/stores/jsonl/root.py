"""One OS-owned local writer shared by journal and history adapters (POSIX only)."""

from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, TypeVar, cast

from tether.types import JsonValue
from tether.types.execution import CommitUnknown
from tether.types.storage import (
    FileHead,
    FileRecord,
    IntegrityError,
    RootBusy,
    StorageLimits,
    StoreError,
)

from .files import decode, encode, inspect_file, load_head, make_directory, save_head

T = TypeVar("T")
FileName = Literal["journal", "history"]


class JsonlRoot:
    """Explicit root lifetime; close before another process/instance may open this root."""

    def __init__(self, path: Path, limits: StorageLimits) -> None:
        self._loop = asyncio.get_running_loop()
        self.path, self.limits = path, limits
        self.durability: Literal["os"] = "os"
        self.recovery_notices: list[dict[str, str | int]] = []
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tether-jsonl")
        self._active: set[asyncio.Future[object]] = set()
        self._heads: dict[FileName, FileHead] = {}
        self._claimed: set[FileName] = set()
        self._lock_fd: int | None = None
        self._closed, self._poisoned = False, False

    @classmethod
    async def open(cls, path: str | Path, *, limits: StorageLimits | None = None) -> JsonlRoot:
        root = cls(Path(path), limits or StorageLimits())
        try:
            await root._io(root._initialize)
        except BaseException:
            await root.aclose()
            raise
        return root

    def _initialize(self) -> None:
        make_directory(self.path)
        descriptor = os.open(self.path / ".writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise RootBusy("local_root_already_open") from None
        self._lock_fd = descriptor
        for name in ("journal", "history"):
            head, torn = inspect_file(
                self.path / (name + ".jsonl"),
                self.limits.max_batch_bytes,
                self.limits.max_startup_batches,
            )
            head_path = (self.path / (name + ".jsonl")).with_suffix(".head")
            if not head_path.exists() or load_head(head_path) != head:
                save_head(head_path, head)
            self._heads[name] = head
            if torn:
                self.recovery_notices.append({"file": name, "offset": head.end, "torn_bytes": torn})

    def _check_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("wrong_event_loop")

    async def _io(self, function: Callable[..., T], *args: object) -> T:
        self._check_loop()
        if self._closed or self._poisoned:
            raise StoreError("writer_unavailable")
        if len(self._active) >= self.limits.max_pending_io:
            raise StoreError("io_capacity")
        future = asyncio.get_running_loop().run_in_executor(self._executor, function, *args)
        tracked = cast(asyncio.Future[object], future)
        self._active.add(tracked)
        tracked.add_done_callback(self._finished)
        async with asyncio.timeout(self.limits.io_seconds):
            return await asyncio.shield(future)

    def _finished(self, future: asyncio.Future[object]) -> None:
        self._active.discard(future)
        if not future.cancelled() and isinstance(future.exception(), IntegrityError):
            self._poisoned = True
        if self._closed and not self._active:
            self._finalize_close()

    def _path(self, name: FileName) -> Path:
        if name not in {"journal", "history"}:
            raise ValueError("invalid_journal_file")
        return self.path / (name + ".jsonl")

    def claim(self, name: FileName) -> None:
        if name in self._claimed:
            raise RootBusy("adapter_already_open")
        self._claimed.add(name)

    async def append_file(
        self, name: FileName, value: JsonValue, *, guard: Callable[[], None] | None = None
    ) -> int:
        try:
            return await self._io(self._append, name, value, guard)
        except (asyncio.CancelledError, CommitUnknown):
            self._poisoned = True
            raise
        except TimeoutError:
            self._poisoned = True
            raise CommitUnknown("journal_commit_unknown") from None

    def _append(self, name: FileName, value: JsonValue, guard: Callable[[], None] | None) -> int:
        if self._poisoned:
            raise StoreError("writer_unavailable")
        head, path = self._heads[name], self._path(name)
        line = encode(value, head.sha256)
        if len(line) > self.limits.max_batch_bytes:
            raise StoreError("batch_too_large")
        if guard is not None:
            guard()
        try:
            with path.open("r+b") as output:
                output.seek(0, os.SEEK_END)
                if output.tell() != head.end:
                    raise IntegrityError("journal_changed_outside_writer")
                output.write(line)
                output.flush()
                os.fsync(output.fileno())
            updated = FileHead(end=head.end + len(line), sha256=decode(line).sha256)
            save_head(path.with_suffix(".head"), updated)
            self._heads[name] = updated
        except OSError:
            self._poisoned = True
            raise CommitUnknown("journal_commit_unknown") from None
        return head.end

    def _read(self, name: FileName, offset: int) -> tuple[bytes, JsonValue]:
        if offset < 0:
            raise IntegrityError("invalid_offset")
        with self._path(name).open("rb") as source:
            source.seek(offset)
            line = source.readline(self.limits.max_batch_bytes + 1)
        if not line or len(line) > self.limits.max_batch_bytes:
            raise IntegrityError("invalid_offset")
        return line, decode(line).payload

    async def read_file(self, name: FileName, offset: int) -> JsonValue:
        _, value = await self._io(self._read, name, offset)
        return value

    async def scan(self, name: FileName) -> AsyncIterator[FileRecord]:
        offset = 0
        while offset < self._heads[name].end:
            line, value = await self._io(self._read, name, offset)
            yield FileRecord(offset=offset, value=value)
            offset += len(line)

    async def aclose(self) -> bool:
        self._check_loop()
        self._closed = True
        if self._active:
            _, pending = await asyncio.wait(
                tuple(self._active), timeout=self.limits.shutdown_seconds
            )
            if pending:
                return False
        self._finalize_close()
        return True

    def _finalize_close(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        self._executor.shutdown(wait=False, cancel_futures=False)
