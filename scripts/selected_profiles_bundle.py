"""Verify that the selected live app source matches its locked local bundle."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


async def deployed_bundle_matches_lock(
    workspace: Any,
    app: Any,
    app_name: str,
    bundle: Path,
    deployment_reference: Path,
) -> None:
    """@covers US-068-AC9: deployed workspace bytes match locked wheels and assets."""
    from databricks.sdk.service.workspace import ExportFormat

    reference = json.loads(
        await asyncio.to_thread(deployment_reference.read_text, encoding="utf-8")
    )
    source_path = reference.get("source_code_path")
    deployment_id = reference.get("deployment_id")
    active_deployment = getattr(app, "active_deployment", None)
    active_deployment_id = getattr(active_deployment, "deployment_id", None)
    active_source_path = getattr(active_deployment, "source_code_path", None)
    bundle_exists = await asyncio.to_thread(bundle.is_dir)
    if (
        not isinstance(source_path, str)
        or not source_path
        or not isinstance(deployment_id, str)
        or not deployment_id
        or active_deployment_id != deployment_id
        or active_source_path != source_path
        or not bundle_exists
    ):
        raise RuntimeError("deployment_source_binding_mismatch")
    deployment = await asyncio.to_thread(workspace.apps.get_deployment, app_name, deployment_id)
    deployment_status = getattr(getattr(deployment, "status", None), "state", None)
    deployment_source = getattr(deployment, "source_code_path", None)
    artifacts = getattr(deployment, "deployment_artifacts", None)
    snapshot_path = getattr(artifacts, "source_code_path", None)
    if (
        getattr(deployment, "deployment_id", None) != deployment_id
        or getattr(deployment_status, "value", deployment_status) != "SUCCEEDED"
        or deployment_source != source_path
        or not isinstance(snapshot_path, str)
        or not snapshot_path
    ):
        raise RuntimeError("deployed_snapshot_unavailable")
    exported = await asyncio.to_thread(
        workspace.workspace.export,
        snapshot_path,
        format=ExportFormat.AUTO,
    )
    if not isinstance(exported.content, str) or not exported.content:
        raise RuntimeError("deployment_source_export_empty")
    try:
        data = base64.b64decode(exported.content, validate=True)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            remote = _snapshot_inventory(archive)
    except (ValueError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, OSError):
        raise RuntimeError("deployment_source_export_invalid") from None
    local = await asyncio.to_thread(_local_inventory, bundle)
    if not local:
        raise RuntimeError("deployment_bundle_empty")
    if remote != local:
        raise RuntimeError("deployment_source_bytes_mismatch")


def _snapshot_inventory(archive: zipfile.ZipFile) -> dict[str, str]:
    names = archive.namelist()
    if len(names) != len(set(names)) or any("\\" in name for name in names):
        raise ValueError("invalid_snapshot_paths")
    if "manifest.mf" not in names:
        raise ValueError("snapshot_manifest_missing")
    manifest = archive.read("manifest.mf")
    _validate_platform_manifest(manifest)
    source_files = [name for name in names if not name.endswith("/") and name != "manifest.mf"]
    if not source_files:
        raise ValueError("snapshot_source_missing")
    root = _snapshot_root(names)
    result: dict[str, str] = {}
    for name in source_files:
        prefix = root + "/"
        if not name.startswith(prefix):
            raise ValueError("snapshot_root_mismatch")
        relative = PurePosixPath(name[len(prefix) :]).as_posix()
        if relative in result:
            raise ValueError("duplicate_snapshot_path")
        result[relative] = hashlib.sha256(archive.read(name)).hexdigest()
    return result


def _snapshot_root(names: list[str]) -> str:
    roots: set[str] = set()
    for name in names:
        if name == "manifest.mf":
            continue
        parts = name.split("/")
        if parts[-1] == "":
            parts.pop()
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("invalid_snapshot_paths")
        if len(parts) == 1 and name.endswith("/"):
            roots.add(parts[0])
            continue
        if len(parts) < 2:
            raise ValueError("invalid_snapshot_paths")
        roots.add(parts[0])
    if len(roots) != 1:
        raise ValueError("snapshot_root_mismatch")
    root = roots.pop()
    if re.fullmatch(r"[0-9a-f]{32}", root) is None:
        raise ValueError("snapshot_root_invalid")
    return root


def _validate_platform_manifest(content: bytes) -> None:
    if not content or len(content) > 4096:
        raise ValueError("snapshot_manifest_size_invalid")
    manifest = json.loads(content.decode("utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != {"guid", "name", "origId", "version"}:
        raise ValueError("snapshot_manifest_shape_invalid")
    guid = manifest["guid"]
    try:
        parsed_guid = uuid.UUID(guid) if isinstance(guid, str) else None
    except ValueError:
        parsed_guid = None
    if (
        parsed_guid is None
        or manifest["name"] != "manifest.mf"
        or not isinstance(manifest["origId"], int)
        or isinstance(manifest["origId"], bool)
        or not -(2**63) <= manifest["origId"] < 2**63
        or not isinstance(manifest["version"], str)
        or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", manifest["version"]) is None
    ):
        raise ValueError("snapshot_manifest_shape_invalid")


def _local_inventory(bundle: Path) -> dict[str, str]:
    return {
        path.relative_to(bundle).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in bundle.rglob("*")
        if path.is_file()
    }


__all__ = ["deployed_bundle_matches_lock"]
