"""Fabricated NDJSON in the qualified CLI's actual log-source shape."""

from __future__ import annotations

import json


def privacy_logs(
    invocation_id: str = "invocation-123",
    *,
    success_session_id: str = "privacy-session",
    rejected_session_id: str = "privacy-refused",
    api_prefix: str = "/api",
) -> tuple[str, str]:
    def line(source: str, message: str) -> str:
        return json.dumps({"source": source, "timestamp": 1.0, "message": message}) + "\n"

    app = line("APP", "TETHER_QUALIFICATION_WORKER_STARTED " + invocation_id)
    platform = (
        line("BUILD", "Fabricated deployment metadata")
        + line("HTTP", f'"GET {api_prefix}/sessions/{success_session_id} HTTP/1.1" 200 OK')
        + line(
            "HTTP",
            f'"POST {api_prefix}/sessions/{rejected_session_id}/turns HTTP/1.1" 401 Unauthorized',
        )
    )
    return app, platform
