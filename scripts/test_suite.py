"""Run every collected case in bounded children and preserve complete outcomes."""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import math
import sys
import tempfile
from pathlib import Path

from process import CommandIncompleteError, run_command
from public_test_ids import validate_public_test_id
from selected_profiles_process import await_retained
from test_evidence import ObservedEvidence, junit_results
from web_artifacts import inventory

DEFAULT_CHILD_WATCHDOG_SECONDS = 1800.0
NATIVE_CASE_PREFIXES = ("tests/test_embedded_", "tests/test_postgres_")
EXPENSIVE_CASE_PREFIXES = (
    "tests/test_client_benchmark.py::",
    "tests/test_postgres_resource_shutdown.py::",
)


class SuiteEvidenceIncompleteError(ValueError):
    def __init__(self, reason: str, phase: str) -> None:
        self.reason = reason
        self.phase = phase
        super().__init__(f"python_suite_{reason}")


async def command(
    arguments: list[str], deadline_seconds: float | None = DEFAULT_CHILD_WATCHDOG_SECONDS
) -> int:
    print("+ " + " ".join(arguments), flush=True)
    result = await run_command(
        arguments,
        capture=False,
        deadline_seconds=deadline_seconds,
        own_process_group=True,
    )
    return result.returncode


def groups(values: object) -> list[list[str]]:
    if not isinstance(values, list) or not values:
        raise ValueError("empty_test_inventory")
    result: list[list[str]] = []
    current: list[str] = []
    seen: set[str] = set()
    used = 0
    native = False
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            raise ValueError("invalid_test_inventory")
        validate_public_test_id(value)
        size = len(value.encode("utf-8")) + 1
        if size > 64 * 1024:
            raise ValueError("test_id_too_large")
        is_native = value.startswith(NATIVE_CASE_PREFIXES)
        limit = 1 if value.startswith(EXPENSIVE_CASE_PREFIXES) else 8 if is_native else 32
        single = bool(current and current[0].startswith(EXPENSIVE_CASE_PREFIXES))
        if current and (
            single or native != is_native or len(current) >= limit or used + size > 64 * 1024
        ):
            result.append(current)
            current, used = [], 0
        current.append(value)
        used += size
        native = is_native
        seen.add(value)
    return [*result, current]


def covered(python: str, operation: str, data: Path) -> list[str]:
    return [python, "-m", "coverage", operation, f"--data-file={data}"]


async def run_batch(
    python: str,
    data: Path,
    batch: list[str],
    evidence: ObservedEvidence,
    deadline_seconds: float | None = DEFAULT_CHILD_WATCHDOG_SECONDS,
) -> int:
    with tempfile.TemporaryDirectory(prefix="tether-test-group-") as directory:
        report = Path(directory) / "observed.xml"
        try:
            code = await command(
                [
                    *covered(python, "run", data),
                    "--append",
                    "-m",
                    "pytest",
                    "--no-cov",
                    "--basetemp",
                    str(Path(directory) / "pytest"),
                    f"--junitxml={report}",
                    *batch,
                ],
                deadline_seconds,
            )
        except CommandIncompleteError as error:
            evidence.record_group(batch, "incomplete", [], None, error.reason)
            raise
        try:
            outcomes = await asyncio.to_thread(junit_results, report, batch)
        except Exception:
            evidence.record_group(batch, "incomplete", [], code, "junit_report_invalid")
            raise SuiteEvidenceIncompleteError("junit_report_invalid", "junit") from None
        if code == 0 and all(item["outcome"] == "passed" for item in outcomes):
            evidence.record_group(batch, "passed", outcomes, code)
        elif any(item["outcome"] == "failed" for item in outcomes) or any(
            item["outcome"] == "skipped" for item in outcomes
        ):
            evidence.record_group(batch, "failed", outcomes, code)
        else:
            evidence.record_group(batch, "incomplete", outcomes, code, "child_exit_unexplained")
            raise SuiteEvidenceIncompleteError("child_exit_unexplained", "pytest")
        return code


async def collect_and_run(
    python: str,
    directory: Path,
    required_roots: tuple[str, ...],
    evidence: ObservedEvidence,
    deadline_seconds: float | None = DEFAULT_CHILD_WATCHDOG_SECONDS,
) -> int:
    data = directory / ".coverage"
    try:
        code = await command(covered(python, "erase", data), deadline_seconds)
        if code:
            evidence.failure_reason, evidence.failure_phase = (
                "coverage_initialization_failed",
                "erase",
            )
            return code
        inventory_path = directory / "cases.json"
        collector = Path(__file__).with_name("collect_tests.py").resolve()
        code = await command(
            [*covered(python, "run", data), "--append", str(collector), str(inventory_path)],
            deadline_seconds,
        )
        if code:
            evidence.failure_reason, evidence.failure_phase = "collection_incomplete", "collect"
            return code
        nodes = json.loads(await asyncio.to_thread(inventory_path.read_text, encoding="utf-8"))
        batches = groups(nodes)
    except CommandIncompleteError:
        raise
    except ValueError:
        evidence.failure_reason, evidence.failure_phase = "invalid_test_inventory", "collect"
        raise
    except Exception:
        evidence.failure_reason, evidence.failure_phase = "collection_incomplete", "collect"
        raise SuiteEvidenceIncompleteError("collection_incomplete", "collect") from None
    evidence.inventory = nodes
    evidence.inventory_complete = True
    for root in required_roots:
        if not any(
            node.startswith(root.rstrip("/") + "/")
            or fnmatch.fnmatchcase(node.split("::")[0], root)
            for node in nodes
        ):
            evidence.failure_reason, evidence.failure_phase = "required_suite_empty", "inventory"
            raise SuiteEvidenceIncompleteError("required_suite_empty", "inventory")
    print(f"Collected {sum(map(len, batches))} cases in {len(batches)} bounded groups", flush=True)
    for batch in batches:
        code = await run_batch(python, data, batch, evidence, deadline_seconds)
        if code:
            return code
    code = await command(covered(python, "report", data), deadline_seconds)
    evidence.coverage_returncode = code
    return code


async def run_suite(
    python: str,
    required_roots: tuple[str, ...] = (),
    *,
    child_deadline_seconds: float | None = DEFAULT_CHILD_WATCHDOG_SECONDS,
    evidence_path: Path | None = None,
) -> int:
    if child_deadline_seconds is not None and (
        not math.isfinite(child_deadline_seconds) or child_deadline_seconds <= 0
    ):
        raise ValueError("suite child watchdog must be finite and positive")
    evidence = ObservedEvidence(await asyncio.to_thread(inventory, Path.cwd()))
    code: int | None = None
    cancelled = False
    try:
        with tempfile.TemporaryDirectory(prefix="tether-test-coverage-") as directory:
            code = await collect_and_run(
                python,
                Path(directory),
                required_roots,
                evidence,
                child_deadline_seconds,
            )
    except CommandIncompleteError as error:
        evidence.failure_reason, evidence.failure_phase = error.reason, error.phase
    except SuiteEvidenceIncompleteError as error:
        evidence.failure_reason, evidence.failure_phase = error.reason, error.phase
    except ValueError:
        evidence.failure_reason, evidence.failure_phase = "invalid_test_inventory", "collect"
    except asyncio.CancelledError:
        evidence.failure_reason, evidence.failure_phase = "cancelled", "suite"
        cancelled = True
    finally:
        writer = asyncio.create_task(
            asyncio.to_thread(evidence.write, Path.cwd(), code, evidence_path)
        )
        try:
            receipt = await await_retained(writer)
        except asyncio.CancelledError:
            cancelled = True
            receipt = writer.result()
        print(f"Observed Python evidence: {receipt}", flush=True)
    if cancelled:
        raise asyncio.CancelledError
    if evidence.qualified:
        return 0
    return code if code not in (None, 0) else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--child-watchdog-seconds", type=float, default=DEFAULT_CHILD_WATCHDOG_SECONDS
    )
    parser.add_argument("--externally-supervised", action="store_true")
    args = parser.parse_args()
    seconds = None if args.externally_supervised else args.child_watchdog_seconds
    return await run_suite(sys.executable, child_deadline_seconds=seconds)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
