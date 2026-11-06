"""Build wheels/sdists twice and probe independent clean installed profiles."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from browser_evidence import playwright_results
from functional_watchdog import functional_watchdog_seconds
from package_command_owner import (
    INHERITED_POLICY,
    STANDALONE_POLICY,
    run_package_command,
    use_package_command_policy,
)
from process import CommandIncompleteError, run_command
from web_artifacts import (
    MATRIX,
    PROJECTS,
    archive_contents,
    digest,
    distribution_checks,
    installed_probe,
    inventory,
    write_receipt,
)
from web_compatibility import refuse_old_core
from web_consumer import CORE, LEGACY, UI
from web_embedder import qualify_embedder
from web_remote_probe import installed_remote
from web_sbom import installed_sbom


class InstalledProfile(TypedDict):
    harness_requirements: list[str]
    sbom: dict[str, object]
    modules: list[str]
    isolated_import_paths_verified: bool
    dependencies: list[list[str]]
    extras: tuple[str, ...]
    projects: tuple[str, ...]
    test_results: list[dict[str, object]]


async def checked(arguments: list[str], cwd: Path) -> str:
    shown = (
        [*arguments[: arguments.index("-c") + 1], "<fabricated installed consumer>"]
        if "-c" in arguments
        else arguments
    )
    print("+ " + " ".join(shown), flush=True)
    result = await run_package_command(run_command, arguments, cwd=cwd)
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise ValueError(f"artifact_command_failed:{result.returncode}")
    return result.stdout


async def checked_remote(arguments: list[str], cwd: Path) -> str:
    """Keep remote-probe diagnostics closed while preserving the command owner."""
    result = await run_package_command(run_command, arguments, cwd=cwd)
    if result.returncode:
        raise ValueError("installed_remote_command_failed")
    return result.stdout


async def build(uv: str, root: Path, output: Path, constraints: Path) -> dict[str, Path]:
    wheels = {}
    for name, directory in PROJECTS.items():
        destination = output / name
        await checked(
            [
                uv,
                "build",
                "--python",
                sys.executable,
                "--build-constraints",
                str(constraints),
                "--no-sources",
                "--out-dir",
                str(destination),
            ],
            root / directory,
        )
        found = tuple(destination.glob("*.whl"))
        sources = tuple(destination.glob("*.tar.gz"))
        if len(found) != 1 or len(sources) != 1:
            raise ValueError("build_requires_wheel_and_sdist:" + name)
        wheels[name] = found[0]
    return wheels


async def installed_browser(python: Path) -> list[dict[str, object]]:
    npm, env = shutil.which("npm"), shutil.which("env")
    if npm is None or env is None:
        raise ValueError("npm_and_env_required_for_installed_browser_acceptance")
    root = Path.cwd()
    report = root / "artifacts/browser-report.json"
    await asyncio.to_thread(report.unlink, missing_ok=True)
    await checked([env, f"TETHER_BROWSER_PYTHON={python}", npm, "run", "test:e2e"], root)
    observed = await asyncio.to_thread(playwright_results, report, root)
    if any(result["outcome"] != "passed" for result in observed):
        raise ValueError("installed_browser_required_cases_did_not_pass")
    return observed


async def profile_checks(
    python: Path,
    name: str,
    projects: tuple[str, ...],
    extras: tuple[str, ...],
    directory: Path,
) -> list[dict[str, object]]:
    observed: list[dict[str, object]] = [
        {"node_id": f"installed::{name}::closure", "outcome": "passed"}
    ]
    for required, program, label in (
        ("novirhiza-tether", CORE, "host-recovery"),
        ("novirhiza-tether", LEGACY, "legacy-composition"),
        ("novirhiza-tether-web-ui", UI, "packaged-assets"),
    ):
        if required in projects:
            await checked([str(python), "-I", "-c", program], directory)
            observed.append({"node_id": f"installed::{name}::{label}", "outcome": "passed"})
    if "tui" in extras:
        terminal = python.parent / ("tether.exe" if sys.platform == "win32" else "tether")
        await checked([str(terminal), "--help"], directory)
        observed.append({"node_id": f"installed::{name}::terminal-entrypoint", "outcome": "passed"})
    if name == "combined":
        observed.extend(await installed_browser(python))
    return observed


async def matrix(
    uv: str, wheels: dict[str, Path], directory: Path, runtime_constraints: Path
) -> dict[str, InstalledProfile]:
    receipts: dict[str, InstalledProfile] = {}
    for name, (projects, extras, absent) in MATRIX.items():
        environment = directory / name
        await checked([uv, "venv", "--python", sys.executable, str(environment)], directory)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        selected = [
            str(wheels[project])
            + ("[" + ",".join(extras) + "]" if project == "novirhiza-tether" and extras else "")
            for project in projects
        ]
        harness = ["hypercorn==0.18.0"] if name == "combined" else []
        await checked(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--constraint",
                str(runtime_constraints),
                *selected,
                *harness,
            ],
            directory,
        )
        result = await checked(
            [str(python), "-I", "-c", installed_probe(projects, extras, absent)], directory
        )
        observed = await profile_checks(python, name, projects, extras, directory)
        details = json.loads(result)
        receipts[name] = {
            "modules": sorted(details["modules"]),
            "harness_requirements": harness,
            "isolated_import_paths_verified": True,
            "dependencies": details["dependencies"],
            "sbom": installed_sbom(details["metadata"], projects, extras, name),
            "extras": extras,
            "projects": projects,
            "test_results": observed,
        }
    return receipts


def compare_builds(first: Path, second: Path) -> dict[str, object]:
    """@covers US-069-AC5: compare actual members, including wire and asset manifests."""
    files = sorted(
        path.relative_to(first)
        for path in first.rglob("*")
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    other = sorted(
        path.relative_to(second)
        for path in second.rglob("*")
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if files != other:
        raise ValueError("rebuild_artifact_inventory_changed")
    receipts = {}
    for relative in files:
        left, right = first / relative, second / relative
        if archive_contents(left) != archive_contents(right):
            raise ValueError("rebuild_contents_differ:" + str(relative))
        receipts[str(relative)] = {
            "sha256_first": digest(left.read_bytes()),
            "sha256_second": digest(right.read_bytes()),
            "normalized_contents_identical": True,
        }
    return receipts


async def rebuild_sdists(
    uv: str, first: Path, directory: Path, constraints: Path, wheels: dict[str, Path]
) -> list[dict[str, str]]:
    """Rebuild outside the checkout and compare every wheel member, including metadata."""
    outcomes: list[dict[str, str]] = []
    for name in PROJECTS:
        sdist = next((first / name).glob("*.tar.gz"))
        await checked(
            [
                uv,
                "build",
                "--python",
                sys.executable,
                "--build-constraints",
                str(constraints),
                "--no-sources",
                "--wheel",
                "--out-dir",
                str(directory / "from-sdist" / name),
                str(sdist),
            ],
            directory,
        )
        rebuilt = tuple((directory / "from-sdist" / name).glob("*.whl"))
        if len(rebuilt) != 1 or archive_contents(rebuilt[0]) != archive_contents(wheels[name]):
            raise ValueError("sdist_rebuilt_wheel_contents_differ:" + name)
        outcomes.append({"node_id": f"installed::{name}::sdist-rebuilt-wheel", "outcome": "passed"})
    return outcomes


def locked_runtime_pins(root: Path) -> list[str]:
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    versions: dict[str, str] = {}
    for package in packages:
        if "registry" not in package.get("source", {}):
            continue
        name, version = package["name"], package["version"]
        if name in versions and versions[name] != version:
            raise ValueError("ambiguous_runtime_lock_profile:" + name)
        versions[name] = version
    if not versions:
        raise ValueError("locked_registry_runtime_cohort_required")
    return [name + "==" + version for name, version in sorted(versions.items())]


def workspace(root: Path, retained: Path | None) -> AbstractContextManager[str]:
    if retained is None:
        return tempfile.TemporaryDirectory(prefix="tether-installed-")
    directory = retained.absolute()
    if root.resolve() in (directory.resolve(), *directory.resolve().parents):
        raise ValueError("retained_artifact_workspace_must_be_outside_checkout")
    directory.mkdir(mode=0o700, parents=True)
    return nullcontext(str(directory))


async def run(root: Path, output: Path, retained: Path | None = None) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("uv_required_for_installed_package_acceptance")
    before = await asyncio.to_thread(inventory, root)
    context = await asyncio.to_thread(workspace, root, retained)
    with context as temporary:
        directory = Path(temporary)
        first, second = directory / "first", directory / "second"
        pins = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["uv"][
            "build-constraint-dependencies"
        ]
        if not pins or any("==" not in pin for pin in pins):
            raise ValueError("exact_build_toolchain_constraints_required")
        constraints = directory / "build-constraints.txt"
        await asyncio.to_thread(constraints.write_text, "\n".join(pins) + "\n")
        wheels = await build(uv, root, first, constraints)
        await build(uv, root, second, constraints)
        comparison = await asyncio.to_thread(compare_builds, first, second)
        metadata = {
            name: await asyncio.to_thread(
                distribution_checks,
                root,
                path,
                next((first / name).glob("*.tar.gz")),
                name,
            )
            for name, path in wheels.items()
        }
        rebuilt_cases = await rebuild_sdists(uv, first, directory, constraints, wheels)
        compatibility = await refuse_old_core(
            uv, wheels["novirhiza-tether-web-api"], directory, checked
        )
        runtime_pins = locked_runtime_pins(root)
        runtime_constraints = directory / "runtime-constraints.txt"
        await asyncio.to_thread(runtime_constraints.write_text, "\n".join(runtime_pins) + "\n")
        profiles = await matrix(uv, wheels, directory, runtime_constraints)
        remote_cases = await installed_remote(directory, checked_remote)
        embedder = await qualify_embedder(uv, root, wheels, directory, checked)
        after = await asyncio.to_thread(inventory, root)
        if before != after:
            raise ValueError("source_changed_during_artifact_acceptance_rerun_on_frozen_inputs")
        receipt = {
            "status": "passed",
            "schema_version": 1,
            "command": "make web-package-check / clean build and installed matrix",
            "recorded_at": datetime.now(UTC).isoformat(),
            "python": sys.version,
            "build_constraints": pins,
            "runtime_constraints": runtime_pins,
            "source_sha256": before,
            "artifacts": comparison,
            "wheel_metadata": metadata,
            "installed_profiles": profiles,
            "independent_embedder": embedder,
            "compatibility": compatibility,
            "test_results": [
                result for profile in profiles.values() for result in profile["test_results"]
            ]
            + embedder["test_results"]
            + compatibility["test_results"]
            + rebuilt_cases
            + remote_cases
            + [
                {"node_id": f"installed::{name}::license-namespace-types", "outcome": "passed"}
                for name in metadata
            ]
            + [{"node_id": "installed::cohort::reproducible-archives", "outcome": "passed"}],
            "limits": [
                "Python installed artifact closure only",
                "No live platform acceptance; browser uses the combined installed profile",
                "Normalized archive member hashes compared; raw gzip metadata may differ",
            ],
        }
        await asyncio.to_thread(write_receipt, output, receipt)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "tether-web-package-receipt.json",
    )
    parser.add_argument(
        "--retain-workdir",
        type=Path,
        help="New private directory outside the checkout; default removes temporary builds",
    )
    args = parser.parse_args()
    try:
        watchdog_seconds, externally_supervised = _owner_settings()
    except ValueError:
        print(
            "Installed package gate incomplete: invalid owner watchdog configuration",
            file=sys.stderr,
        )
        return 2
    output = args.output.resolve()
    attempt = output.with_name(f".{output.name}.{uuid4().hex}.pending")
    owner: asyncio.Timeout | None = None

    try:
        if not externally_supervised:
            (Path.cwd() / "artifacts" / "packages-incomplete.json").unlink(missing_ok=True)
        with use_package_command_policy(
            INHERITED_POLICY if externally_supervised else STANDALONE_POLICY
        ):
            if externally_supervised:
                await run(Path.cwd(), attempt, args.retain_workdir)
            else:
                owner = asyncio.timeout(watchdog_seconds)
                async with owner:
                    await run(Path.cwd(), attempt, args.retain_workdir)
                if owner.expired():
                    raise CommandIncompleteError("watchdog_exhausted", "execution")
        output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(attempt, output)
        print(f"Installed package acceptance receipt: {output}")
    except CommandIncompleteError as error:
        print(f"Installed package gate incomplete: {error}", file=sys.stderr)
        _record_incomplete(error.reason, error.phase)
        return 2
    except TimeoutError as error:
        if owner is not None and owner.expired():
            print("Installed package gate incomplete: watchdog_exhausted", file=sys.stderr)
            _record_incomplete("watchdog_exhausted", "execution")
            return 2
        raise error
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        print(f"Installed package gate failed: {error}", file=sys.stderr)
        return 1
    finally:
        attempt.unlink(missing_ok=True)
    return 0


def _owner_settings() -> tuple[float, bool]:
    return (
        functional_watchdog_seconds(os.environ),  # noqa: TID251
        os.environ.get("TETHER_FUNCTIONAL_WATCHDOG_SUPERVISED") == "1",  # noqa: TID251
    )


def _record_incomplete(reason: str, phase: str) -> None:
    marker = Path.cwd() / "artifacts" / "packages-incomplete.json"
    marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".packages-incomplete-", dir=marker.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "schema_version": 1,
                    "status": "incomplete",
                    "framework": "packages",
                    "reason": reason,
                    "phase": phase,
                },
                stream,
                separators=(",", ":"),
            )
            stream.write("\n")
        os.replace(name, marker)
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
