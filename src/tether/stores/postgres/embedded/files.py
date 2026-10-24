"""Exclusive managed data ownership. Run filesystem operations only as retained offloads."""

import json
import os
import shutil
import sys
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from uuid import uuid4

from tether.types.storage import IntegrityError, RootBusy, StoreError, Unsupported

from .config import EmbeddedConfig


@dataclass
class Files:
    data: Path
    runtime: Path
    marker: Path
    binaries: Path
    lock_fd: int | None

    def initialized(self) -> bool:
        version = self.data / "PG_VERSION"
        if not version.exists():
            if any(self.data.iterdir()):
                raise StoreError("embedded_partial_initialization")
            return False
        if not self.marker.is_file():
            raise IntegrityError("embedded_unmanaged_directory")
        try:
            with self.marker.open("rb") as source:
                content = source.read(513)
            if len(content) > 512:
                raise ValueError("oversized_marker")
            marker = json.loads(content)
        except (ValueError, OSError):
            raise IntegrityError("embedded_marker_integrity") from None
        with version.open("rb") as source:
            native_version = source.read(16)
        if marker != {"version": 1, "profile": "postgres18"} or native_version != b"18\n":
            raise IntegrityError("embedded_marker_integrity")
        if (self.data / "postmaster.pid").exists():
            raise RootBusy("embedded_existing_server")
        return True

    def mark(self) -> None:
        with self.marker.open("xb") as output:
            output.write(b'{"profile":"postgres18","version":1}\n')
            output.flush()
            os.fsync(output.fileno())
        descriptor = os.open(self.marker.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def release(self) -> None:
        try:
            with suppress(FileNotFoundError):
                shutil.rmtree(self.runtime)
        finally:
            if self.lock_fd is not None:
                descriptor, self.lock_fd = self.lock_fd, None
                os.close(descriptor)


def binaries() -> Path:
    profile = (sys.platform, os.uname().machine) if sys.platform in {"darwin", "linux"} else None
    if (
        profile not in {("darwin", "arm64"), ("linux", "aarch64"), ("linux", "x86_64")}
        or os.geteuid() == 0
    ):
        raise Unsupported("embedded_platform_unsupported")
    if sys.version_info[:2] not in {(3, 11), (3, 12), (3, 13)}:
        raise Unsupported("embedded_python_unsupported")
    try:
        package = distribution("pgserver-pg18")
    except PackageNotFoundError:
        raise Unsupported("embedded_extra_required") from None
    if package.version != "18.6.1":
        raise Unsupported("embedded_binary_version_unsupported")
    path = Path(str(package.locate_file("pgserver/pginstall/bin")))
    if not all((path / name).is_file() for name in ("postgres", "initdb", "pg_isready")):
        raise Unsupported("embedded_binaries_missing")
    return path


def prepare(config: EmbeddedConfig) -> Files:
    if (
        not config.bootstrap_user
        or "\x00" in config.bootstrap_user
        or len(config.bootstrap_user.encode()) > 63
    ):
        raise ValueError("embedded_bootstrap_user_invalid")
    if not config.data_dir.is_absolute() or not config.runtime_dir.is_absolute():
        raise ValueError("embedded_paths_must_be_absolute")
    data, runtime = config.data_dir.resolve(), config.runtime_dir.resolve()
    binary_path = binaries()
    import fcntl

    if runtime.is_relative_to(data):
        raise ValueError("embedded_runtime_inside_data")
    if not data.parent.is_dir() or not runtime.is_dir():
        raise StoreError("embedded_parent_missing")
    descriptor = os.open(
        data.parent / ("." + data.name + ".tether-lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException as error:
        os.close(descriptor)
        if isinstance(error, BlockingIOError):
            raise RootBusy("embedded_directory_busy") from None
        raise
    owned = runtime / ("tether-" + uuid4().hex)
    try:
        if len(os.fsencode(owned)) > 85:
            raise StoreError("embedded_socket_path_too_long")
        data.mkdir(mode=0o700, exist_ok=True)
        owned.mkdir(mode=0o700)
        return Files(data, owned, data / ".tether-owner", binary_path, descriptor)
    except BaseException:
        os.close(descriptor)
        raise
