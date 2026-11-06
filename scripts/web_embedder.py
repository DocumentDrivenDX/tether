"""Qualify a copied consumer with its own wheel-backed lock and environment."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypedDict

from test_evidence import junit_results
from web_artifacts import digest, installed_probe
from web_sbom import installed_sbom


class EmbedderReceipt(TypedDict):
    sbom: dict[str, object]
    status: str
    lock_sha256: str
    consumer_source: str
    wheel_backed_sources: list[str]
    test_results: list[dict[str, str]]
    strict_installed_typing: bool
    limits: list[str]


Check = Callable[[list[str], Path], Awaitable[str]]


async def qualify_embedder(
    uv: str,
    root: Path,
    wheels: dict[str, Path],
    directory: Path,
    checked: Check,
) -> EmbedderReceipt:
    consumer = directory / "independent-embedder"
    await asyncio.to_thread(
        shutil.copytree,
        root / "examples/embed",
        consumer,
        ignore=shutil.ignore_patterns(
            "dist", "build", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "uv.lock"
        ),
    )
    wheelhouse = consumer / "wheelhouse"
    await asyncio.to_thread(wheelhouse.mkdir)
    sources = []
    selected = ("novirhiza-tether", "novirhiza-tether-web-api", "novirhiza-tether-web-ui")
    for name in selected:
        destination = wheelhouse / wheels[name].name
        await asyncio.to_thread(shutil.copy2, wheels[name], destination)
        sources.append(f'{name} = {{ path = "wheelhouse/{destination.name}" }}')
    project = consumer / "pyproject.toml"
    text = await asyncio.to_thread(project.read_text)
    text = text.replace(
        'typeCheckingMode = "strict"', 'typeCheckingMode = "strict"\ninclude = ["src"]'
    )
    await asyncio.to_thread(
        project.write_text, text + "\n[tool.uv.sources]\n" + "\n".join(sources) + "\n"
    )
    await checked([uv, "lock", "--project", str(consumer), "--python", sys.executable], consumer)
    await checked([uv, "sync", "--locked", "--extra", "test", "--project", str(consumer)], consumer)
    python = (
        consumer / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    installed = json.loads(
        await checked(
            [
                str(python),
                "-I",
                "-c",
                installed_probe(selected, (), ("textual", "openai", "psycopg", "databricks-sdk")),
            ],
            consumer,
        )
    )
    await checked(
        [sys.executable, "-m", "pyright", "--pythonpath", str(python), "--project", str(consumer)],
        consumer,
    )
    collector = consumer / "collect_public_tests.py"
    await asyncio.to_thread(shutil.copy2, root / "scripts/collect_tests.py", collector)
    await asyncio.to_thread(
        shutil.copy2,
        root / "scripts/public_test_ids.py",
        consumer / "public_test_ids.py",
    )
    collector_text = await asyncio.to_thread(collector.read_text)
    await asyncio.to_thread(collector.write_text, collector_text.replace('"--no-cov", ', ""))
    collected, report = consumer / "cases.json", consumer / "observed.xml"
    await checked([str(python), str(collector), str(collected)], consumer)
    nodes = json.loads(await asyncio.to_thread(collected.read_text))
    if not nodes:
        raise ValueError("independent_embedder_has_no_tests")
    await checked(
        [
            str(python),
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "--no-header",
            "-q",
            f"--junitxml={report}",
        ],
        consumer,
    )
    results = await asyncio.to_thread(junit_results, report, nodes)
    if any(result["outcome"] != "passed" for result in results):
        raise ValueError("independent_embedder_required_cases_not_passed")
    observed = [
        {"node_id": "examples/embed/" + result["node_id"], "outcome": result["outcome"]}
        for result in results
    ]
    return {
        "status": "passed",
        "sbom": installed_sbom(installed["metadata"], selected, (), "independent-embedder"),
        "lock_sha256": digest((consumer / "uv.lock").read_bytes()),
        "consumer_source": "examples/embed",
        "wheel_backed_sources": sources,
        "test_results": observed,
        "strict_installed_typing": True,
        "limits": [
            "Fabricated independent project copy; no private config or source checkout dependency",
            "No live platform inference",
        ],
    }
