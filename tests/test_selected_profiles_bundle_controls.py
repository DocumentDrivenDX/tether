"""Bounded controls for Databricks snapshot manifest and source bindings."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from selected_profiles_bundle import deployed_bundle_matches_lock

_ROOT = "0123456789abcdef0123456789abcdef"
_SOURCE = "/Workspace/qualification-source"
_DEPLOYMENT = "qualification-deployment-123"


def _manifest() -> bytes:
    return json.dumps(
        {
            "guid": str(uuid.uuid4()),
            "name": "manifest.mf",
            "origId": 0,
            "version": "1.0",
        }
    ).encode()


def _archive(members: dict[str, bytes]) -> str:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return base64.b64encode(output.getvalue()).decode("ascii")


def _archive_with_duplicate_member() -> str:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(_ROOT + "/app.yaml", b"locked application")
        archive.writestr(_ROOT + "/app.yaml", b"duplicate member")
        archive.writestr("manifest.mf", _manifest())
    return base64.b64encode(output.getvalue()).decode("ascii")


def _workspace(content: str) -> Any:
    class Workspace:
        def __init__(self) -> None:
            self.workspace = self
            self.apps = self

        def get_deployment(self, name: str, deployment_id: str) -> SimpleNamespace:
            assert name == "qualification-app"
            assert deployment_id == _DEPLOYMENT
            return SimpleNamespace(
                deployment_id=_DEPLOYMENT,
                source_code_path=_SOURCE,
                status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
                deployment_artifacts=SimpleNamespace(source_code_path="snapshot-export"),
            )

        def export(self, path: str, *, format: object) -> SimpleNamespace:
            assert path == "snapshot-export"
            return SimpleNamespace(content=content)

    return Workspace()


def _invoke(
    tmp_path: Path, workspace: Any, *, active_id: str = _DEPLOYMENT, active_source: str = _SOURCE
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.yaml").write_bytes(b"locked application")
    reference = tmp_path / "deployment.json"
    reference.write_text(
        json.dumps({"source_code_path": _SOURCE, "deployment_id": _DEPLOYMENT}),
        encoding="utf-8",
    )
    app = SimpleNamespace(
        active_deployment=SimpleNamespace(deployment_id=active_id, source_code_path=active_source)
    )
    asyncio.run(
        deployed_bundle_matches_lock(workspace, app, "qualification-app", bundle, reference)
    )


def test_deployed_bundle_accepts_exact_locked_source_and_generated_manifest(tmp_path: Path) -> None:
    content = _archive(
        {
            _ROOT + "/app.yaml": b"locked application",
            "manifest.mf": _manifest(),
        }
    )
    _invoke(tmp_path, _workspace(content))


@pytest.mark.parametrize(
    "members",
    [
        {_ROOT + "/app.yaml": b"locked application", "manifest.mf": b"{}"},
        {
            _ROOT + "/app.yaml": b"locked application",
            "manifest.mf": b"x" * 4097,
        },
        {
            _ROOT + "/app.yaml": b"locked application",
            _ROOT + "/manifest.mf": _manifest(),
        },
        {
            _ROOT + "/app.yaml": b"locked application",
            "manifest.mf": _manifest(),
            _ROOT + "/unexpected.txt": b"fabricated extra",
        },
        {
            _ROOT + "/app.yaml": b"locked application",
            _ROOT + "/src": b"file at directory path",
            _ROOT + "/src/child.txt": b"nested below file path",
            "manifest.mf": _manifest(),
        },
    ],
    ids=(
        "malformed-manifest",
        "oversized-manifest",
        "nested-manifest",
        "extra-source",
        "file-directory-ambiguity",
    ),
)
def test_deployed_bundle_rejects_invalid_manifest_or_unexpected_source(
    tmp_path: Path, members: dict[str, bytes]
) -> None:
    with pytest.raises(RuntimeError):
        _invoke(tmp_path, _workspace(_archive(members)))


def test_deployed_bundle_rejects_duplicate_zip_members(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="Duplicate name"):
        content = _archive_with_duplicate_member()
    with pytest.raises(RuntimeError, match="deployment_source_export_invalid"):
        _invoke(tmp_path, _workspace(content))


def test_deployed_bundle_requires_active_source_path_binding(tmp_path: Path) -> None:
    content = _archive(
        {
            _ROOT + "/app.yaml": b"locked application",
            "manifest.mf": _manifest(),
        }
    )
    with pytest.raises(RuntimeError, match="deployment_source_binding_mismatch"):
        _invoke(tmp_path, _workspace(content), active_source="/Workspace/other-source")
