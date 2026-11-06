"""Source/profile-bound receipt assembly for selected live Apps cases."""

from __future__ import annotations

import platform as runtime_platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from qualify_live_apps_config import safe_origin
from qualify_live_apps_evidence import profile_binding_sha256
from web_artifacts import inventory, write_receipt


def browser_versions(
    path: Path,
    source: dict[str, str],
    *,
    profile: str,
    origin: str,
) -> dict[str, object]:
    from web_platform_receipt import PlatformEvidence, verify_live

    try:
        receipt, current = verify_live(
            Path(__file__).resolve().parents[1],
            profile,
            PlatformEvidence(receipt=path, origin=origin),
        )
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError("current_browser_receipt_required") from None
    versions = receipt.get("versions")
    shutdown = receipt.get("shutdown")
    app_runtime = shutdown.get("runtime") if isinstance(shutdown, dict) else None
    if current != source or not isinstance(versions, dict) or not isinstance(app_runtime, dict):
        raise RuntimeError("current_browser_receipt_required")
    result = dict(versions)
    result["app_runtime"] = app_runtime
    result["selected_profile_collector"] = {
        "python": runtime_platform.python_version(),
        "platform": runtime_platform.system(),
        "architecture": runtime_platform.machine(),
    }
    return result


def write_selected_receipt(
    root: Path,
    output: Path,
    source_before: dict[str, str],
    *,
    profile: str,
    origin: str,
    versions: dict[str, object],
    case_ids: dict[str, str],
    cases: list[tuple[str, float]],
    observations: dict[str, object] | None = None,
    credential_source: Literal["keychain-child-sdk", "injected-factory"] = "keychain-child-sdk",
) -> None:
    if not isinstance(credential_source, str) or credential_source not in {
        "keychain-child-sdk",
        "injected-factory",
    }:
        raise RuntimeError("selected_profile_credential_source_invalid")
    source_after = inventory(root)
    if source_before != source_after:
        raise RuntimeError("selected_profile_source_changed")
    unknown = {case for case, _ in cases} - set(case_ids)
    if unknown or not cases:
        raise RuntimeError("selected_profile_case_not_observed")
    receipt = {
        "status": "passed",
        "schema_version": 1,
        "profile": "selected-databricks-apps",
        "profile_binding_sha256": profile_binding_sha256(profile, safe_origin(origin)),
        "command": (
            "python scripts/qualify_selected_profiles.py --profile <selected-profile> "
            "--browser-receipt <live-receipt> --output <receipt>"
            if credential_source == "keychain-child-sdk"
            else "qualify_selected_profiles._execute(args, credential_factory=<explicit-factory>)"
        ),
        "credential_source": credential_source,
        "recorded_at": datetime.now(UTC).isoformat(),
        "source_sha256": source_before,
        "source_sha256_after": source_after,
        "versions": versions,
        "test_results": [
            {
                "node_id": case_ids[case],
                "outcome": "passed",
                "duration_seconds": max(0.001, duration),
            }
            for case, duration in cases
        ],
        "observations": observations or {},
    }
    write_receipt(output, receipt)


__all__ = ["browser_versions", "write_selected_receipt"]
