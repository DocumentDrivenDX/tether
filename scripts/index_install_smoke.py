"""Install a published version from the public index into fresh environments.

Each profile gets its own virtual environment and an empty uv cache, so no
locally built wheel or checkout file can satisfy the install. The receipt names
the resolved distribution hashes reported by the index and the installed
versions of the runtime dependencies.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from process import run_command

INDEX = "https://pypi.org/simple"
PROFILES = ("", "postgres", "openai", "tui")
SMOKE = (
    "import importlib.metadata as md, json, tether;"
    "from tether.types import ModelExecutionLimits;"
    "names = ('novirhiza-tether', 'pydantic', 'pydantic-ai-slim', 'anyio', 'psycopg',"
    " 'openai', 'textual');"
    "versions = {};\n"
    "for n in names:\n"
    "    try: versions[n] = md.version(n)\n"
    "    except md.PackageNotFoundError: pass\n"
    "print(json.dumps(versions))"
)


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"required tool not found: {name}")
    return found


async def run(command: list[str], *, env_cache: Path) -> str:
    print("+ " + " ".join(command), flush=True)
    result = await run_command(
        ["/usr/bin/env", f"UV_CACHE_DIR={env_cache}", "UV_NO_CONFIG=1", *command],
        deadline_seconds=900,
    )
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


async def profile(version: str, extra: str, python: str) -> dict[str, object]:
    work = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="tether-index-"))
    try:
        cache, venv = work / "cache", work / "venv"
        uv = tool("uv")
        await run([uv, "venv", "--python", python, str(venv)], env_cache=cache)
        interpreter = str(venv / "bin" / "python")
        spec = f"novirhiza-tether[{extra}]=={version}" if extra else f"novirhiza-tether=={version}"
        await run(
            [uv, "pip", "install", "--python", interpreter, "--index-url", INDEX, spec],
            env_cache=cache,
        )
        installed = json.loads(await run([interpreter, "-I", "-c", SMOKE], env_cache=cache))
        return {"extra": extra or "base", "requirement": spec, "installed": installed}
    finally:
        await asyncio.to_thread(shutil.rmtree, work, ignore_errors=True)


async def index_hashes(version: str) -> list[dict[str, str]]:
    url = f"https://pypi.org/pypi/novirhiza-tether/{version}/json"
    body = await run([tool("curl"), "-fsS", url], env_cache=Path(tempfile.gettempdir()))
    files = json.loads(body)["urls"]
    return [
        {
            "filename": item["filename"],
            "sha256": item["digests"]["sha256"],
            "upload_time": item["upload_time_iso_8601"],
        }
        for item in files
    ]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("--python", default="3.11")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(UTC).isoformat()
    results = [await profile(args.version, extra, args.python) for extra in PROFILES]
    receipt = {
        "kind": "tether.index-install-smoke",
        "version": 1,
        "package_version": args.version,
        "index": INDEX,
        "platform": f"{platform.system()} {platform.machine()}",
        "python_request": args.python,
        "published_files": await index_hashes(args.version),
        "profiles": results,
        "started": started,
        "finished": datetime.now(UTC).isoformat(),
    }
    text = json.dumps(receipt, indent=2) + "\n"
    await asyncio.to_thread(args.output.write_text, text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
