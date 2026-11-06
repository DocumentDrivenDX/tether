"""A three-case claim, stale profile or late report cannot qualify Apps."""

import asyncio
import copy
import json
import sys
from unittest.mock import AsyncMock

import pytest
import web_quality
import web_release_postcheck
from qualify_live_apps_evidence import LIVE_CASE_IDS, profile_binding_sha256
from qualify_selected_profiles import PROFILE_CASE_IDS
from test_acceptance_closure import fixture_project
from transport_fixture import shutdown_observation
from web_artifacts import PROJECTS, inventory
from web_closure import MANIFEST, PLAN, STP
from web_gate_receipt import STAGES
from web_platform_receipt import CARRIER_CRITERIA, PlatformEvidence, verify_live, verify_platform


def _write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def _runtime(root):
    distributions = dict.fromkeys(PROJECTS, "0.2.0.dev0")
    distributions.update(
        {
            "hypercorn": "0.18.0",
            "databricks-sdk": "0.158.0",
            "psycopg": "3.3.2",
            "psycopg-binary": "3.3.2",
        }
    )
    for name, directory in PROJECTS.items():
        path = root / directory / "pyproject.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[project]\nname = "{name}"\nversion = "0.2.0.dev0"\n')
    (root / "uv.lock").write_text(
        "\n".join(
            f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
            for name, version in distributions.items()
        )
    )
    _write(root, "package.json", {"packageManager": "npm@11.20.0"})
    _write(
        root,
        "package-lock.json",
        {"packages": {"node_modules/@playwright/test": {"version": "1.64.1"}}},
    )
    return {
        "python": "3.11.17",
        "platform": "Linux",
        "architecture": "x86_64",
        "distributions": distributions,
    }


def _authority(root):
    manifest, _ = fixture_project(root)
    story = root / "docs/helix/01-frame/user-stories/US-068-fabricated.md"
    story.write_text(
        "\n".join(f"- [ ] **{ac}** — Fabricated criterion" for ac in sorted(CARRIER_CRITERIA))
    )
    for name in (PLAN, STP):
        path = root / name
        suffix = [
            f"| FR-69 | {ac} | WP-08 |" if name == PLAN else f"| {ac} | Fabricated case |"
            for ac in sorted(CARRIER_CRITERIA - {"US-068-AC1"})
        ]
        path.write_text(path.read_text() + "\n" + "\n".join(suffix))
    source = root / "src/tether/fabricated.py"
    source.write_text(
        "# Unit fixture only\n" + "\n".join("# @covers " + ac for ac in CARRIER_CRITERIA)
    )
    live_source = root / "tests/ui/databricks-live.spec.ts"
    live_source.parent.mkdir(parents=True, exist_ok=True)
    live_source.write_text("// Unit fixture only; no live execution claim\n")
    for node in PROFILE_CASE_IDS.values():
        source = root / node.split("::", 1)[0]
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Unit fixture only; no selected-profile execution claim\n")
    return manifest


def _fixture(root):
    manifest = _authority(root)
    runtime = _runtime(root)
    shutdown = shutdown_observation(root, synthetic=True)
    shutdown["runtime"] = runtime
    current = inventory(root)
    binding = profile_binding_sha256("fabricated-caller", "https://fabricated.example.test")
    receipt = {
        "status": "passed",
        "schema_version": 1,
        "profile": "selected-databricks-apps",
        "command": "fabricated fixture only",
        "recorded_at": "unit fixture",
        "source_sha256": current,
        "source_sha256_after": current,
        "profile_binding_sha256": binding,
        "versions": {
            "python": "3.11.17",
            "platform": "Darwin",
            "architecture": "arm64",
            "node": "v22.22.2",
            "npm": "11.20.0",
            "playwright": "1.64.1",
        },
        "test_results": [{"node_id": node, "outcome": "passed"} for node in LIVE_CASE_IDS],
        "shutdown": shutdown,
    }
    selected = PlatformEvidence(
        _write(root, "live.json", receipt), "https://fabricated.example.test/"
    )
    public = "docs/helix/04-build/evidence/web-platform/live.json"
    _write(root, public, receipt)
    supplemental = copy.deepcopy(receipt)
    supplemental["credential_source"] = "keychain-child-sdk"
    supplemental["versions"].update(
        app_runtime=runtime,
        selected_profile_collector={
            "python": "3.11.17",
            "platform": "Darwin",
            "architecture": "arm64",
        },
    )
    supplemental["test_results"] = [
        {"node_id": node, "outcome": "passed"} for node in PROFILE_CASE_IDS.values()
    ]
    other = "docs/helix/04-build/evidence/web-platform/other.json"
    _write(root, other, supplemental)
    for ac in CARRIER_CRITERIA:
        node = LIVE_CASE_IDS[0] if ac in {"US-068-AC1", "US-068-AC5"} else LIVE_CASE_IDS[1]
        primary = ac in {"US-068-AC1", "US-068-AC5", "US-068-AC8"}
        manifest["cases"][ac] = {
            "primary_task": "WP-08",
            "implementation": ["src/tether/fabricated.py"],
            "tests": [node if primary else PROFILE_CASE_IDS[ac]],
            "receipts": [public if primary else other],
        }
    manifest["cases"]["US-068-AC3"]["tests"].append(PROFILE_CASE_IDS["US-068-AC6"])
    manifest.update(source_sha256=current, profile_binding_sha256=binding)
    _write(root, MANIFEST, manifest)
    original = root / "docs/helix/04-build/evidence/web-platform/fabricated.json"
    original_data = json.loads(original.read_text())
    original_data["source_sha256"] = current
    original.write_text(json.dumps(original_data))
    return selected, receipt


def test_all_ten_current_profile_proofs_verify_without_restarting(tmp_path, monkeypatch):
    selected, _ = _fixture(tmp_path)
    runner = AsyncMock()
    monkeypatch.setattr(web_quality, "command", runner)
    assert (
        asyncio.run(
            web_quality.simple_gate(
                tmp_path, "platform-check", "fabricated-caller", platform=selected
            )
        )
        == 0
    )
    runner.assert_not_awaited()


def test_three_interface_cases_do_not_substitute_for_all_carrier_criteria(tmp_path):
    selected, _ = _fixture(tmp_path)
    path = tmp_path / MANIFEST
    manifest = json.loads(path.read_text())
    del manifest["cases"]["US-068-AC2"]
    _write(tmp_path, MANIFEST, manifest)
    with pytest.raises(ValueError, match="acceptance_unproven:US-068-AC2"):
        verify_platform(tmp_path, "fabricated-caller", selected)


def test_unrelated_observed_profile_case_cannot_close_another_criterion(tmp_path):
    selected, _ = _fixture(tmp_path)
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    manifest["cases"]["US-068-AC2"]["tests"] = [PROFILE_CASE_IDS["US-068-AC4"]]
    _write(tmp_path, MANIFEST, manifest)
    with pytest.raises(ValueError, match="required_case_missing:US-068-AC2"):
        verify_platform(tmp_path, "fabricated-caller", selected)


def test_identity_expiry_cannot_be_omitted_from_authentication_acceptance(tmp_path):
    selected, _ = _fixture(tmp_path)
    manifest = json.loads((tmp_path / MANIFEST).read_text())
    manifest["cases"]["US-068-AC3"]["tests"] = [PROFILE_CASE_IDS["US-068-AC3"]]
    _write(tmp_path, MANIFEST, manifest)
    with pytest.raises(ValueError, match="required_case_missing:US-068-AC3"):
        verify_platform(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize("outcome", ["skipped", "xfailed", "failed"])
def test_failed_or_skipped_live_case_refuses(tmp_path, outcome):
    selected, receipt = _fixture(tmp_path)
    receipt["test_results"][0]["outcome"] = outcome
    _write(tmp_path, "live.json", receipt)
    with pytest.raises(ValueError, match="required_case_not_passed"):
        verify_live(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize(
    "case", ["source", "after", "binding", "missing", "duplicate", "node", "app"]
)
def test_stale_wrong_profile_incomplete_or_unqualified_receipt_refuses(tmp_path, case):
    selected, receipt = _fixture(tmp_path)
    if case in {"source", "after"}:
        receipt["source_sha256" + ("_after" if case == "after" else "")] = {}
    elif case == "binding":
        assert selected.origin is not None
        receipt["profile_binding_sha256"] = profile_binding_sha256(
            "another-caller", selected.origin
        )
    elif case == "missing":
        receipt["test_results"].pop()
    elif case == "duplicate":
        receipt["test_results"][1] = receipt["test_results"][0]
    elif case == "node":
        receipt["versions"]["node"] = "v22.20.0"
    else:
        receipt["shutdown"]["runtime"]["distributions"]["novirhiza-tether"] = "0.1.0"
    _write(tmp_path, "live.json", receipt)
    messages = {
        "source": "missing_failed_stale_or_wrong_profile",
        "after": "missing_failed_stale_or_wrong_profile",
        "binding": "missing_failed_stale_or_wrong_profile",
        "missing": "exact_cases_required",
        "duplicate": "exact_cases_required",
        "node": "driver_runtime_outside_qualified_cohort",
        "app": "app_packages_differ_from_locked_cohort",
    }
    with pytest.raises(ValueError, match=messages[case]):
        verify_live(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize(
    ("elapsed", "message"),
    [
        (True, "shutdown_measurement_invalid"),
        (float("nan"), "shutdown_measurement_invalid"),
        (float("inf"), "shutdown_measurement_invalid"),
        (14.01, "shutdown_exceeds_configured_or_platform_bound"),
        (15, "shutdown_exceeds_configured_or_platform_bound"),
    ],
)
def test_unmeasured_or_overdue_signal_report_never_qualifies(tmp_path, elapsed, message):
    selected, receipt = _fixture(tmp_path)
    receipt["shutdown"]["signal_to_report_seconds"] = elapsed
    _write(tmp_path, "live.json", receipt)
    with pytest.raises(ValueError, match=message):
        verify_live(tmp_path, "fabricated-caller", selected)


def test_local_receipt_cannot_close_live_criterion(tmp_path):
    selected, _ = _fixture(tmp_path)
    path = tmp_path / "docs/helix/04-build/evidence/web-platform/other.json"
    data = json.loads(path.read_text())
    del data["profile_binding_sha256"]
    _write(tmp_path, str(path.relative_to(tmp_path)), data)
    with pytest.raises(ValueError, match="profile_not_bound"):
        verify_platform(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize("field", ["source_sha256_after", "schema_version", "profile"])
def test_supplemental_profile_proof_needs_current_final_source_and_schema(tmp_path, field):
    selected, _ = _fixture(tmp_path)
    name = "docs/helix/04-build/evidence/web-platform/other.json"
    data = json.loads((tmp_path / name).read_text())
    del data[field]
    _write(tmp_path, name, data)
    with pytest.raises(ValueError, match="profile_not_bound"):
        verify_platform(tmp_path, "fabricated-caller", selected)


def test_supplemental_profile_proof_requires_qualified_runtime(tmp_path):
    selected, _ = _fixture(tmp_path)
    name = "docs/helix/04-build/evidence/web-platform/other.json"
    data = json.loads((tmp_path / name).read_text())
    data["versions"]["node"] = "v22.20.0"
    _write(tmp_path, name, data)
    with pytest.raises(ValueError, match="driver_runtime_outside_qualified_cohort"):
        verify_platform(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize("field", ["app_runtime", "selected_profile_collector"])
def test_profile_collector_and_app_versions_cannot_be_omitted(tmp_path, field):
    selected, _ = _fixture(tmp_path)
    name = "docs/helix/04-build/evidence/web-platform/other.json"
    data = json.loads((tmp_path / name).read_text())
    del data["versions"][field]
    _write(tmp_path, name, data)
    with pytest.raises(ValueError, match="runtime_missing_or_unqualified"):
        verify_platform(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize("source", [None, "unlisted"])
def test_profile_credential_source_is_explicit_and_closed(tmp_path, source):
    selected, _ = _fixture(tmp_path)
    name = "docs/helix/04-build/evidence/web-platform/other.json"
    data = json.loads((tmp_path / name).read_text())
    data["credential_source"] = source
    _write(tmp_path, name, data)
    with pytest.raises(ValueError, match="selected_profile_credential_source_required"):
        verify_platform(tmp_path, "fabricated-caller", selected)


def test_injected_credential_factory_keeps_same_selected_profile_case_requirements(tmp_path):
    selected, _ = _fixture(tmp_path)
    name = "docs/helix/04-build/evidence/web-platform/other.json"
    data = json.loads((tmp_path / name).read_text())
    data["credential_source"] = "injected-factory"
    _write(tmp_path, name, data)
    verify_platform(tmp_path, "fabricated-caller", selected)


def test_different_profile_manifest_cannot_reuse_selected_live_receipt(tmp_path):
    selected, _ = _fixture(tmp_path)
    data = json.loads((tmp_path / MANIFEST).read_text())
    data["profile_binding_sha256"] = "different-profile"
    _write(tmp_path, MANIFEST, data)
    with pytest.raises(ValueError, match="manifest_missing_stale_or_wrong_profile"):
        verify_platform(tmp_path, "fabricated-caller", selected)


@pytest.mark.parametrize("missing", ["profile", "origin", "receipt"])
def test_no_platform_inputs_are_inferred(tmp_path, missing):
    selected, _ = _fixture(tmp_path)
    profile = None if missing == "profile" else "fabricated-caller"
    evidence = PlatformEvidence(
        None if missing == "receipt" else selected.receipt,
        None if missing == "origin" else selected.origin,
    )
    with pytest.raises(ValueError, match="explicit_platform_profile_origin_and_receipt_required"):
        verify_live(tmp_path, profile, evidence)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("signal_name", "SIGINT", "physical_ownership_evidence_missing"),
        ("outstanding", [], "shutdown_observation_invalid"),
        ("asgi_http_versions", ["1.1"], "shutdown_ingress_observation_mismatch"),
    ],
)
def test_idle_or_different_transport_signal_cannot_substitute_for_held_worker(
    tmp_path, field, value, message
):
    selected, receipt = _fixture(tmp_path)
    receipt["shutdown"][field] = value
    _write(tmp_path, "live.json", receipt)
    with pytest.raises(ValueError, match=message):
        verify_live(tmp_path, "fabricated-caller", selected)


def test_postcheck_cannot_bypass_selected_platform_proofs(tmp_path, monkeypatch):
    selected, live = _fixture(tmp_path)
    aggregate = {
        "status": "passed",
        "source_sha256": live["source_sha256"],
        "command": "unit fixture only",
        "recorded_at": "unit fixture",
        "versions": live["versions"],
        "stages": list(STAGES),
        "test_results": [
            {"node_id": "scripts/web_quality.py::" + stage, "outcome": "passed"} for stage in STAGES
        ],
    }
    receipt = _write(tmp_path, "aggregate.json", aggregate)
    monkeypatch.chdir(tmp_path)
    arguments = ["postcheck", "--aggregate", str(receipt)]
    monkeypatch.setattr(sys, "argv", arguments)
    assert web_release_postcheck.main() == 1
    assert selected.origin is not None
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *arguments,
            "--profile",
            "fabricated-caller",
            "--app-origin",
            selected.origin,
            "--live-receipt",
            str(selected.receipt),
        ],
    )
    assert web_release_postcheck.main() == 0
