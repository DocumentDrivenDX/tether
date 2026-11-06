"""Focused safety checks for selected-profile live qualification helpers."""

from __future__ import annotations

import pytest
from databricks.sdk.service.iam import Patch, PatchOp, PatchSchema
from qualify_selected_profiles import (
    PROFILE_CASE_IDS,
    _missing_schema_refusal_code,
    _validate_case_dependencies,
    log_privacy,
)
from selected_profiles_bundle import deployed_bundle_matches_lock
from selected_profiles_evidence import browser_versions
from selected_profiles_expiry import _is_expired_database_credential_error
from selected_profiles_identity import _await_retained, _ordered_disable_check_restore
from selected_profiles_logs import _clean_environment
from selected_profiles_pg_oracle import OracleResult
from selected_profiles_process import _runtime_path

from tether.client import IntegrityError


@pytest.mark.parametrize("sqlstate", ["28000", "28P01"])
def test_expired_database_credential_requires_authentication_sqlstate(sqlstate: str) -> None:
    assert _is_expired_database_credential_error(OracleResult("auth_refused", sqlstate=sqlstate))


@pytest.mark.parametrize("sqlstate", [None, "08006", "3F000", "42601"])
def test_expiry_rejects_transport_and_non_authentication_failures(sqlstate: str | None) -> None:
    result = OracleResult("other_failure", sqlstate=sqlstate)
    assert not _is_expired_database_credential_error(result)


def test_missing_schema_accepts_only_typed_profile_mismatch_or_schema_sqlstate() -> None:
    assert _missing_schema_refusal_code(IntegrityError("database_schema_mismatch")) == (
        "database_schema_mismatch"
    )

    class SchemaMissing(Exception):
        sqlstate = "3F000"

    class OtherIntegrity(IntegrityError):
        pass

    assert _missing_schema_refusal_code(SchemaMissing()) == "3F000"
    assert _missing_schema_refusal_code(OtherIntegrity("different_failure")) is None
    assert _missing_schema_refusal_code(ConnectionError("offline")) is None


def test_revocation_case_requires_the_genuine_expiry_control() -> None:
    with pytest.raises(RuntimeError, match="revocation_evidence_requires_genuine_expiry_case"):
        _validate_case_dependencies(["US-068-AC3"])
    _validate_case_dependencies(["US-068-AC3", "US-068-AC6"])


def test_privacy_evidence_requires_both_nonempty_log_streams() -> None:
    from selected_profile_log_fixtures import privacy_logs

    app, platform = privacy_logs()
    for captures in (("", platform), (app, " \n")):
        with pytest.raises(RuntimeError, match="platform_log_stream_empty"):
            log_privacy(
                *captures,
                ("fabricated-content",),
                "fabricated-credential",
                "invocation-123",
                success_session_id="privacy-session",
                rejected_session_id="privacy-refused",
                api_prefix="/api",
            )


def test_privacy_evidence_checks_both_streams_for_all_markers() -> None:
    from selected_profile_log_fixtures import privacy_logs

    markers = ("fabricated-content", "fabricated-rejected-content")
    app, platform = privacy_logs()

    def validate(app: str, platform: str) -> None:
        log_privacy(
            app,
            platform,
            markers,
            "fabricated-credential",
            "invocation-123",
            success_session_id="privacy-session",
            rejected_session_id="privacy-refused",
            api_prefix="/api",
        )

    validate(app, platform)
    for captures in (
        (app + markers[0], platform),
        (app, platform + "fabricated-credential"),
    ):
        with pytest.raises(RuntimeError, match="privacy_marker_found_in_platform_logs"):
            validate(*captures)


def test_privacy_evidence_requires_matching_fresh_worker_marker() -> None:
    from selected_profile_log_fixtures import privacy_logs

    app, platform = privacy_logs("old-invocation")
    with pytest.raises(RuntimeError, match="privacy_app_window_not_anchored"):
        log_privacy(
            app,
            platform,
            ("fabricated-content",),
            "fabricated-credential",
            "current-invocation",
            success_session_id="privacy-session",
            rejected_session_id="privacy-refused",
            api_prefix="/api",
        )


def test_live_profile_case_ids_name_proving_functions() -> None:
    assert PROFILE_CASE_IDS["US-068-AC2"] == "scripts/selected_profiles_owner.py::owner_isolation"
    assert (
        PROFILE_CASE_IDS["US-068-AC3"]
        == "scripts/selected_profiles_identity.py::revocation_and_refusal"
    )
    assert (
        PROFILE_CASE_IDS["US-068-AC4"]
        == "scripts/selected_profiles_identity.py::principal_separation"
    )
    assert (
        PROFILE_CASE_IDS["US-068-AC6"] == "scripts/selected_profiles_expiry.py::credential_rotation"
    )
    assert (
        PROFILE_CASE_IDS["US-068-AC7"]
        == "scripts/selected_profiles_example_runtime.py::selected_example_schema_refusal"
    )


def test_selected_receipt_uses_validated_browser_and_app_runtime(monkeypatch, tmp_path) -> None:
    import json

    import web_platform_receipt

    source = {"scripts/qualify_selected_profiles.py": "a" * 64}
    browser = {
        "versions": {"python": "3.11.0", "playwright": "1.0.0"},
        "shutdown": {"runtime": {"python": "3.11.0", "distributions": {}}},
    }

    def verify_live(root, profile, evidence):
        assert evidence.origin == "https://example.test"
        return browser, source

    monkeypatch.setattr(web_platform_receipt, "verify_live", verify_live)
    path = tmp_path / "browser.json"
    path.write_text(json.dumps({"fixture": True}))
    versions = browser_versions(
        path,
        source,
        profile="fabricated-profile",
        origin="https://example.test",
    )
    assert versions["python"] == "3.11.0"
    assert versions["app_runtime"] == browser["shutdown"]["runtime"]
    collector = versions["selected_profile_collector"]
    assert isinstance(collector, dict)
    assert set(collector) == {
        "python",
        "platform",
        "architecture",
    }


def test_scim_revocation_patch_keeps_false_boolean_in_sdk_request() -> None:
    request = {
        "Operations": [Patch(op=PatchOp.REPLACE, value={"active": False}).as_dict()],
        "schemas": [PatchSchema.URN_IETF_PARAMS_SCIM_API_MESSAGES_2_0_PATCH_OP.value],
    }
    assert request["Operations"] == [{"op": "replace", "value": {"active": False}}]


def test_log_cli_prefix_starts_with_empty_profile_environment() -> None:
    assert _clean_environment("/fabricated/home") == (
        "/usr/bin/env",
        "-i",
        "HOME=/fabricated/home",
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
    )


def test_clean_runtime_path_retains_selected_tool_directories(tmp_path) -> None:
    node = tmp_path / "node-tools" / "node"
    databricks = tmp_path / "cli-tools" / "databricks"
    node.parent.mkdir()
    databricks.parent.mkdir()
    node.write_text("fabricated node executable")
    databricks.write_text("fabricated CLI executable")
    path = _runtime_path((str(node), None, str(databricks)))
    directories = path.split(":")
    assert directories.index(str(node.parent)) < directories.index(str(databricks.parent))
    assert "/usr/bin" in path.split(":")
    assert "/bin" in path.split(":")


def test_clean_runtime_path_preserves_selected_symlink_entrypoint(tmp_path) -> None:
    import os

    entrypoint = tmp_path / "homebrew" / "bin" / "npm"
    target = tmp_path / "npm-package" / "bin" / "npm-cli.js"
    entrypoint.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.write_text("fabricated npm entrypoint")
    os.symlink(target, entrypoint)
    path = _runtime_path((str(entrypoint),))
    directories = path.split(":")
    assert str(entrypoint.parent) in directories
    assert str(target.parent) not in directories


def test_cancelled_revocation_waits_for_physical_disable_before_restore() -> None:
    import asyncio
    import threading

    async def run() -> None:
        disable_started = threading.Event()
        release_disable = threading.Event()
        sequence: list[str] = []

        def physical_disable() -> None:
            disable_started.set()
            if not release_disable.wait(timeout=5):
                raise TimeoutError("test_disable_release_timeout")
            sequence.append("disable-finished")

        async def disable() -> None:
            await asyncio.to_thread(physical_disable)

        async def check() -> None:
            sequence.append("check")

        async def restore() -> str:
            sequence.append("restore")
            return "restored"

        operation = asyncio.create_task(_ordered_disable_check_restore(disable, check, restore))
        waiter = asyncio.create_task(_await_retained(operation))
        assert await asyncio.to_thread(disable_started.wait, 2)
        waiter.cancel()
        await asyncio.sleep(0.05)
        waiter.cancel()
        await asyncio.sleep(0.05)
        assert sequence == []
        release_disable.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert sequence == ["disable-finished", "check", "restore"]

    asyncio.run(run())


def test_deployed_bundle_checker_compares_exported_source_bytes(tmp_path) -> None:
    import asyncio
    import base64
    import io
    import json
    import uuid
    import zipfile
    from types import SimpleNamespace

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.yaml").write_text("command: locked\n")
    source_path = "/Workspace/qualification-source"
    snapshot_path = "/Workspace/.snapshots/qualification-123"
    deployment_id = "qualification-deployment-123"
    reference = tmp_path / "deployment.json"
    reference.write_text(
        json.dumps({"source_code_path": source_path, "deployment_id": deployment_id})
    )
    output = io.BytesIO()
    snapshot_root = "0123456789abcdef0123456789abcdef"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(snapshot_root + "/app.yaml", (bundle / "app.yaml").read_bytes())
        archive.writestr(
            "manifest.mf",
            json.dumps(
                {
                    "guid": str(uuid.uuid4()),
                    "name": "manifest.mf",
                    "origId": 0,
                    "version": "1.0",
                }
            ),
        )

    class Workspace:
        def __init__(self) -> None:
            self.workspace = self
            self.apps = self

        def get_deployment(self, name: str, requested_id: str) -> SimpleNamespace:
            assert name == "qualification-app"
            assert requested_id == deployment_id
            return SimpleNamespace(
                deployment_id=deployment_id,
                source_code_path=source_path,
                status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
                deployment_artifacts=SimpleNamespace(source_code_path=snapshot_path),
            )

        def export(self, path: str, *, format: object) -> SimpleNamespace:
            if path != snapshot_path:
                raise AssertionError("unexpected_deployment_snapshot")
            return SimpleNamespace(content=base64.b64encode(output.getvalue()).decode("ascii"))

    asyncio.run(
        deployed_bundle_matches_lock(
            Workspace(),
            SimpleNamespace(
                active_deployment=SimpleNamespace(
                    deployment_id=deployment_id, source_code_path=source_path
                ),
            ),
            "qualification-app",
            bundle,
            reference,
        )
    )
    (bundle / "app.yaml").write_text("command: changed\n")
    with pytest.raises(RuntimeError, match="deployment_source_bytes_mismatch"):
        asyncio.run(
            deployed_bundle_matches_lock(
                Workspace(),
                SimpleNamespace(
                    active_deployment=SimpleNamespace(
                        deployment_id=deployment_id, source_code_path=source_path
                    ),
                ),
                "qualification-app",
                bundle,
                reference,
            )
        )


def test_deployed_bundle_checker_rejects_extra_exported_files(tmp_path) -> None:
    import asyncio
    import base64
    import io
    import json
    import uuid
    import zipfile
    from types import SimpleNamespace

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.yaml").write_text("command: locked\n")
    source_path = "/Workspace/qualification-source"
    snapshot_path = "/Workspace/.snapshots/qualification-123"
    deployment_id = "qualification-deployment-123"
    reference = tmp_path / "deployment.json"
    reference.write_text(
        json.dumps({"source_code_path": source_path, "deployment_id": deployment_id})
    )
    output = io.BytesIO()
    snapshot_root = "0123456789abcdef0123456789abcdef"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(snapshot_root + "/app.yaml", (bundle / "app.yaml").read_bytes())
        archive.writestr(snapshot_root + "/unexpected.txt", "extra")
        archive.writestr(
            "manifest.mf",
            json.dumps(
                {
                    "guid": str(uuid.uuid4()),
                    "name": "manifest.mf",
                    "origId": 0,
                    "version": "1.0",
                }
            ),
        )

    class Workspace:
        def __init__(self) -> None:
            self.workspace = self
            self.apps = self

        def get_deployment(self, name: str, requested_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                deployment_id=deployment_id,
                source_code_path=source_path,
                status=SimpleNamespace(state=SimpleNamespace(value="SUCCEEDED")),
                deployment_artifacts=SimpleNamespace(source_code_path=snapshot_path),
            )

        def export(self, path: str, *, format: object) -> SimpleNamespace:
            assert path == snapshot_path
            return SimpleNamespace(content=base64.b64encode(output.getvalue()).decode("ascii"))

    with pytest.raises(RuntimeError, match="deployment_source_bytes_mismatch"):
        asyncio.run(
            deployed_bundle_matches_lock(
                Workspace(),
                SimpleNamespace(
                    active_deployment=SimpleNamespace(
                        deployment_id=deployment_id, source_code_path=source_path
                    ),
                ),
                "qualification-app",
                bundle,
                reference,
            )
        )


def test_deployed_bundle_checker_requires_recorded_successful_deployment(tmp_path) -> None:
    import asyncio
    import json
    from types import SimpleNamespace

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.yaml").write_text("command: locked\n")
    source_path = "/Workspace/qualification-source"
    reference = tmp_path / "deployment.json"
    reference.write_text(
        json.dumps({"source_code_path": source_path, "deployment_id": "expected-id"})
    )

    class Workspace:
        def __init__(self) -> None:
            self.apps = self

        def get_deployment(self, name: str, requested_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                deployment_id="different-id",
                source_code_path=source_path,
                status=SimpleNamespace(state=SimpleNamespace(value="IN_PROGRESS")),
                deployment_artifacts=SimpleNamespace(source_code_path="snapshot"),
            )

    with pytest.raises(RuntimeError, match="deployed_snapshot_unavailable"):
        asyncio.run(
            deployed_bundle_matches_lock(
                Workspace(),
                SimpleNamespace(
                    active_deployment=SimpleNamespace(
                        deployment_id="expected-id", source_code_path=source_path
                    ),
                ),
                "qualification-app",
                bundle,
                reference,
            )
        )


def test_deployed_bundle_checker_rejects_historical_deployment(tmp_path) -> None:
    import asyncio
    import json
    from types import SimpleNamespace

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    reference = tmp_path / "deployment.json"
    reference.write_text(
        json.dumps(
            {
                "source_code_path": "/Workspace/qualification-source",
                "deployment_id": "historical-id",
            }
        )
    )
    app = SimpleNamespace(
        active_deployment=SimpleNamespace(
            deployment_id="current-id", source_code_path="/Workspace/qualification-source"
        ),
    )
    with pytest.raises(RuntimeError, match="deployment_source_binding_mismatch"):
        asyncio.run(
            deployed_bundle_matches_lock(object(), app, "qualification-app", bundle, reference)
        )
