"""A privacy claim needs fresh APP and actual platform HTTP observations."""

from __future__ import annotations

import json

import pytest
from selected_profile_log_fixtures import privacy_logs
from selected_profiles_privacy import validate_privacy


def _validate(app: str, platform: str, *, diagnostics: tuple[str, ...] = ()) -> None:
    validate_privacy(
        app,
        platform,
        ("fabricated user marker", "fabricated refused marker"),
        "fabricated-credential-marker",
        "invocation-123",
        success_session_id="privacy-session",
        rejected_session_id="privacy-refused",
        api_prefix="/api",
        diagnostics=diagnostics,
    )


def test_real_available_platform_sources_require_fresh_success_and_refusal() -> None:
    app, platform = privacy_logs()
    assert '"SYSTEM"' not in platform
    _validate(app, platform)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("privacy-session", "previous-session"),
        ("privacy-refused", "previous-refusal"),
        ("401 Unauthorized", "500 Internal Server Error"),
        ('"source": "HTTP"', '"source": "APP"'),
    ],
    ids=["old-success", "old-refusal", "server-failure", "app-only"],
)
def test_unrelated_or_unclassified_platform_records_never_anchor_a_window(
    old: str, new: str
) -> None:
    app, platform = privacy_logs()
    with pytest.raises(RuntimeError, match="privacy_platform_window_not_anchored"):
        _validate(app, platform.replace(old, new))


def test_fresh_worker_cannot_be_replaced_by_old_app_metadata() -> None:
    _, platform = privacy_logs()
    with pytest.raises(RuntimeError, match="privacy_app_window_not_anchored"):
        _validate(privacy_logs("old-invocation")[0], platform)


@pytest.mark.parametrize("kind", ["app-content", "platform-credential", "cli-credential"])
def test_all_captured_output_is_checked_for_private_markers(kind: str) -> None:
    app, platform = privacy_logs()
    diagnostics: tuple[str, ...] = ()
    if kind == "app-content":
        app += "fabricated user marker"
    elif kind == "platform-credential":
        platform += "fabricated-credential-marker"
    else:
        diagnostics = ("fabricated-credential-marker",)
    with pytest.raises(RuntimeError, match="privacy_marker_found_in_platform_logs"):
        _validate(app, platform, diagnostics=diagnostics)


def test_corrupt_log_data_refuses_instead_of_counting_as_empty_safe_logs() -> None:
    app, platform = privacy_logs()
    with pytest.raises(RuntimeError, match="platform_log_format_invalid"):
        _validate(app, platform + "not-json")


def test_malformed_request_target_is_not_a_fresh_anchor() -> None:
    app, platform = privacy_logs()
    item = json.dumps({"source": "HTTP", "message": '"POST http://[ HTTP/1.1" 401 No'})
    _validate(app, item + "\n" + platform)
    with pytest.raises(RuntimeError, match="privacy_platform_window_not_anchored"):
        _validate(app, item)
