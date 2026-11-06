"""Copied metadata for the two live log-privacy request paths."""

from __future__ import annotations

from dataclasses import dataclass

_OBSERVATION_REASONS = frozenset(
    {
        "platform_log_capture_limit_exceeded",
        "platform_log_capture_incomplete",
        "platform_log_cli_missing",
        "platform_log_format_invalid",
        "platform_log_pipe_unavailable",
        "platform_log_record_invalid",
        "platform_log_stream_empty",
        "platform_log_subscription_did_not_start",
        "platform_log_subscription_start_failed",
        "platform_log_subscriptions_incomplete",
        "privacy_app_window_not_anchored",
        "privacy_invocation_marker_unavailable",
        "privacy_observation_incomplete",
        "privacy_platform_window_not_anchored",
    }
)


class PrivacyObservationIncomplete(RuntimeError):
    """Captured evidence could not establish a complete, correctly anchored window."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _OBSERVATION_REASONS else "privacy_observation_incomplete"
        super().__init__(self.reason)


@dataclass(frozen=True)
class PrivacyTurn:
    content_marker: str
    invocation_id: str
    session_id: str


@dataclass(frozen=True)
class PrivacyRejection:
    content_marker: str
    credential_marker: str
    session_id: str


@dataclass(frozen=True)
class PrivacyWindow:
    app_logs: str
    platform_logs: str
    diagnostics: tuple[str, str]
    success: PrivacyTurn
    rejected: PrivacyRejection
