"""Refuse hash-pinned published cores outside the exact candidate package cohort."""

from __future__ import annotations

import asyncio
from email.parser import Parser
from pathlib import Path
from typing import TypedDict

from package_command_owner import run_package_command
from process import run_command
from web_embedder import Check

OLD_CORE = (
    "https://files.pythonhosted.org/packages/d9/5c/"
    "0da4f6e5c2e938f64956ad0d19eea3739b865cfe1d52475114f724084d7e/"
    "novirhiza_tether-0.1.0-py3-none-any.whl"
    "#sha256=df427a4c9660edd8712219c10fd1390134b1fc5b26e3427f72c76ae58adb8fd4"
)
PUBLISHED_CORE = (
    "https://files.pythonhosted.org/packages/33/38/"
    "c427cbcbfe52d90a98ba00fe50885e17bb630b05edb823c8ae0d6028e5d2/"
    "novirhiza_tether-0.2.0-py3-none-any.whl"
    "#sha256=d275bfe8ec54b893077277aee4cddd4a59ff20c9bc2f800a13e32b2de6a61c99"
)


class CoreRefusal(TypedDict):
    version: str
    published_artifact: str
    candidate_api_refused: bool


class CompatibilityReceipt(TypedDict):
    installed_old_core: str
    published_artifact: str
    candidate_api_refused: bool
    published_core_rejections: list[CoreRefusal]
    test_results: list[dict[str, str]]


def _installed_core_version(environment: Path) -> str:
    metadata = tuple(environment.rglob("novirhiza_tether-*.dist-info/METADATA"))
    if len(metadata) != 1:
        raise ValueError("published_core_installed_metadata_missing")
    value = Parser().parsestr(metadata[0].read_text(encoding="utf-8"))
    if value.get("Name") != "novirhiza-tether":
        raise ValueError("published_core_installed_name_mismatch")
    return str(value.get("Version", ""))


async def _refuse_published_core(
    uv: str, api: Path, directory: Path, checked: Check, version: str, artifact: str
) -> CoreRefusal:
    environment = directory / ("published-core-" + version + "-negative")
    import sys

    await checked([uv, "venv", "--python", sys.executable, str(environment)], directory)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    await checked([uv, "pip", "install", "--python", str(python), "--no-deps", artifact], directory)
    if await asyncio.to_thread(_installed_core_version, environment) != version:
        raise ValueError("old_core_artifact_version_mismatch")
    result = await run_package_command(
        run_command,
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--dry-run",
            "--index-url",
            "https://pypi.org/simple",
            str(api),
            artifact,
        ],
        cwd=directory,
    )
    refusal = result.stdout + result.stderr
    if not result.returncode or not all(
        text in refusal
        for text in (
            "No solution found",
            "novirhiza-tether==0.3.0",
            "requirements are unsatisfiable",
        )
    ):
        raise ValueError("expected_old_core_version_conflict_not_observed:" + refusal)
    return {"version": version, "published_artifact": artifact, "candidate_api_refused": True}


async def refuse_old_core(
    uv: str, api: Path, directory: Path, checked: Check
) -> CompatibilityReceipt:
    rejections = [
        await _refuse_published_core(uv, api, directory, checked, version, artifact)
        for version, artifact in (("0.1.0", OLD_CORE), ("0.2.0", PUBLISHED_CORE))
    ]
    return {
        "installed_old_core": "0.1.0",
        "published_artifact": OLD_CORE,
        "candidate_api_refused": True,
        "published_core_rejections": rejections,
        "test_results": [
            {"node_id": node, "outcome": "passed"}
            for node in (
                "installed::old-core::candidate-api-refused",
                "installed::published-core-0.2.0::candidate-api-refused",
            )
        ],
    }
