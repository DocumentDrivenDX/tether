"""Verify current selected Apps evidence without repeating a destructive restart."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from qualify_live_apps_config import safe_origin
from qualify_live_apps_evidence import LIVE_CASE_IDS, profile_binding_sha256
from qualify_selected_profiles import PROFILE_CASE_IDS
from web_artifacts import PROJECTS, inventory
from web_closure import (
    MANIFEST,
    PLAN,
    ac_inventory,
    case_errors,
    primary_mapping,
    read_receipt,
    relative_file,
)
from web_transport_receipt import validate_shutdown_observation

CARRIER_CRITERIA = {f"US-068-AC{number}" for number in range(1, 11)}


@dataclass(frozen=True)
class PlatformEvidence:
    receipt: Path | None = None
    origin: str | None = None


def _read(path: Path) -> dict:
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("platform_receipt_exceeds_bounded_size")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("platform_receipt_must_be_object")
    return value


def _versions(root: Path, receipt: dict) -> None:
    versions = receipt.get("versions", {})
    manifest = json.loads((root / "package.json").read_text())
    lock = json.loads((root / "package-lock.json").read_text())
    expected = lock["packages"]["node_modules/@playwright/test"]["version"]
    if (
        not isinstance(versions, dict)
        or versions.get("node") != "v22.22.2"
        or not str(versions.get("python", "")).startswith("3.11.")
        or manifest.get("packageManager") != "npm@" + str(versions.get("npm", ""))
        or versions.get("playwright") != expected
        or not versions.get("platform")
        or not versions.get("architecture")
    ):
        raise ValueError("platform_driver_runtime_outside_qualified_cohort")


def _app_versions(root: Path, runtime: object) -> None:
    if not isinstance(runtime, dict) or not str(runtime.get("python", "")).startswith("3.11."):
        raise ValueError("platform_app_runtime_missing_or_unqualified")
    if not runtime.get("platform") or not runtime.get("architecture"):
        raise ValueError("platform_app_environment_missing")
    distributions = runtime.get("distributions")
    if not isinstance(distributions, dict):
        raise ValueError("platform_app_package_versions_missing")
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    expected = {item["name"]: item["version"] for item in packages}
    for name, directory in PROJECTS.items():
        expected[name] = tomllib.loads((root / directory / "pyproject.toml").read_text())[
            "project"
        ]["version"]
    required = {*PROJECTS, "hypercorn", "databricks-sdk", "psycopg", "psycopg-binary"}
    if any(
        not expected.get(name) or distributions.get(name) != expected[name] for name in required
    ):
        raise ValueError("platform_app_packages_differ_from_locked_cohort")


def _shutdown(root: Path, value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("platform_shutdown_measurement_required")
    validate_shutdown_observation(root, value)
    _app_versions(root, value.get("runtime"))


def verify_live(
    root: Path, profile: str | None, selected: PlatformEvidence
) -> tuple[dict, dict[str, str]]:
    if not profile or selected.receipt is None or not selected.origin:
        raise ValueError("explicit_platform_profile_origin_and_receipt_required")
    binding = profile_binding_sha256(profile, safe_origin(selected.origin))
    current = inventory(root)
    data = _read(selected.receipt)
    if (
        not current
        or data.get("status") != "passed"
        or data.get("schema_version") != 1
        or data.get("profile") != "selected-databricks-apps"
        or data.get("source_sha256") != current
        or data.get("source_sha256_after") != current
        or data.get("profile_binding_sha256") != binding
        or not data.get("command")
        or not data.get("recorded_at")
    ):
        raise ValueError("selected_platform_receipt_missing_failed_stale_or_wrong_profile")
    results = data.get("test_results")
    if not isinstance(results, list) or len(results) != len(LIVE_CASE_IDS):
        raise ValueError("selected_platform_exact_cases_required")
    if any(not isinstance(item, dict) or item.get("outcome") != "passed" for item in results):
        raise ValueError("selected_platform_required_case_not_passed")
    if {item.get("node_id") for item in results} != set(LIVE_CASE_IDS):
        raise ValueError("selected_platform_exact_cases_required")
    _versions(root, data)
    _shutdown(root, data.get("shutdown"))
    return data, current


def _proof_versions(root: Path, data: dict, tests: set[str]) -> None:
    _versions(root, data)
    if not tests.intersection(PROFILE_CASE_IDS.values()):
        return
    source = data.get("credential_source")
    if not isinstance(source, str) or source not in {"keychain-child-sdk", "injected-factory"}:
        raise ValueError("selected_profile_credential_source_required")
    versions = data["versions"]
    collector = versions.get("selected_profile_collector")
    if (
        not isinstance(collector, dict)
        or not str(collector.get("python", "")).startswith("3.11.")
        or not collector.get("platform")
        or not collector.get("architecture")
    ):
        raise ValueError("selected_profile_collector_runtime_missing_or_unqualified")
    _app_versions(root, versions.get("app_runtime"))


def _bound_proof(root: Path, case: dict, current: dict[str, str], binding: str) -> bool:
    tests = set(case["tests"])
    for name in case["receipts"]:
        observed = read_receipt(root, name, current)
        path = relative_file(root, name, "docs/helix/04-build/evidence/web-platform/")
        data = _read(path)
        if (
            data.get("schema_version") == 1
            and data.get("profile") == "selected-databricks-apps"
            and data.get("profile_binding_sha256") == binding
            and data.get("source_sha256_after") == current
            and any(observed.get(test) == "passed" for test in tests)
        ):
            _proof_versions(root, data, tests)
            return True
    return False


def verify_platform(root: Path, profile: str | None, selected: PlatformEvidence) -> None:
    live, current = verify_live(root, profile, selected)
    manifest = _read(relative_file(root, MANIFEST))
    binding = live["profile_binding_sha256"]
    if (
        manifest.get("source_sha256") != current
        or manifest.get("profile_binding_sha256") != binding
    ):
        raise ValueError("selected_platform_manifest_missing_stale_or_wrong_profile")
    criteria, errors = ac_inventory(root)
    mapping, mapping_errors = primary_mapping((root / PLAN).read_text(), criteria)
    if errors or mapping_errors or not criteria.issuperset(CARRIER_CRITERIA):
        raise ValueError("governing_platform_acceptance_mapping_invalid")
    cases = manifest.get("cases", {})
    if not isinstance(cases, dict):
        raise ValueError("selected_platform_acceptance_cases_missing")
    required_live = {
        **PROFILE_CASE_IDS,
        "US-068-AC1": LIVE_CASE_IDS[0],
        "US-068-AC5": LIVE_CASE_IDS[0],
        "US-068-AC8": LIVE_CASE_IDS[1],
    }
    if set(required_live) != CARRIER_CRITERIA:
        raise ValueError("selected_platform_required_case_inventory_incomplete")
    for ac in sorted(CARRIER_CRITERIA):
        case = cases.get(ac)
        if case_errors(root, ac, case, mapping.get(ac, ""), current):
            raise ValueError("selected_platform_acceptance_unproven:" + ac)
        if not isinstance(case, dict) or not _bound_proof(root, case, current, binding):
            raise ValueError("selected_platform_acceptance_profile_not_bound:" + ac)
        required = {required_live[ac]}
        if ac == "US-068-AC3":
            required.add(PROFILE_CASE_IDS["US-068-AC6"])
        if not required.issubset(case["tests"]):
            raise ValueError("selected_platform_required_case_missing:" + ac)
