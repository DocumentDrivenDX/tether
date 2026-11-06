"""Commands and exact workload identities for selected reference profiles."""

from __future__ import annotations

from typing import NamedTuple


class ProfileSpec(NamedTuple):
    profile: str
    script: str
    deadline_seconds: int
    arguments: tuple[str, ...] = ()


PROFILES = {
    "store": ProfileSpec("local-posix-jsonl", "benchmark_web.py", 18_000, ("--storage", "posix")),
    "memory-store": ProfileSpec(
        "memoryfiles-jsonl-reference-capacity", "benchmark_web.py", 18_000, ("--storage", "memory")
    ),
    "client": ProfileSpec(
        "local-posix-jsonl-public-client", "benchmark_client.py", 3_600, ("--storage", "posix")
    ),
    "volatile-client": ProfileSpec(
        "volatile-public-client", "benchmark_client.py", 3_600, ("--storage", "volatile")
    ),
    "engine": ProfileSpec("public-engine-exclusive-wall", "benchmark_engine.py", 1_800),
    "follower-memory": ProfileSpec(
        "public-follower-retained-python", "benchmark_follower_memory.py", 3_600
    ),
}

# Authenticated Apps execution is supplied by the separately supervised operator
# wrapper, never implicitly launched by the local measurement command.
EXTERNAL_PROFILES = {"apps": "apps-public-client-read"}
DYNAMIC_IDENTITIES = {"apps": ("workload.list_items_per_page", "http_versions")}
MEMORY_ALLOCATIONS = (
    "single_follower_empty_active_persisted_queue_current_python_bytes",
    "single_follower_full_persisted_queue_python_growth_bytes",
    "single_follower_full_live_buffer_python_growth_bytes",
    "single_follower_coalesced_live_event_python_growth_bytes",
)

STORE_IDENTITY: dict[str, object] = {
    "workload": {
        "sessions": 10_000,
        "entries_per_session": 200,
        "large_session_entries": 100_000,
        "samples": 100,
        "entry_facts_bytes": 768,
        "page_positions": [index * 99_900 // 99 for index in range(100)],
    },
    "limits": {"max_index_bytes": 4 * 1024**3, "lease_seconds": 300, "feed_retention": 10_000},
    "checks.page_size": 100,
    "checks.page_indices_exact": True,
    "checks.page_ranges_checked": 101,
}

CLIENT_IDENTITY: dict[str, object] = {
    "child_environment_profile": "explicit-local-path/current-source/fixed-locale",
    "samples": 100,
    "followers.followers": 1_000,
    "followers.queue_size": 32,
    "followers.commits": 100,
    "streaming_followers.followers": 1_000,
    "streaming_followers.queue_size": 32,
    "streaming_followers.stream_chunks": 200,
    "streaming_followers.consumers_verified": 1000,
    "streaming_followers.exact_ordered_reply_consumers": 1000,
    "streaming_followers.terminal_boundaries": 1000,
    "streaming_followers.missed_events": 0,
    "cursor_reconnect.feed_retention": 8,
    "cursor_reconnect.results.retained_cursor.sample_count": 100,
    "cursor_reconnect.results.retained_cursor.missed_commits_per_sample": 3,
    "cursor_reconnect.results.retained_cursor.total_missed_commits": 300,
    "cursor_reconnect.results.retained_cursor.exact_ordered_last_in_commit_count": 300,
    "cursor_reconnect.results.retention_floor.sample_count": 100,
    "cursor_reconnect.results.retention_floor.observed_too_old_resync_count": 100,
    "cursor_reconnect.results.retention_floor.snapshot_at_latest_watermark_count": 100,
    "cursor_reconnect.results.retention_floor.boundary_since_replayed": True,
    "cursor_reconnect.results.retention_floor.one_before_boundary_resynced": True,
}

IDENTITIES: dict[str, dict[str, object]] = {
    "store": {**STORE_IDENTITY, "durability": "os"},
    "memory-store": {**STORE_IDENTITY, "durability": "none"},
    "client": {
        **CLIENT_IDENTITY,
        "storage.configuration": "LocalJsonlSettings",
        "storage.durability": "os",
        "separate_process_follower.followers": 1,
        "separate_process_follower.commits": 100,
        "startup.cli_operational_first_output.sample_count": 100,
        "startup.cli_operational_first_output.first_byte_observed": True,
        "startup.cli_operational_first_output.clean_exit_samples": 100,
        "startup.cli_operational_first_output.runtime_provenance."
        "console_shebang_matches_active_interpreter": True,
        "startup.cli_operational_first_output.runtime_provenance."
        "child_import_matches_checkout_source": True,
    },
    "volatile-client": {
        **CLIENT_IDENTITY,
        "storage.configuration": "VolatileSettings",
        "storage.durability": "none",
        "storage.resume": "off",
    },
    "engine": {
        "workload": {
            "samples": 100,
            "model_calls_per_step": 1,
            "tools": 0,
            "context_policy": "full",
            "history": "fresh",
        },
        "measurements.total_step_wall.sample_count": 100,
        "measurements.exclusive_wall_upper_bound.sample_count": 100,
        "observed_boundary_calls.model_stream": 100,
        "observed_boundary_calls.attempt_reserve": 100,
        "observed_boundary_calls.attempt_start": 100,
        "observed_boundary_calls.attempt_observe": 100,
        "observed_boundary_calls.attempt_callbacks": 300,
    },
    "follower-memory": {
        **{"measurements." + key + ".sample_count": 100 for key in MEMORY_ALLOCATIONS},
        "measurements.serialized_event_payload_bytes.live_buffer_max_per_sample.sample_count": 100,
        "measurements.serialized_event_payload_bytes.coalesced_event_per_sample.sample_count": 100,
        "workload": {
            "single_follower_trials": 100,
            "aggregate_followers": 1000,
            "queue_size": 32,
            "persisted_queue_capacity": 32,
            "live_buffer_capacity": 32,
            "held_turns": 32,
            "max_active_turns": 32,
            "overflow_active_turns": 32,
            "event_text_bytes": 760,
            "coalesced_total_model_text_bytes": 32768,
            "coalesced_buffered_text_bytes": 32767,
            "coalesced_response_limit_bytes": 65536,
            "max_read_bytes": 1048576,
        },
        "measurements.aggregate_1000_followers.registered_followers": 1000,
        "measurements.aggregate_1000_followers.ready_followers": 1000,
        "measurements.causal_occupancy.persisted_queue": {
            "capacity": 32,
            "occupancy": 32,
            "changes_enqueued_after_one_consumed": 32,
            "journal_commits_preseeded_before_baseline": 35,
            "overflow_probe_pending_before_snapshot": True,
        },
        "measurements.causal_occupancy.live_buffer": {"capacity": 32, "occupancy": 32},
        "measurements.causal_occupancy.coalesced_live": {
            "events": 1,
            "text_bytes": 32767,
            "total_model_text_bytes": 32768,
            "later_delta_chunks": 32,
            "distinct_attempts": 1,
        },
        **{
            "measurements.event_text_bytes." + key + "." + field: expected
            for key, expected in (
                ("live_buffer_max_per_sample", 760),
                ("coalesced_buffered_per_sample", 32767),
            )
            for field in ("min_bytes", "max_bytes")
        },
        "measurements.event_text_bytes.live_buffer_max_per_sample.sample_count": 100,
        "measurements.event_text_bytes.coalesced_buffered_per_sample.sample_count": 100,
    },
    "apps": {
        "workload.samples": 100,
        "workload.list_limit": 50,
        "workload.capability_requests": 100,
        "workload.list_requests": 100,
        "workload.persistent_httpx": True,
        "workload.credential_mode": "preacquired-ram",
        "checks.deployment_current_and_exact": True,
        "checks.credential_accepted_on_all_requests": True,
        "checks.same_preacquired_credential": True,
        "measurements.capability_handshake.sample_count": 100,
        "measurements.list_page.sample_count": 100,
        "limits.server_hardware": "unknown-managed",
        "limits.client_timing": "credential-provider+network+typed-decode",
        "limits.transport": "persistent-httpx-sequential",
    },
}

STORE_METRICS = {
    "results.list_50.p99_ms",
    "results.view.p99_ms",
    "results.entries_100_beginning.p99_ms",
    "results.entries_100_middle.p99_ms",
    "results.entries_100_end.p99_ms",
    "results.entries_100_spread.p99_ms",
    "results.append_four.p99_ms",
}

CLIENT_METRICS = {
    "host_open.p99_ms",
    "followers.command_to_follower.p99_ms",
    "followers.event_loop_lag.p99_ms",
    "reconnect.command_to_follower.p99_ms",
    "reconnect.event_loop_lag.p99_ms",
    "streaming_followers.event_loop_lag.p99_ms",
    "total_process_peak_rss_including_host_bytes",
    "scripted_turn_total.p99_ms",
    "engine_step_total_time_upper_bound.p99_ms",
    "cursor_reconnect.results.retained_cursor.reconnect_to_replay_complete.p99_ms",
    "cursor_reconnect.results.retention_floor.reconnect_to_resync_snapshot.p99_ms",
}

METRICS = {
    "store": STORE_METRICS,
    "memory-store": STORE_METRICS,
    "client": CLIENT_METRICS
    | {
        "separate_process_follower.command_to_follower_upper_bound.p99_ms",
        "separate_process_follower.total_follower_process_peak_rss_bytes",
        "startup.core_import.p99_ms",
        "startup.cli_help.p99_ms",
        "startup.cli_operational_first_output.spawn_to_first_byte.p99_ms",
        "startup.textual_first_paint.p99_ms",
    },
    "volatile-client": CLIENT_METRICS,
    "engine": {
        "measurements.exclusive_wall_upper_bound.p99_ms",
        "measurements.total_step_wall.p99_ms",
    },
    "follower-memory": {
        "measurements." + key + suffix
        for key in MEMORY_ALLOCATIONS
        for suffix in (".p99_bytes", ".max_bytes")
    }
    | {
        "measurements.aggregate_1000_followers.python_retained_growth_bytes",
        "measurements.aggregate_1000_followers.whole_process_peak_rss_after_bytes",
    },
    "apps": {
        "measurements.capability_handshake.p99_ms",
        "measurements.list_page.p99_ms",
    },
}
