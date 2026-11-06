"""Real package-aware gates; absent implementations never become passing skips."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import quality
from boundaries.debt import load_debt
from browser_evidence import incomplete_marker, playwright_timed_out, record
from functional_watchdog import (
    DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
    functional_watchdog_seconds,
)
from functional_watchdog import (
    FUNCTIONAL_WATCHDOG_ENV as FUNCTIONAL_WATCHDOG_ENV,
)
from functional_watchdog import (
    MAX_FUNCTIONAL_WATCHDOG_MS as MAX_FUNCTIONAL_WATCHDOG_MS,
)
from functional_watchdog import (
    WATCHDOG_DECIMAL as WATCHDOG_DECIMAL,
)
from process import CommandIncompleteError, CommandPhase, CommandReason, run_command
from test_evidence import junit_named_results
from web_artifacts import inventory
from web_gate_receipt import AGGREGATE, record_aggregate
from web_platform_receipt import PlatformEvidence, verify_platform
from web_suites import resolve_paths, run_selected

NO_PLATFORM = PlatformEvidence()

SUITES = {
    "client-contract": (
        "tests/test_client*.py",
        "tests/client",
        "tests/test_reference_clients.py",
        "tests/test_public_reference_profiles.py",
        "tests/test_public_port_conformance.py",
        "tests/test_public_host_ownership.py",
        "tests/test_owned_jsonl_shutdown.py",
        "tests/test_remote*.py",
        "tests/test_tui*.py",
    ),
    "api-contract": ("packages/web-api/tests",),
    "ui-check": ("packages/web-ui/tests",),
    "package-check": ("tests/packaging",),
}
NPM_SCRIPTS = {
    "boundaries": ("check:boundaries",),
    "ui-check": (
        "check:wire",
        "lint",
        "check:boundaries",
        "typecheck",
        "build",
        "test",
        "check:packages",
        "test:e2e",
    ),
}
PACKAGE_FILES = (
    "packages/web-api/pyproject.toml",
    "packages/web-ui/pyproject.toml",
    "packages/databricks/pyproject.toml",
    "packages/client-ts/package.json",
    "packages/react/package.json",
    "packages/ui/package.json",
    "apps/reference-web/package.json",
)
NATIVE_OWNER_TESTS = (
    "direct functional owner retains and cleans its exact child group across repeated signals",
    "direct owner escalates when a pipe-closed descendant ignores termination",
    "externally supervised wrapper inherits the caller group and leaves cleanup to that owner",
    "owner reports a child failure before global readiness and leaves no child group",
    "standalone mode refuses unsupported process-group ownership before framework spawn",
    "direct owner drains child output after its output consumer closes",
)
REQUIRED = {
    "boundaries": ("package.json",),
    "client-contract": ("src/tether/client/protocol.py",),
    "api-contract": ("packages/web-api/pyproject.toml", "packages/web-api/schema/http-v1.json"),
    "ui-check": ("packages/web-ui/pyproject.toml", "package-lock.json"),
    "package-check": PACKAGE_FILES,
    "check": (*PACKAGE_FILES, "package-lock.json"),
    "platform-check": ("scripts/qualify_live_apps.py", "scripts/web_platform_receipt.py"),
    "release-postcheck": ("scripts/web_release_postcheck.py",),
    "release-check": (
        *PACKAGE_FILES,
        "scripts/qualify_web_platform.py",
        "scripts/audit_web_closure.py",
    ),
}


def missing_requirements(root: Path, gate: str) -> list[str]:
    return [path for path in REQUIRED[gate] if not (root / path).is_file()]


async def _framework_incomplete(root: Path, name: str) -> tuple[CommandReason, CommandPhase] | None:
    if name not in {"test", "test:e2e", "check:packages"}:
        return None
    framework = {
        "test": "vitest",
        "test:e2e": "playwright",
        "check:packages": "packages",
    }[name]
    marker = await asyncio.to_thread(incomplete_marker, root, framework)
    if marker is not None:
        return marker
    if name == "test:e2e":
        report = root / "artifacts" / "browser-report.json"
        if report.is_file() and await asyncio.to_thread(playwright_timed_out, report):
            return "watchdog_exhausted", "execution"
    return None


async def run_python_package_gate(
    root: Path, directory: Path | None, deadline_seconds: float
) -> int:
    marker = root / "artifacts" / "packages-incomplete.json"
    await asyncio.to_thread(marker.unlink, missing_ok=True)
    code = await command(
        package_command(directory),
        deadline_seconds=deadline_seconds,
        externally_supervised=True,
    )
    incomplete = await _framework_incomplete(root, "check:packages")
    if incomplete is not None:
        raise CommandIncompleteError(*incomplete)
    return code


async def _prepare_npm_evidence(root: Path, name: str) -> dict[str, str]:
    if name not in {"test", "test:e2e", "check:packages"}:
        return {}
    before = await asyncio.to_thread(inventory, root)
    filename = {
        "test": "vitest-report.json",
        "test:e2e": "browser-report.json",
        "check:packages": "npm-package-report.json",
    }[name]
    await asyncio.to_thread((root / "artifacts" / filename).unlink, missing_ok=True)
    framework = {"test": "vitest", "test:e2e": "playwright", "check:packages": "packages"}[name]
    await asyncio.to_thread(
        (root / "artifacts" / f"{framework}-incomplete.json").unlink, missing_ok=True
    )
    return before


async def _npm_script_result(root: Path, name: str, code: int, before: dict[str, str]) -> int:
    if name != "check:packages" and (not before or not code):
        return code
    incomplete = await _framework_incomplete(root, name)
    if incomplete is not None:
        raise CommandIncompleteError(*incomplete)
    return code


async def command(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
    externally_supervised: bool = False,
) -> int:
    print("+ " + " ".join(arguments), flush=True)
    # The tooling runner is the boundary that carries ownership into npm.
    previous_supervision = os.environ.get("TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED")  # noqa: TID251
    if externally_supervised:
        os.environ["TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED"] = "1"  # noqa: TID251
    try:
        if cwd is not None:
            result = await run_command(
                arguments,
                cwd=cwd,
                capture=False,
                deadline_seconds=deadline_seconds,
                own_process_group=True,
            )
        else:
            result = await run_command(
                arguments,
                capture=False,
                deadline_seconds=deadline_seconds,
                own_process_group=True,
            )
        return result.returncode
    finally:
        if externally_supervised:
            if previous_supervision is None:
                os.environ.pop("TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED", None)  # noqa: TID251
            else:
                os.environ["TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED"] = previous_supervision  # noqa: TID251


async def run_npm(
    root: Path,
    scripts: tuple[str, ...],
    *,
    deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
) -> int:
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    registered = manifest.get("scripts", {})
    if not isinstance(registered, dict):
        print("package.json scripts must be an explicit map", file=sys.stderr)
        return 1
    absent = [name for name in scripts if not registered.get(name)]
    npm = shutil.which("npm")
    if absent or npm is None:
        print(f"Required npm executable/scripts missing: {absent}", file=sys.stderr)
        return 1
    for name in scripts:
        before = await _prepare_npm_evidence(root, name)
        # Archive reproducibility and three clean consumer closures run several
        # separately bounded phases; this is not one pytest or Node consumer.
        code = await command(
            [npm, "run", name],
            deadline_seconds=deadline_seconds,
            externally_supervised=True,
        )
        code = await _npm_script_result(root, name, code, before)
        if code:
            return code
        if before:
            await asyncio.to_thread(record, root, name, before)
    return 0


async def run_native_owner_tests(
    root: Path, *, deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS
) -> int:
    """Run the six POSIX process-owner controls with a fresh builtin JUnit report."""
    if os.name != "posix":
        raise CommandIncompleteError("unsupported_platform", "spawn")
    node = shutil.which("node")
    if node is None:
        print("Required Node executable missing for native process-owner tests", file=sys.stderr)
        return 1
    required = {
        ("test", name): "tests/test_functional_command_owner.mjs::" + name
        for name in NATIVE_OWNER_TESTS
    }
    with tempfile.TemporaryDirectory(prefix="tether-native-owner-junit-") as temporary:
        report = Path(temporary) / "junit.xml"
        code = await command(
            [
                node,
                "--test",
                "--test-reporter=junit",
                "--test-reporter-destination=" + str(report),
                "tests/test_functional_command_owner.mjs",
            ],
            cwd=root,
            deadline_seconds=deadline_seconds,
        )
        try:
            outcomes = junit_named_results(report, required)
        except (OSError, ValueError) as error:
            print(f"Native process-owner JUnit report refused: {error}", file=sys.stderr)
            return code or 1
        if code or any(outcome["outcome"] != "passed" for outcome in outcomes):
            failed = [outcome for outcome in outcomes if outcome["outcome"] != "passed"]
            for outcome in failed:
                print(
                    f"Native process-owner case {outcome['node_id']}: {outcome['outcome']}",
                    file=sys.stderr,
                )
            if code and not failed:
                print(
                    "Native process-owner command exited nonzero after its JUnit report",
                    file=sys.stderr,
                )
            return code or 1
    return 0


async def simple_gate(
    root: Path,
    gate: str,
    profile: str | None,
    package_workdir: Path | None = None,
    *,
    platform: PlatformEvidence = NO_PLATFORM,
    deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
) -> int:
    python = sys.executable
    if gate == "boundaries":
        code = await command([python, "scripts/check_layers.py"], deadline_seconds=deadline_seconds)
        return code or await run_npm(root, NPM_SCRIPTS[gate], deadline_seconds=deadline_seconds)
    if gate == "platform-check":
        if not profile:
            print(
                "A selected live profile is required; missing access is not a pass", file=sys.stderr
            )
            return 1
        await asyncio.to_thread(verify_platform, root, profile, platform)
        print("Current selected Apps evidence verified for all carrier criteria", flush=True)
        return 0
    code = await run_selected(
        SUITES[gate], deadline_seconds=deadline_seconds, own_process_group=True
    )
    if code:
        return code
    checks = {
        "api-contract": [
            python,
            "-m",
            "tether_web_api.schema",
            "--check",
            "packages/web-api/schema/http-v1.json",
        ],
    }
    if gate in checks or gate == "package-check":
        return (
            await run_python_package_gate(root, package_workdir, deadline_seconds)
            if gate == "package-check"
            else await command(
                checks[gate], deadline_seconds=deadline_seconds, externally_supervised=True
            )
        )
    return (
        await run_npm(root, NPM_SCRIPTS[gate], deadline_seconds=deadline_seconds)
        if gate in NPM_SCRIPTS
        else 0
    )


def package_command(directory: Path | None) -> list[str]:
    arguments = [sys.executable, "scripts/check_web_packages.py"]
    return arguments + (["--retain-workdir", str(directory)] if directory is not None else [])


async def complete_gate(
    root: Path,
    gate: str,
    base: str,
    profile: str | None,
    package_workdir: Path | None = None,
    *,
    platform: PlatformEvidence = NO_PLATFORM,
    deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
) -> int:
    # The Python recipe runs in-process; each external command retains its own
    # configurable operator watchdog and incomplete commands cannot close a pass.
    await asyncio.to_thread(AGGREGATE.unlink, missing_ok=True)
    code = await run_npm(root, ("build",), deadline_seconds=deadline_seconds)
    if code:
        return code
    sources = await asyncio.to_thread(inventory, root)
    required = tuple(dict.fromkeys(path for paths in SUITES.values() for path in paths))
    python_evidence_dir = Path(tempfile.mkdtemp(prefix="tether-python-quality-"))
    python_evidence_path = python_evidence_dir / "python-evidence.json"
    code = await quality.check(
        base,
        required_roots=required,
        suite_deadline_seconds=deadline_seconds,
        command_deadline_seconds=deadline_seconds,
        python_evidence_path=python_evidence_path,
    )
    if code:
        return code
    code = await run_npm(root, NPM_SCRIPTS["ui-check"], deadline_seconds=deadline_seconds)
    if code:
        return code
    code = await run_native_owner_tests(root, deadline_seconds=deadline_seconds)
    if code:
        return code
    code = await run_python_package_gate(root, package_workdir, deadline_seconds)
    if code:
        return code
    await record_aggregate(root, sources, python_evidence_path)
    return (
        0
        if gate == "check"
        else await release_finish(root, profile, platform, deadline_seconds=deadline_seconds)
    )


async def release_finish(
    root: Path,
    profile: str | None,
    platform: PlatformEvidence,
    *,
    deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
) -> int:
    if any(load_debt(root).values()):
        print("WP-10 interface migration debt remains; release refused", file=sys.stderr)
        return 1
    code = await simple_gate(
        root, "platform-check", profile, platform=platform, deadline_seconds=deadline_seconds
    )
    return code or await command(
        [sys.executable, "scripts/audit_web_closure.py"], deadline_seconds=deadline_seconds
    )


async def execute(
    root: Path,
    gate: str,
    base: str,
    profile: str | None,
    package_workdir: Path | None = None,
    *,
    platform: PlatformEvidence = NO_PLATFORM,
    deadline_seconds: float = DEFAULT_FUNCTIONAL_WATCHDOG_SECONDS,
) -> int:
    missing = missing_requirements(root, gate)
    if missing:
        print("Unbuilt required gate inputs: " + ", ".join(missing), file=sys.stderr)
        return 1
    if gate == "release-postcheck":
        arguments = [
            sys.executable,
            "scripts/web_release_postcheck.py",
            "--profile",
            profile or "",
        ]
        if platform.origin:
            arguments += ["--app-origin", platform.origin]
        if platform.receipt is not None:
            arguments += ["--live-receipt", str(platform.receipt)]
        return await command(arguments, deadline_seconds=deadline_seconds)
    if gate in {"check", "release-check"}:
        required = tuple(path for paths in SUITES.values() for path in paths)
        await asyncio.to_thread(resolve_paths, root, required)
        return await complete_gate(
            root,
            gate,
            base,
            profile,
            package_workdir,
            platform=platform,
            deadline_seconds=deadline_seconds,
        )
    return await simple_gate(
        root,
        gate,
        profile,
        package_workdir,
        platform=platform,
        deadline_seconds=deadline_seconds,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=REQUIRED)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--profile")
    parser.add_argument("--package-workdir", type=Path)
    parser.add_argument("--live-receipt", type=Path)
    parser.add_argument("--app-origin")
    args = parser.parse_args()
    try:
        return await execute(
            Path.cwd(),
            args.gate,
            args.base,
            args.profile,
            args.package_workdir,
            platform=PlatformEvidence(args.live_receipt, args.app_origin),
            deadline_seconds=functional_watchdog_seconds(os.environ),  # noqa: TID251
        )
    except CommandIncompleteError as error:
        print(f"Web gate incomplete: {error.reason} in {error.phase}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        print(f"Web gate refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
