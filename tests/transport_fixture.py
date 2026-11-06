"""Explicit fabricated observation for qualification validator controls."""

import hashlib
from pathlib import Path
from typing import Any

_MODULES = {
    "carrier_composition": "packages/databricks/src/tether_databricks/composition.py",
    "launcher": "examples/databricks-app/src/app.py",
}


def shutdown_observation(root: Path, *, synthetic: bool = False) -> dict[str, Any]:
    modules = {}
    for name, source in _MODULES.items():
        path = root / source
        if synthetic:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Fabricated source fixture only\n")
        modules[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    process, nonce = "1" * 32, "2" * 64
    correlation = {"process_uuid": process, "nonce_sha256": nonce}
    return {
        "signal_name": "SIGTERM",
        "signal_to_report_seconds": 1.0,
        "signal_to_main_return_seconds": 1.1,
        "configured_shutdown_seconds": 14.0,
        "main_returned": True,
        "worker_invocation_id": "fabricated-worker",
        "complete": False,
        "outstanding": ["client_host"],
        "asgi_http_versions": ["1.1", "2"],
        "transport": {
            "schema_version": 1,
            "invocation_id": "fabricated-worker",
            **correlation,
            "loaded_module_sha256": modules,
            "ingress": {
                "scope": "public-apps-ingress",
                "response_status": 200,
                "frontend_http_version": "HTTP/2",
                "asgi_http_version": "1.1",
                **correlation,
            },
            "h2c": {
                "scope": "deployed-listener",
                "negotiation": "prior-knowledge",
                "response_status": 200,
                "asgi_http_version": "2",
                "response_bytes": 100,
                "proxy_protocol_claim": False,
                **correlation,
            },
        },
        "observer": {
            "mode": "instrumented-qualification-only",
            "observer_sha256": "3" * 64,
            "probe_sha256": "4" * 64,
            "bundle_sha256": "5" * 64,
            "stop_binding_sha256": "6" * 64,
            "stop_state": "STOPPED",
            "candidate_modules_byte_identical": True,
            "h2c_completed_before_sigterm": True,
        },
    }
