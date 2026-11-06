"""Collect source-bound selected-profile checks for Databricks Apps."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from databricks.sdk import WorkspaceClient
from qualify_live_apps_config import safe_origin
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from qualify_web_platform import profile_from_environment
from selected_profiles_evidence import browser_versions, write_selected_receipt
from selected_profiles_identity_credentials import workspace_for_bearer
from selected_profiles_identity_probe import IdentityObservationIncomplete
from selected_profiles_privacy_types import PrivacyObservationIncomplete, PrivacyRejection
from selected_profiles_process import exec_with_profile_environment
from selected_schema_failure import missing_schema_refusal_code
from web_artifacts import inventory

if TYPE_CHECKING:
    from selected_profiles_identity_credentials import CredentialFactory, FreshCredential

ROOT = Path(__file__).resolve().parents[1]
PROFILE_CASE_IDS = {
    "US-068-AC2": "scripts/selected_profiles_owner.py::owner_isolation",
    "US-068-AC3": "scripts/selected_profiles_identity.py::revocation_and_refusal",
    "US-068-AC4": "scripts/selected_profiles_identity.py::principal_separation",
    "US-068-AC6": "scripts/selected_profiles_expiry.py::credential_rotation",
    "US-068-AC7": "scripts/selected_profiles_example_runtime.py::selected_example_schema_refusal",
    "US-068-AC9": "scripts/selected_profiles_bundle.py::deployed_bundle_matches_lock",
    "US-068-AC10": "scripts/qualify_selected_profiles.py::log_privacy",
}


def _missing_schema_refusal_code(error: BaseException) -> str | None:
    return missing_schema_refusal_code(error)


def _validate_case_dependencies(cases: list[str]) -> None:
    if "US-068-AC3" in cases and "US-068-AC6" not in cases:
        raise RuntimeError("revocation_evidence_requires_genuine_expiry_case")


def _security_secret(service: str, account: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed Keychain binary and operation
        ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.rstrip("\n")


def _service_workspace(config_file: str, reference_file: str, profile_name: str) -> WorkspaceClient:
    reference = json.loads(Path(reference_file).read_text(encoding="utf-8"))
    return WorkspaceClient(
        profile=profile_name,
        config_file=config_file,
        client_secret=_security_secret(reference["service"], reference["account"]),
        auth_type="oauth-m2m",
    )


def _authorization(workspace: WorkspaceClient) -> str:
    headers = dict(workspace.config.authenticate())
    value = headers.get("Authorization") or headers.get("authorization")
    if not isinstance(value, str) or not value.startswith("Bearer "):
        raise RuntimeError("selected_profile_credential_missing")
    return value


class _StaticCredential:
    def __init__(self, authorization: str) -> None:
        self._authorization = authorization

    async def authorization(self) -> str:
        return self._authorization


async def missing_schema_refusal(
    *, workspace: WorkspaceClient, settings: Any, schema: str, infra_role: str
) -> None:
    """Low-level source control; the selected-profile pass uses the full example runtime."""
    from tether_databricks_example.postgres import LakebaseConnectionSource, LakebaseSettings

    from tether.client import Actor
    from tether.client.postgres import PostgresDurabilityProfile, PostgresResources

    identity, authorization = await asyncio.gather(
        asyncio.to_thread(workspace.current_user.me),
        asyncio.to_thread(_authorization, workspace),
    )
    if not isinstance(identity.user_name, str) or not identity.user_name:
        raise RuntimeError("database_caller_identity_unavailable")
    source = LakebaseConnectionSource(
        LakebaseSettings(
            workspace_host=workspace.config.host,
            endpoint=settings.pg_endpoint,
            host=settings.pg_host,
            database=settings.pg_database,
            infra_role=infra_role,
            sslrootcert=settings.ca_file,
        ),
        database_user=lambda actor: actor.id,
        infrastructure_client=workspace,
        user_client_factory=workspace_for_bearer,
    )
    actor = Actor(id=identity.user_name, credential=_StaticCredential(authorization))
    async with source(actor, None) as connection:
        baseline = await connection.execute("SELECT 1")
        if await baseline.fetchone() != (1,):
            raise RuntimeError("valid_database_baseline_failed")
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,)
        )
        before = await cursor.fetchone()
    if before is None or before[0] != 0:
        raise RuntimeError("missing_schema_probe_not_isolated")
    try:
        await PostgresResources.open(
            source,
            actor=actor,
            schema=schema,
            durability_profile=PostgresDurabilityProfile(
                "databricks-lakebase-pg18", endpoint_id=settings.pg_endpoint
            ),
        )
    except Exception as error:
        if _missing_schema_refusal_code(error) is None:
            raise RuntimeError("missing_schema_refusal_unclassified") from None
    else:
        raise RuntimeError("missing_schema_open_unexpectedly_succeeded")
    async with source(actor, None) as connection:
        cursor = await connection.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname = %s", (schema,)
        )
        after = await cursor.fetchone()
    if after != before:
        raise RuntimeError("missing_schema_was_created")


def locked_bundle(bundle: Path) -> None:
    """@covers US-068-AC9: the deployed bundle is self-contained and has no Node runtime."""
    from web_artifacts import archive_contents, wheel_metadata

    required = {"app.yaml", "pyproject.toml", "uv.lock", "databricks.yml", "qualification-ca.pem"}
    if not bundle.is_dir() or not required <= {path.name for path in bundle.iterdir()}:
        raise RuntimeError("deployment_bundle_incomplete")
    if (bundle / "requirements.txt").exists() or (bundle / "node_modules").exists():
        raise RuntimeError("deployment_bundle_has_unlocked_runtime")
    command = (bundle / "app.yaml").read_text(encoding="utf-8")
    if '"uv"' not in command or '"--locked"' not in command or "node" in command.lower():
        raise RuntimeError("deployment_command_not_locked_python")
    lock = tomllib.loads((bundle / "uv.lock").read_text(encoding="utf-8"))
    locked = {item["name"].lower().replace("_", "-"): item["version"] for item in lock["package"]}
    wheels = tuple((bundle / "wheelhouse").glob("*.whl"))
    metadata: dict[str, Path] = {}
    for path in wheels:
        name = wheel_metadata(path)["name"]
        if not isinstance(name, str):
            raise RuntimeError("deployment_wheel_metadata_invalid")
        metadata[name.lower().replace("_", "-")] = path
    for name in (
        "novirhiza-tether",
        "novirhiza-tether-web-api",
        "novirhiza-tether-web-ui",
        "novirhiza-tether-databricks",
    ):
        wheel = metadata.get(name)
        if wheel is None or wheel_metadata(wheel)["version"] != locked.get(name):
            raise RuntimeError("deployment_wheel_lock_mismatch")
    ui_wheel = metadata["novirhiza-tether-web-ui"]
    assets = archive_contents(ui_wheel)
    if not any(path.endswith(".js") for path in assets) or not any(
        path.endswith(".css") for path in assets
    ):
        raise RuntimeError("deployment_bundle_static_assets_missing")
    if any(path.name in {"node", "node.exe", "npm", "npm.cmd"} for path in bundle.rglob("*")):
        raise RuntimeError("deployment_bundle_node_runtime_present")


def log_privacy(
    app_logs: str,
    platform_logs: str,
    content_markers: tuple[str, ...],
    credential_marker: str,
    invocation_id: str,
    *,
    success_session_id: str,
    rejected_session_id: str,
    api_prefix: str,
    diagnostics: tuple[str, ...] = (),
) -> None:
    """@covers US-068-AC10: actual APP/platform captures exclude request markers."""
    from selected_profiles_privacy import validate_privacy

    validate_privacy(
        app_logs,
        platform_logs,
        content_markers,
        credential_marker,
        invocation_id,
        success_session_id=success_session_id,
        rejected_session_id=rejected_session_id,
        api_prefix=api_prefix,
        diagnostics=diagnostics,
    )


async def exercise_privacy_route(origin: str, prefix: str, authorization: str) -> PrivacyRejection:
    from selected_profiles_privacy import rejected_request

    return await rejected_request(origin, prefix, authorization)


async def _run_cases(
    args: argparse.Namespace,
    *,
    origin: str,
    prefix: str,
    settings: Any,
    values: dict[str, str],
    app_name: str,
    app_principal_id: str,
    app: Any,
    human_workspace: WorkspaceClient,
    service_workspace: WorkspaceClient,
    credential_factory: CredentialFactory,
) -> tuple[list[tuple[str, float]], dict[str, object]]:
    observed: list[tuple[str, float]] = []
    observations: dict[str, object] = {}
    for case in args.case:
        started = time.monotonic()
        if case == "US-068-AC2":
            from selected_profiles_owner import owner_isolation

            await owner_isolation(
                origin,
                prefix,
                settings,
                values.get("TETHER_SERVICE_CALLER_DATABASE_ROLE", ""),
                human_workspace,
                service_workspace,
            )
        elif case == "US-068-AC3":
            from selected_profiles_identity import revocation_and_refusal

            identity_admin = None
            if reference := getattr(args, "identity_admin_reference", None):
                from selected_profiles_identity_admin import identity_admin_from_reference

                identity_admin = await identity_admin_from_reference(
                    reference,
                    primary_workspace=human_workspace,
                    identity_reference=args.identity_reference,
                    app_principal_id=app_principal_id,
                )
            revocation = await revocation_and_refusal(
                origin,
                prefix,
                admin_workspace=human_workspace,
                service_workspace=service_workspace,
                identity_reference=args.identity_reference,
                app_principal_id=app_principal_id,
                app_name=app_name,
                identity_admin=identity_admin,
                refresh_credential=credential_factory,
            )
            observations[case] = {
                "administration_mode": "account-scim-azure-cli" if reference else "workspace-scim",
                "authority_scope": "account" if reference else "workspace",
                "target_ownership_verified": True,
                "application_caller_profiles_unchanged": True,
                **revocation,
            }
        elif case == "US-068-AC4":
            from selected_profiles_identity import principal_separation

            observations[case] = await principal_separation(
                human_workspace,
                service_workspace,
                app_principal_id,
                values.get("TETHER_PG_INFRA_ROLE", ""),
                origin,
                prefix,
                settings,
                values.get("TETHER_SERVICE_CALLER_DATABASE_ROLE", ""),
            )
        elif case == "US-068-AC7":
            from selected_profiles_example_runtime import selected_example_schema_refusal

            await selected_example_schema_refusal(
                workspace=service_workspace,
                values=values,
                selected_schema=values.get("TETHER_PG_SCHEMA", ""),
                bundle=args.bundle,
            )
        elif case == "US-068-AC9":
            locked_bundle(args.bundle)
            from selected_profiles_bundle import deployed_bundle_matches_lock

            await deployed_bundle_matches_lock(
                human_workspace,
                app,
                app_name,
                args.bundle,
                args.deployment_reference_file,
            )
        elif case == "US-068-AC10":
            from selected_profiles_logs import capture_privacy_window
            from selected_profiles_owner import successful_content_turn

            authorization = await asyncio.to_thread(_authorization, human_workspace)
            window = await capture_privacy_window(
                args.profile,
                app_name,
                lambda: successful_content_turn(origin, prefix, human_workspace),
                lambda authorization=authorization: exercise_privacy_route(
                    origin, prefix, authorization
                ),
                api_prefix=prefix,
            )
            log_privacy(
                window.app_logs,
                window.platform_logs,
                (window.success.content_marker, window.rejected.content_marker),
                window.rejected.credential_marker,
                window.success.invocation_id,
                success_session_id=window.success.session_id,
                rejected_session_id=window.rejected.session_id,
                api_prefix=prefix,
                diagnostics=window.diagnostics,
            )
        elif case == "US-068-AC6":
            from selected_profiles_expiry import credential_rotation

            observations[case] = await credential_rotation(
                service_workspace,
                settings,
                origin,
                prefix,
                refresh_credential=credential_factory,
            )
        observed.append((case, time.monotonic() - started))
    return observed, observations


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Primary caller profile anchor")
    parser.add_argument("--service-profile", required=True)
    parser.add_argument("--service-config-file", required=True)
    parser.add_argument("--service-secret-reference", required=True)
    parser.add_argument("--settings-file", type=Path, required=True)
    parser.add_argument("--app-reference-file", type=Path, required=True)
    parser.add_argument("--identity-reference", type=Path, required=True)
    parser.add_argument("--identity-admin-reference", type=Path)
    parser.add_argument("--deployment-reference-file", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-receipt", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=tuple(PROFILE_CASE_IDS), required=True)
    return parser.parse_args()


def _execute(
    args: argparse.Namespace, *, credential_factory: CredentialFactory | None = None
) -> None:
    if args.output.exists():
        raise RuntimeError("selected_profile_receipt_already_exists")
    _validate_case_dependencies(args.case)
    source_before = inventory(ROOT)
    values = json.loads(args.settings_file.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()
    ):
        raise RuntimeError("qualification_settings_invalid")
    settings = profile_from_environment(values)
    origin = safe_origin(settings.url)
    versions = browser_versions(
        args.browser_receipt,
        source_before,
        profile=args.profile,
        origin=origin,
    )
    prefix = settings.api_prefix
    from selected_profiles_identity_credentials import (
        refresh_workspace_credential,
        workspace_from_credential,
    )

    human_workspace = WorkspaceClient(profile=args.profile)
    factory = (
        credential_factory
        if credential_factory is not None
        else lambda: refresh_workspace_credential(
            args.service_profile,
            args.service_config_file,
            args.service_secret_reference,
            human_workspace.config.host,
        )
    )

    async def acquire_credential() -> FreshCredential:
        return await factory()

    credential = asyncio.run(acquire_credential())
    if credential.host != human_workspace.config.host:
        raise RuntimeError("qualification_service_workspace_host_mismatch")
    service_workspace = workspace_from_credential(credential)
    app_reference = json.loads(args.app_reference_file.read_text(encoding="utf-8"))
    app_name = app_reference.get("name")
    if not isinstance(app_name, str) or not app_name:
        raise RuntimeError("qualification_app_reference_invalid")
    app = human_workspace.apps.get(app_name)
    app_principal_id = getattr(app, "service_principal_client_id", None)
    if not isinstance(app_principal_id, str) or not app_principal_id:
        raise RuntimeError("qualification_app_principal_unavailable")
    observed, observations = asyncio.run(
        _run_cases(
            args,
            origin=origin,
            prefix=prefix,
            settings=settings,
            values=values,
            app_name=app_name,
            app_principal_id=app_principal_id,
            app=app,
            human_workspace=human_workspace,
            service_workspace=service_workspace,
            credential_factory=factory,
        )
    )
    write_selected_receipt(
        ROOT,
        args.output,
        source_before,
        profile=args.profile,
        origin=origin,
        versions=versions,
        case_ids=PROFILE_CASE_IDS,
        cases=observed,
        observations=observations,
        credential_source="keychain-child-sdk"
        if credential_factory is None
        else "injected-factory",
    )
    print("selected_profile_cases_passed=" + ",".join(case for case, _ in observed))


def main() -> int:
    exec_with_profile_environment(
        Path(__file__), sys.argv, ROOT / "examples" / "databricks-app" / "src"
    )
    args = _parse_args()
    try:
        _execute(args)
        return 0
    except (
        IdentityObservationIncomplete,
        PrivacyObservationIncomplete,
        LiveQualificationIncomplete,
    ) as error:
        print("selected_profile_check_incomplete=" + error.reason, file=sys.stderr)
        return 2
    except Exception as error:
        safe_errors = {
            "selected_profile_receipt_already_exists",
            "qualification_settings_invalid",
            "qualification_app_reference_invalid",
            "qualification_app_principal_unavailable",
            "selected_profile_source_changed",
            "selected_profile_case_not_observed",
        }
        detail = (
            str(error)
            if type(error) is RuntimeError and str(error) in safe_errors
            else type(error).__name__
        )
        print("selected_profile_check_failed=" + detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
