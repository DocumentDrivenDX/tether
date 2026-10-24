"""Canonical checksummed batches and durable acknowledged-prefix witnesses."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from tether.types import JsonValue
from tether.types.canonical import canonical, digest
from tether.types.storage import DiskBatch, FileHead, IntegrityError, StoreError


def decode(line: bytes) -> DiskBatch:
    try:
        batch = DiskBatch.model_validate_json(line)
        value = batch.model_dump(mode="json")
        sha = value.pop("sha256")
        if sha != digest(value) or line != canonical(batch.model_dump(mode="json")) + b"\n":
            raise ValueError("batch_integrity")
        return batch
    except (ValueError, TypeError):
        raise IntegrityError("batch_integrity") from None


def encode(value: JsonValue, previous: str | None) -> bytes:
    payload: dict[str, JsonValue] = {"version": 1, "previous": previous, "payload": value}
    batch = DiskBatch.model_validate(payload | {"sha256": digest(payload)})
    return canonical(batch.model_dump(mode="json")) + b"\n"


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def make_directory(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        sync_directory(directory.parent)


def save_head(path: Path, head: FileHead) -> None:
    temporary = path.with_suffix(".tmp-" + uuid4().hex)
    with temporary.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(canonical(head.model_dump(mode="json")))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def load_head(path: Path) -> FileHead:
    if not path.exists():
        return FileHead(end=0, sha256=None)
    try:
        if path.stat().st_size > 1024:
            raise ValueError("head_size")
        return FileHead.model_validate_json(path.read_bytes())
    except (ValueError, OSError):
        raise IntegrityError("acknowledged_prefix_invalid") from None


def acknowledged_head(path: Path) -> FileHead:
    head_path = path.with_suffix(".head")
    if path.exists() and path.stat().st_size and not head_path.exists():
        raise IntegrityError("acknowledged_prefix_missing")
    return load_head(head_path)


def inspect_file(path: Path, maximum: int, batches: int) -> tuple[FileHead, int]:
    head = acknowledged_head(path)
    offset, previous, torn = 0, None, 0
    count = 0
    witnessed = head.end == 0 and head.sha256 is None
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+b") as source:
        source.seek(0)
        while line := source.readline(maximum + 1):
            count += 1
            if count > batches:
                raise StoreError("startup_batch_limit")
            if len(line) > maximum:
                raise IntegrityError("batch_too_large")
            if not line.endswith(b"\n"):
                if offset < head.end:
                    raise IntegrityError("acknowledged_prefix_truncated")
                torn = len(line)
                break
            batch = decode(line)
            if batch.previous != previous:
                raise IntegrityError("batch_chain_invalid")
            offset += len(line)
            previous = batch.sha256
            if offset == head.end:
                witnessed = head.sha256 == previous
    if not witnessed or offset < head.end:
        raise IntegrityError("acknowledged_prefix_invalid")
    if torn:
        quarantine(path, offset)
    return FileHead(end=offset, sha256=previous), torn


def quarantine(path: Path, offset: int) -> None:
    destination = path.with_suffix(".torn-" + uuid4().hex)
    with path.open("r+b") as source, destination.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        source.seek(offset)
        output.write(source.read())
        output.flush()
        os.fsync(output.fileno())
        source.truncate(offset)
        source.flush()
        os.fsync(source.fileno())
    sync_directory(path.parent)
