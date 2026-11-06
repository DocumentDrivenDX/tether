"""Closed result for live-qualification observation exhaustion."""

from __future__ import annotations

_REASONS = frozenset(
    {
        "live_admin_command_not_completed",
        "live_app_state_not_observed",
        "live_browser_exit_not_observed",
        "live_browser_output_not_settled",
        "live_browser_rendezvous_not_observed",
        "live_shutdown_report_not_observed",
        "live_effect_state_not_observed",
        "live_recovered_barrier_not_observed",
        "live_runtime_metadata_not_observed",
        "live_turn_terminal_not_observed",
        "live_log_follower_cleanup_not_observed",
        "live_tui_readiness_not_observed",
        "live_unknown_effect_intent_not_durable",
        "live_unknown_effect_recovery_not_visible",
        "live_worker_start_not_observed",
        "reference_runtime_startup_not_observed",
        "reference_runtime_shutdown_not_observed",
        "qualification_app_state_not_observed",
        "qualification_event_feed_not_started",
        "qualification_log_observer_readiness_unavailable",
        "qualification_provisional_reply_not_observed",
        "qualification_turn_terminal_not_observed",
        "qualification_shutdown_log_not_observed",
        "qualification_workspace_identity_not_observed",
        "qualification_database_credential_not_observed",
    }
)


class LiveQualificationIncomplete(RuntimeError):
    """A live observation ended without enough evidence to pass or fail behavior."""

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        self.reason = reason if reason in _REASONS else "live_observation_incomplete"
        super().__init__(self.reason if detail is None else f"{self.reason}:{detail}")


__all__ = ["LiveQualificationIncomplete"]
