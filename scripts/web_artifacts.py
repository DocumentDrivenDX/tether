"""Inspect built archives and installed dependency closures without checkout imports."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

PROJECTS = {
    "novirhiza-tether": ".",
    "novirhiza-tether-web-api": "packages/web-api",
    "novirhiza-tether-web-ui": "packages/web-ui",
    "novirhiza-tether-databricks": "packages/databricks",
}
MATRIX = {
    "base": (
        ("novirhiza-tether",),
        (),
        (
            "textual",
            "fastapi",
            "httpx",
            "openai",
            "psycopg",
            "tiktoken",
            "databricks-sdk",
            "opentelemetry-sdk",
            "opentelemetry-exporter-otlp-proto-http",
            "novirhiza-tether-web-api",
            "novirhiza-tether-web-ui",
            "novirhiza-tether-databricks",
        ),
    ),
    "tui": (("novirhiza-tether",), ("tui",), ("fastapi", "openai", "psycopg")),
    "remote": (("novirhiza-tether",), ("remote",), ("fastapi", "textual", "openai", "psycopg")),
    "postgres": (("novirhiza-tether",), ("postgres",), ("fastapi", "textual", "openai")),
    "openai": (("novirhiza-tether",), ("openai",), ("fastapi", "textual", "psycopg")),
    "embedded-postgres": (
        ("novirhiza-tether",),
        ("embedded-postgres",),
        ("fastapi", "textual", "openai"),
    ),
    "api": (("novirhiza-tether", "novirhiza-tether-web-api"), (), ("textual", "openai", "psycopg")),
    "ui": (
        ("novirhiza-tether-web-ui",),
        (),
        ("novirhiza-tether", "fastapi", "textual", "pydantic"),
    ),
    "carrier": (
        tuple(PROJECTS),
        (),
        ("textual", "openai", "opentelemetry-sdk", "opentelemetry-exporter-otlp-proto-http"),
    ),
    "combined": (tuple(PROJECTS), ("tui", "remote", "postgres", "openai"), ()),
}
MODULES = {
    "novirhiza-tether": "tether.client",
    "novirhiza-tether-web-api": "tether_web_api",
    "novirhiza-tether-web-ui": "tether_web_ui",
    "novirhiza-tether-databricks": "tether_databricks",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_contents(path: Path) -> dict[str, str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return {
                name: digest(archive.read(name))
                for name in sorted(archive.namelist())
                if not name.endswith("/")
            }
    with tarfile.open(path, "r:gz") as archive:
        result = {}
        for member in archive.getmembers():
            if member.isfile():
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("archive_member_unreadable")
                result[member.name] = digest(stream.read())
        return result


def wheel_metadata(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("wheel_requires_one_metadata_file")
        message = BytesParser().parsebytes(archive.read(metadata[0]))
        return {
            "name": message["Name"],
            "version": message["Version"],
            "requires": message.get_all("Requires-Dist", []),
        }


def distribution_checks(root: Path, wheel: Path, sdist: Path, expected: str) -> dict[str, object]:
    """@covers US-069-AC2: actual wheel namespace/types and wheel/sdist license bytes."""
    namespace = MODULES[expected].split(".", 1)[0]
    contents = archive_contents(wheel)
    if namespace + "/py.typed" not in contents:
        raise ValueError("distribution_missing_public_typing_marker:" + expected)
    python_files = [name for name in contents if name.endswith(".py")]
    if not python_files or any(not name.startswith(namespace + "/") for name in python_files):
        raise ValueError("distribution_namespace_leak:" + expected)
    source = archive_contents(sdist)
    for name in ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE"):
        wanted = digest((root / name).read_bytes())
        installed = [
            value
            for path, value in contents.items()
            if ".dist-info/" in path and path.endswith("/" + name)
        ]
        shipped = [
            value
            for path, value in source.items()
            if len(Path(path).parts) == 2 and Path(path).name == name
        ]
        if installed != [wanted] or shipped != [wanted]:
            raise ValueError("distribution_license_missing_or_changed:" + expected + ":" + name)
    metadata = wheel_metadata(wheel)
    if metadata["name"] != expected:
        raise ValueError("unexpected_distribution_name")
    return {
        **metadata,
        "namespace": namespace,
        "public_typing_marker": True,
        "licenses": "root MIT/Apache/NOTICE bytes verified in wheel and sdist",
    }


def inventory(root: Path) -> dict[str, str]:
    paths = [
        root / "AGENTS.md",
        root / "pyproject.toml",
        root / "uv.lock",
        root / "package-lock.json",
        root / "Makefile",
        root / "README.md",
        root / "LICENSE-MIT",
        root / "LICENSE-APACHE",
        root / "NOTICE",
        root / "package.json",
        root / "tsconfig.json",
        root / "tsconfig.base.json",
        root / "eslint.config.js",
        root / "vitest.config.ts",
        root / "docs/embedding.md",
        root / "docs/web-api.md",
        root / "docs/react.md",
        root / "docs/remote-terminal.md",
        root / "docs/capture/capture_web.py",
    ]
    resolved_root = root.resolve()
    skipped = {
        "node_modules",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".bundle",
        "artifacts",
        ".pytest_cache",
        ".ruff_cache",
        ".git",
    }
    for folder in (
        "src",
        "apps",
        ".github",
        ".githooks",
        "scripts",
        "tests",
        "packages",
        "examples",
    ):
        for directory, children, names in os.walk(root / folder):
            children[:] = [name for name in children if name not in skipped]
            for name in names:
                path = Path(directory) / name
                if path.suffix != ".tsbuildinfo":
                    if path.is_symlink() and resolved_root not in path.resolve().parents:
                        raise ValueError("source_symlink_escapes_project")
                    paths.append(path)
    return {
        str(path.relative_to(root)): digest(path.read_bytes())
        for path in sorted(set(paths))
        if path.is_file()
    }


def installed_probe(
    projects: tuple[str, ...], extras: tuple[str, ...], absent: tuple[str, ...]
) -> str:
    # @covers US-069-AC7: inspect actual clean closure and refuse excluded dependencies.
    modules = [MODULES[name] for name in projects]
    if "tui" in extras:
        modules += ["tether.tui", "tether.cli"]
    if "remote" in extras:
        modules += ["tether.client.remote"]
    if "postgres" in extras or "embedded-postgres" in extras:
        modules += ["tether.client.postgres"]
    if "openai" in extras:
        modules += ["tether.client.compatible"]
    # Run under python -I in a fresh directory. No PYTHONPATH or editable checkout.
    return "\n".join(
        [
            "import importlib, importlib.metadata as md, json, pathlib, sys",
            f"modules = {modules!r}",
            f"absent = {absent!r}",
            "for name in absent:",
            "    try: md.distribution(name)",
            "    except md.PackageNotFoundError: pass",
            "    else: raise RuntimeError('unexpected_dependency:' + name)",
            "loaded = {}",
            "for name in modules:",
            "    module = importlib.import_module(name)",
            "    path = pathlib.Path(module.__file__).resolve()",
            "    if pathlib.Path(sys.prefix).resolve() not in path.parents:",
            "        raise RuntimeError('source_path_escape:' + str(path))",
            "    loaded[name] = str(path)",
            "dependencies = sorted((d.metadata['Name'], d.version) for d in md.distributions())",
            "metadata = [{'name': d.metadata['Name'], 'version': d.version,",
            "    'requires': d.requires or [],",
            "    'license_expression': d.metadata.get('License-Expression', ''),",
            "    'license': d.metadata.get('License', '')} for d in md.distributions()]",
            "print(json.dumps({'modules': loaded, 'dependencies': dependencies,",
            "    'metadata': metadata}))",
        ]
    )


def write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
