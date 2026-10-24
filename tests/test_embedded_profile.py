"""Explicit embedded binary/profile and caller-configuration refusal boundaries."""

from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

from tether.stores.postgres.embedded import EmbeddedConfig, files
from tether.types.storage import Unsupported


@pytest.mark.parametrize(
    "profile", ["platform", "root", "python", "missing", "version", "binaries"]
)
def test_unsupported_binary_profiles_refuse_without_starting(profile, monkeypatch):
    monkeypatch.setattr(files.sys, "platform", "linux")
    monkeypatch.setattr(files.os, "uname", lambda: SimpleNamespace(machine="x86_64"))
    monkeypatch.setattr(files.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(files.sys, "version_info", (3, 11))
    if profile == "platform":
        monkeypatch.setattr(files.sys, "platform", "win32")
    elif profile == "root":
        monkeypatch.setattr(files.os, "geteuid", lambda: 0)
    elif profile == "python":
        monkeypatch.setattr(files.sys, "version_info", (3, 14))
    elif profile == "missing":

        def missing(name):
            raise PackageNotFoundError(name)

        monkeypatch.setattr(files, "distribution", missing)
    else:
        package = SimpleNamespace(
            version="other" if profile == "version" else "18.6.1",
            locate_file=lambda name: Path("/fabricated-missing-binaries"),
        )
        monkeypatch.setattr(files, "distribution", lambda name: package)
    with pytest.raises(Unsupported):
        files.binaries()


@pytest.mark.parametrize(
    "change",
    [
        {"bootstrap_user": ""},
        {"bootstrap_user": "x" * 64},
        {"bootstrap_user": "\x00"},
        {"data_dir": Path("relative")},
        {"runtime_dir": Path("relative")},
    ],
)
def test_invalid_explicit_configuration_refuses_before_native_work(change, tmp_path):
    config = EmbeddedConfig(
        bootstrap_user="fabricated",
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path,
        startup_seconds=1,
        shutdown_seconds=1,
        poll_seconds=0.01,
        max_roots=1,
    ).model_copy(update=change)
    with pytest.raises(ValueError, match="embedded_"):
        files.prepare(config)
    assert not (tmp_path / "data").exists()


def test_native_lock_error_closes_descriptor(tmp_path, monkeypatch):
    import fcntl
    import os

    config = EmbeddedConfig(
        bootstrap_user="fabricated",
        data_dir=tmp_path / "data",
        runtime_dir=tmp_path,
        startup_seconds=1,
        shutdown_seconds=1,
        poll_seconds=0.01,
        max_roots=1,
    )
    original = os.open
    descriptors = []

    def opened(*args, **kwargs):
        descriptor = original(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def failed(*args):
        raise OSError("fabricated_lock_failure")

    monkeypatch.setattr(files, "binaries", lambda: tmp_path)
    monkeypatch.setattr(files.os, "open", opened)
    monkeypatch.setattr(fcntl, "flock", failed)
    with pytest.raises(OSError, match="fabricated_lock_failure"):
        files.prepare(config)
    assert len(descriptors) == 1
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(descriptors[0])
