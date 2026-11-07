"""Run the full suite against the lowest or highest declared base dependency versions.

The locked environment remains the release gate. This script builds an isolated
virtual environment from the locked export, replaces only the ranged runtime
dependencies, resolves them to one end of their declared range and runs the
same bounded suite as ``make check``. It writes a receipt with the requested
bounds and the exact resolved versions.
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

from packaging.utils import canonicalize_name
from process import run_command

ROOT = Path(__file__).resolve().parent.parent
# Runtime dependencies whose published ranges this matrix qualifies, with the
# distributions that move with them.
RANGED = {
    "anyio": "anyio>=4.15.1,<5",
    "pydantic": "pydantic>=2.12,<2.15",
    "pydantic-ai-slim": "pydantic-ai-slim[openai]>=2.59.0,<2.63",
    "psycopg": "psycopg[binary,pool]>=3.3.6,<3.4",
}
FOLLOWERS = ("pydantic-core", "pydantic-graph", "psycopg-binary", "psycopg-pool")
LOWEST = {
    "anyio": "anyio==4.15.1",
    "pydantic": "pydantic==2.12.0",
    "pydantic-ai-slim": "pydantic-ai-slim[openai]==2.59.0",
    "psycopg": "psycopg[binary,pool]==3.3.6",
}
# Locked transitive pins that the highest profile may move; the receipt records
# every resolved value so a reviewer can see what changed.
RELAXED_FOR_HIGHEST = ("openai", "genai-prices", "griffe", "opentelemetry-api", "httpx")
RECORDED = (*RANGED, *FOLLOWERS, *RELAXED_FOR_HIGHEST)


def unpinned_lock(text: str, drop: frozenset[str], relax: frozenset[str]) -> list[str]:
    """Drop ranged distributions and keep relaxed ones by name without their pin."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name = stripped.split(";")[0].split("==")[0].split("@")[0].split("[")[0].strip()
        key = canonicalize_name(name)
        if stripped.startswith("-e") or key not in drop | relax:
            lines.append(stripped)
        elif key in relax:
            marker = stripped.partition(";")[2].strip()
            lines.append(f"{name} ; {marker}" if marker else name)
    return lines


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise SystemExit(f"required tool not found: {name}")
    return found


async def checked(command: list[str]) -> str:
    print("+ " + " ".join(command), flush=True)
    executable = command[0] if Path(command[0]).is_absolute() else tool(command[0])
    result = await run_command([executable, *command[1:]], cwd=ROOT, deadline_seconds=1800)
    if result.returncode:
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


async def matrix(profile: str, output: Path) -> int:
    started = datetime.now(UTC)
    drop = frozenset(canonicalize_name(name) for name in (*RANGED, *FOLLOWERS))
    relax = frozenset(
        canonicalize_name(name) for name in RELAXED_FOR_HIGHEST if profile == "highest"
    )
    exported = await checked(
        ["uv", "export", "--locked", "--all-groups", "--all-extras", "--no-hashes"]
    )
    requested = LOWEST if profile == "lowest" else RANGED
    work = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix=f"tether-matrix-{profile}-"))
    try:
        requirements = work / "requirements.txt"
        lines = unpinned_lock(exported, drop, relax) + list(requested.values())
        await asyncio.to_thread(requirements.write_text, "\n".join(lines) + "\n", encoding="utf-8")
        venv = work / "venv"
        await checked(["uv", "venv", "--python", "3.11", str(venv)])
        python = str(venv / "bin" / "python")
        await checked(
            [
                *("uv", "pip", "install", "--python", python),
                *("--resolution", "highest", "-r", str(requirements)),
            ]
        )
        frozen = json.loads(
            await checked(["uv", "pip", "list", "--python", python, "--format", "json"])
        )
        versions = {canonicalize_name(p["name"]): p["version"] for p in frozen}
        print("+ " + python + " scripts/test_suite.py", flush=True)
        suite = await run_command(
            [python, "scripts/test_suite.py"],
            cwd=ROOT,
            capture=False,
            deadline_seconds=None,
            own_process_group=True,
        )
        commit = (await checked(["git", "rev-parse", "HEAD"])).strip()
        dirty = bool((await checked(["git", "status", "--porcelain"])).strip())
        receipt = {
            "kind": "tether.dependency-matrix",
            "version": 1,
            "profile": profile,
            "source_commit": commit,
            "source_dirty": dirty,
            "python": (await checked([python, "-c", "import sys;print(sys.version)"])).strip(),
            "platform": f"{platform.system()} {platform.machine()}",
            "declared_ranges": RANGED,
            "requested": requested,
            "resolved": {name: versions.get(canonicalize_name(name)) for name in RECORDED},
            "suite": "scripts/test_suite.py (full inventory, bounded groups, 85% branch floor)",
            "suite_returncode": suite.returncode,
            "started": started.isoformat(),
            "finished": datetime.now(UTC).isoformat(),
        }
        text = json.dumps(receipt, indent=2) + "\n"
        await asyncio.to_thread(output.write_text, text, encoding="utf-8")
        print(json.dumps(receipt["resolved"], indent=2))
        return suite.returncode or 0
    finally:
        await asyncio.to_thread(shutil.rmtree, work, ignore_errors=True)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("lowest", "highest"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return await matrix(args.profile, args.output)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
