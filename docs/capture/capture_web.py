"""Capture real fabricated browser journeys; never synthesize scenario images."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import shutil
from pathlib import Path

from scripts.process import run_command
from scripts.web_artifacts import inventory

ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def observed_versions(interpreter: Path) -> dict[str, object]:
    python = await run_command(
        [
            str(interpreter),
            "-I",
            "-c",
            "import importlib.metadata as m,json,platform; "
            "print(json.dumps({'python':platform.python_version(),"
            "'packages':{n:m.version(n) for n in ('novirhiza-tether',"
            "'novirhiza-tether-web-api','novirhiza-tether-web-ui')}}))",
        ],
        cwd=ROOT,
    )
    node = shutil.which("node")
    npm = shutil.which("npm")
    if python.returncode or node is None or npm is None:
        raise RuntimeError("browser_capture_runtime_version_probe_failed")
    manager = await run_command([npm, "--version"], cwd=ROOT)
    if manager.returncode:
        raise RuntimeError("browser_capture_package_manager_version_probe_failed")
    browser = await run_command(
        [
            node,
            "--input-type=module",
            "-e",
            "import {chromium} from '@playwright/test'; "
            "const b=await chromium.launch(); try { "
            "console.log(JSON.stringify({node:process.versions.node,chromium:b.version()}));"
            "} finally {await b.close()}",
        ],
        cwd=ROOT,
    )
    if browser.returncode:
        raise RuntimeError("browser_capture_browser_version_probe_failed")
    return {
        **json.loads(python.stdout),
        **json.loads(browser.stdout),
        "npm": manager.stdout.strip(),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed-python", type=Path)
    arguments = parser.parse_args()
    sources = await asyncio.to_thread(inventory, ROOT)
    # Preserve the virtualenv entry point: resolving its symlink selects the base
    # interpreter and loses the installed artifact closure being qualified.
    interpreter = (arguments.installed_python or ROOT / ".venv/bin/python").absolute()
    if not await asyncio.to_thread(interpreter.is_file):
        raise RuntimeError("browser_capture_python_missing")
    versions = await observed_versions(interpreter)
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm_required")
    result = await run_command([npm, "run", "build"], cwd=ROOT, capture=False)
    if result.returncode:
        raise RuntimeError("browser_build_failed")
    command = [npm, "run", "test:e2e"]
    if arguments.installed_python is not None:
        environment = shutil.which("env")
        if environment is None:
            raise RuntimeError("environment_launcher_required")
        command = [environment, f"TETHER_BROWSER_PYTHON={interpreter}", *command]
    result = await run_command(command, cwd=ROOT, capture=False)
    if result.returncode:
        raise RuntimeError("browser_capture_failed")
    if sources != await asyncio.to_thread(inventory, ROOT):
        raise RuntimeError("browser_capture_source_changed")
    await asyncio.to_thread(write_receipt, arguments, versions, sources)


def write_receipt(
    arguments: argparse.Namespace, versions: dict[str, object], sources: dict[str, str]
) -> None:
    files = [
        ROOT / "docs/media/web-approval.png",
        ROOT / "docs/media/web-conversation.png",
        ROOT / "docs/media/web-mobile.png",
    ]
    source_paths = [
        Path(__file__).resolve(),
        ROOT / "tests/ui/reference.spec.ts",
        ROOT / "tests/ui/server.py",
        ROOT / "tests/ui/fixture.py",
    ]
    for directory in (
        "packages/client-ts/src",
        "packages/react/src",
        "packages/ui/src",
        "apps/reference-web/src",
    ):
        source_paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    receipt = {
        "profile": "installed-wheel-browser"
        if arguments.installed_python is not None
        else "development-source-browser",
        "released": False,
        "versions": versions,
        "platform": platform.system(),
        "capture": "Chromium Playwright screenshots from actual prefixed API/UI journeys",
        "data": "fabricated model, tool and prompt only; isolated temporary JSONL store",
        "assertions": [
            "nested mount and assets",
            "exact tool approval by keyboard",
            "committed history after reload",
            "untrusted markup remains text",
            "other-owner disclosure refused",
            "axe accessibility scan has zero violations",
            "targeted cancellation and explicit resume",
            "close controls distinguish closed live sessions from archived restoration",
        ],
        "images": {path.relative_to(ROOT).as_posix(): digest(path) for path in files},
        "sources": {path.relative_to(ROOT).as_posix(): digest(path) for path in source_paths},
        "source_sha256": sources,
    }
    (ROOT / "docs/media/web-journey-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
