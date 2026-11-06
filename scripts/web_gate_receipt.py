"""Bind a successful full deterministic aggregate to its exact frozen inputs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from process import run_command
from test_evidence import validate_python_receipt
from web_artifacts import inventory, write_receipt

AGGREGATE = Path(tempfile.gettempdir()) / "tether-web-check-receipt.json"
STAGES = (
    "python-quality",
    "check:wire",
    "lint",
    "check:boundaries",
    "typecheck",
    "build",
    "test",
    "check:packages",
    "test:e2e",
    "native-owner-tests",
    "installed-packages",
)
LIMITS = (
    "Full deterministic gate only; live/performance/acceptance closure remain separate",
    "The native process-owner test stage requires POSIX process groups",
)


def _json_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


async def record_aggregate(root: Path, sources: dict[str, str], python_evidence_path: Path) -> None:
    if not sources or sources != await asyncio.to_thread(inventory, root):
        raise ValueError("source_changed_during_full_web_aggregate")
    python_bytes = await asyncio.to_thread(python_evidence_path.read_bytes)
    if len(python_bytes) > 4 * 1024 * 1024:
        raise ValueError("python_evidence_exceeds_aggregate_bound")
    python_evidence = validate_python_receipt(
        json.loads(python_bytes), await asyncio.to_thread(inventory, root)
    )
    python_summary = {
        "receipt_sha256": hashlib.sha256(python_bytes).hexdigest(),
        "receipt_json": python_bytes.decode("utf-8"),
        "status": python_evidence["status"],
        "source_sha256": python_evidence["source_sha256"],
        "inventory_sha256": _json_digest(python_evidence["inventory"]),
        "outcomes_sha256": _json_digest(python_evidence["test_results"]),
    }
    versions = {"python": platform.python_version()}
    for name in ("node", "npm"):
        executable = shutil.which(name)
        if executable is None:
            raise ValueError("qualified_runtime_missing:" + name)
        result = await run_command([executable, "--version"], cwd=root)
        if result.returncode:
            raise ValueError("qualified_runtime_unavailable:" + name)
        versions[name] = result.stdout.strip()
    if not sources or sources != await asyncio.to_thread(inventory, root):
        raise ValueError("source_changed_during_full_web_aggregate")
    await asyncio.to_thread(
        write_receipt,
        AGGREGATE,
        {
            "status": "passed",
            "schema_version": 1,
            "command": "make web-check / complete deterministic core and web aggregate",
            "recorded_at": datetime.now(UTC).isoformat(),
            "source_sha256": sources,
            "python_quality": python_summary,
            "versions": versions,
            "stages": list(STAGES),
            "test_results": [
                {"node_id": "scripts/web_quality.py::" + stage, "outcome": "passed"}
                for stage in STAGES
            ],
            "limits": list(LIMITS),
        },
    )
    print(f"Completed deterministic aggregate receipt: {AGGREGATE}", flush=True)


def verify_aggregate(root: Path, path: Path) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("aggregate_exceeds_bounded_size")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "status",
            "schema_version",
            "command",
            "recorded_at",
            "source_sha256",
            "python_quality",
            "versions",
            "stages",
            "test_results",
            "limits",
        }
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 1
        or value.get("status") != "passed"
        or value.get("source_sha256") != inventory(root)
    ):
        raise ValueError("aggregate_missing_failed_or_stale")
    if value.get("stages") != list(STAGES) or value.get("test_results") != [
        {"node_id": "scripts/web_quality.py::" + stage, "outcome": "passed"} for stage in STAGES
    ]:
        raise ValueError("aggregate_required_stages_not_observed")
    versions = value.get("versions", {})
    if (
        not isinstance(versions, dict)
        or set(versions) != {"python", "node", "npm"}
        or versions.get("node") != "v22.22.2"
        or not isinstance(versions.get("python"), str)
        or not versions["python"].startswith("3.11.")
        or not isinstance(versions.get("npm"), str)
    ):
        raise ValueError("aggregate_runtime_outside_qualified_profile")
    manifest = json.loads((root / "package.json").read_text())
    if manifest.get("packageManager") != "npm@" + versions.get("npm", ""):
        raise ValueError("aggregate_npm_differs_from_declared_toolchain")
    if (
        value.get("command") != "make web-check / complete deterministic core and web aggregate"
        or not isinstance(value.get("recorded_at"), str)
        or not value.get("recorded_at")
        or value.get("limits") != list(LIMITS)
    ):
        raise ValueError("aggregate_provenance_missing")
    python = value.get("python_quality")
    if (
        not isinstance(python, dict)
        or set(python)
        != {
            "receipt_sha256",
            "receipt_json",
            "status",
            "source_sha256",
            "inventory_sha256",
            "outcomes_sha256",
        }
        or not isinstance(python.get("receipt_json"), str)
    ):
        raise ValueError("aggregate_python_evidence_binding_missing")
    raw = python["receipt_json"].encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != python.get("receipt_sha256"):
        raise ValueError("aggregate_python_evidence_hash_mismatch")
    observed = validate_python_receipt(json.loads(raw), inventory(root))
    if (
        python.get("status") != "passed"
        or python.get("source_sha256") != observed.get("source_sha256")
        or python.get("inventory_sha256") != _json_digest(observed["inventory"])
        or python.get("outcomes_sha256") != _json_digest(observed["test_results"])
    ):
        raise ValueError("aggregate_python_evidence_join_failed")
    return value
