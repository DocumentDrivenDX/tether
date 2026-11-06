"""Typed incomplete-result mapping for selected-profile CLI boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import qualify_selected_profiles as qualifier
from qualify_live_apps_incomplete import LiveQualificationIncomplete
from selected_profiles_identity_probe import IdentityObservationIncomplete
from selected_profiles_privacy_types import PrivacyObservationIncomplete


def _install_main_stubs(monkeypatch, error: Exception) -> None:
    monkeypatch.setattr(qualifier, "exec_with_profile_environment", lambda *_args: None)
    monkeypatch.setattr(qualifier, "_parse_args", lambda: SimpleNamespace())
    monkeypatch.setattr(
        qualifier,
        "_execute",
        lambda _args: (_ for _ in ()).throw(error),
    )


def test_privacy_observation_incomplete_is_not_reported_as_behavior_failure(
    monkeypatch, capsys
) -> None:
    _install_main_stubs(monkeypatch, PrivacyObservationIncomplete("canary-private-reason"))

    assert qualifier.main() == 2
    assert (
        capsys.readouterr().err.strip()
        == "selected_profile_check_incomplete=privacy_observation_incomplete"
    )


def test_identity_budget_exhaustion_is_not_reported_as_revocation_or_failure(
    monkeypatch, capsys
) -> None:
    _install_main_stubs(
        monkeypatch,
        IdentityObservationIncomplete("identity_workspace_transition_timeout"),
    )

    assert qualifier.main() == 2
    assert (
        capsys.readouterr().err.strip()
        == "selected_profile_check_incomplete=identity_workspace_transition_timeout"
    )


def test_example_lifespan_observation_is_mapped_to_incomplete_exit_two(monkeypatch, capsys) -> None:
    _install_main_stubs(
        monkeypatch,
        LiveQualificationIncomplete("reference_runtime_startup_not_observed"),
    )

    assert qualifier.main() == 2
    assert (
        capsys.readouterr().err.strip()
        == "selected_profile_check_incomplete=reference_runtime_startup_not_observed"
    )
