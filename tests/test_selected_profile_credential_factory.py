"""One explicit credential factory governs initial, restore and expiry acquisition."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import qualify_selected_profiles as qualifier
import selected_profiles_evidence as evidence
import selected_profiles_expiry as expiry
import selected_profiles_identity as identity
import selected_profiles_identity_credentials as credentials

_HOST = "https://workspace.example.test"


@pytest.fixture
def collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    values = tmp_path / "settings.json"
    values.write_text("{}")
    app = tmp_path / "app.json"
    app.write_text(json.dumps({"name": "fabricated-app"}))
    output = tmp_path / "receipt.json"
    args = argparse.Namespace(
        output=output,
        case=["US-068-AC3", "US-068-AC6"],
        settings_file=values,
        profile="fabricated-human",
        service_profile="fabricated-service",
        service_config_file="fabricated-config",
        service_secret_reference="-".join(("fabricated", "reference")),
        identity_reference=tmp_path / "identity.json",
        app_reference_file=app,
        deployment_reference_file=tmp_path / "deployment.json",
        bundle=tmp_path,
        browser_receipt=tmp_path / "browser.json",
    )
    monkeypatch.setattr(qualifier, "inventory", lambda root: {"source.py": "digest"})
    monkeypatch.setattr(
        qualifier,
        "profile_from_environment",
        lambda values: SimpleNamespace(url="https://apps.example.test", api_prefix="/api"),
    )
    monkeypatch.setattr(qualifier, "browser_versions", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        qualifier,
        "WorkspaceClient",
        lambda **kwargs: SimpleNamespace(
            config=SimpleNamespace(host=_HOST),
            apps=SimpleNamespace(
                get=lambda name: SimpleNamespace(service_principal_client_id="fabricated-app")
            ),
        ),
    )
    monkeypatch.setattr(
        credentials,
        "workspace_from_credential",
        lambda fresh: SimpleNamespace(config=SimpleNamespace(host=fresh.host)),
    )
    default = AsyncMock(side_effect=AssertionError("default_credential_lookup_must_not_run"))
    monkeypatch.setattr(credentials, "refresh_workspace_credential", default)
    written = []
    monkeypatch.setattr(
        qualifier, "write_selected_receipt", lambda *args, **kwargs: written.append(kwargs)
    )
    return args, default, written


def test_same_injected_factory_is_used_for_initial_restore_and_both_expiry_controls(
    collector, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, default, written = collector
    fresh = credentials.FreshCredential(_HOST, " ".join(("Bearer", "fabricated")))
    factory = AsyncMock(return_value=fresh)
    phases = []

    async def revoke(*args, refresh_credential, **kwargs):
        assert refresh_credential is factory
        assert await refresh_credential() is fresh
        phases.append("restore")
        return {"revocation_token_unexpired_at_denial": True}

    async def rotate(*args, refresh_credential, **kwargs):
        assert refresh_credential is factory
        assert await refresh_credential() is fresh
        assert await refresh_credential() is fresh
        phases.append("expiry-positive-controls")
        return {"genuine_expiry_observed": True}

    monkeypatch.setattr(identity, "revocation_and_refusal", revoke)
    monkeypatch.setattr(expiry, "credential_rotation", rotate)
    qualifier._execute(args, credential_factory=factory)
    assert factory.await_count == 4
    assert phases == ["restore", "expiry-positive-controls"]
    default.assert_not_called()
    assert len(written) == 1
    assert written[0]["credential_source"] == "injected-factory"
    assert [case for case, _ in written[0]["cases"]] == ["US-068-AC3", "US-068-AC6"]


def test_injected_factory_wrong_host_refuses_before_cases_or_receipt(collector) -> None:
    args, default, written = collector
    factory = AsyncMock(
        return_value=credentials.FreshCredential(
            "https://other.example.test", " ".join(("Bearer", "fabricated"))
        )
    )
    with pytest.raises(RuntimeError, match="qualification_service_workspace_host_mismatch"):
        qualifier._execute(args, credential_factory=factory)
    factory.assert_awaited_once_with()
    default.assert_not_called()
    assert written == []


def test_factory_may_return_a_noncoroutine_awaitable(collector, monkeypatch) -> None:
    args, default, written = collector
    calls = []

    def factory():
        future = asyncio.get_running_loop().create_future()
        future.set_result(credentials.FreshCredential(_HOST, " ".join(("Bearer", "fabricated"))))
        calls.append(future)
        return future

    async def cases(*args, credential_factory, **kwargs):
        assert credential_factory is factory
        return [], {}

    monkeypatch.setattr(qualifier, "_run_cases", cases)
    qualifier._execute(args, credential_factory=factory)
    assert len(calls) == 1
    default.assert_not_called()
    assert len(written) == 1


@pytest.mark.parametrize("cancelled", [False, True], ids=["refusal", "cancellation"])
def test_injected_factory_failure_never_falls_back_or_writes_receipt(collector, cancelled) -> None:
    args, default, written = collector
    error = asyncio.CancelledError() if cancelled else RuntimeError("fabricated_credential_refusal")
    factory = AsyncMock(side_effect=error)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            qualifier._execute(args, credential_factory=factory)
    else:
        with pytest.raises(RuntimeError, match="fabricated_credential_refusal"):
            qualifier._execute(args, credential_factory=factory)
    factory.assert_awaited_once_with()
    default.assert_not_called()
    assert written == []


@pytest.mark.parametrize("injected", [False, True], ids=["default-cli", "injected-factory"])
def test_receipt_records_actual_credential_composition_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, injected: bool
) -> None:
    source = {"source.py": "fabricated-digest"}
    monkeypatch.setattr(evidence, "inventory", lambda root: source)
    output = tmp_path / "receipt.json"
    mode = "injected-factory" if injected else "keychain-child-sdk"
    evidence.write_selected_receipt(
        tmp_path,
        output,
        source,
        profile="fabricated-human",
        origin="https://apps.example.test",
        versions={},
        case_ids={"fabricated-case": "fabricated-node"},
        cases=[("fabricated-case", 0.01)],
        credential_source=mode,
    )
    recorded = json.loads(output.read_text())
    assert recorded["credential_source"] == mode
    assert ("credential_factory=<explicit-factory>" in recorded["command"]) is injected
    assert recorded["profile"] == "selected-databricks-apps"
    assert recorded["test_results"][0]["node_id"] == "fabricated-node"
