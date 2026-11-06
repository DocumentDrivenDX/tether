"""Separate deployed H2C capability from the proxy's observed protocol."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

_HASH = re.compile(r"[0-9a-f]{64}")
_PROCESS = re.compile(r"[0-9a-f]{32}")
_INVOCATION = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MODULES = {
    "carrier_composition": "packages/databricks/src/tether_databricks/composition.py",
    "launcher": "examples/databricks-app/src/app.py",
}


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("platform_transport_schema_invalid")
    return value


def _correlation(value: dict[str, Any], process: str, nonce: str) -> None:
    if value.get("process_uuid") != process or value.get("nonce_sha256") != nonce:
        raise ValueError("platform_transport_process_or_nonce_mismatch")


def _modules(root: Path, value: object) -> dict[str, str]:
    supplied = _object(value, set(_MODULES))
    expected = {
        name: hashlib.sha256((root / source).read_bytes()).hexdigest()
        for name, source in _MODULES.items()
    }
    if supplied != expected:
        raise ValueError("platform_transport_loaded_source_mismatch")
    return expected


def validate_transport(
    root: Path, value: object, *, invocation_id: str | None = None
) -> dict[str, Any]:
    """Require same-process live ingress and exact deployed-listener H2C proof."""
    proof = _object(
        value,
        {
            "schema_version",
            "invocation_id",
            "process_uuid",
            "nonce_sha256",
            "loaded_module_sha256",
            "ingress",
            "h2c",
        },
    )
    process, nonce, invocation = (
        proof["process_uuid"],
        proof["nonce_sha256"],
        proof["invocation_id"],
    )
    if (
        type(proof["schema_version"]) is not int
        or proof["schema_version"] != 1
        or not isinstance(process, str)
        or _PROCESS.fullmatch(process) is None
        or not isinstance(nonce, str)
        or _HASH.fullmatch(nonce) is None
        or not isinstance(invocation, str)
        or _INVOCATION.fullmatch(invocation) is None
        or (invocation_id is not None and invocation != invocation_id)
    ):
        raise ValueError("platform_transport_correlation_invalid")
    modules = _modules(root, proof["loaded_module_sha256"])
    ingress = _ingress(proof["ingress"], process, nonce)
    h2c = _h2c(proof["h2c"], process, nonce)
    return {
        "schema_version": 1,
        "invocation_id": invocation,
        "process_uuid": process,
        "nonce_sha256": nonce,
        "loaded_module_sha256": modules,
        "ingress": dict(ingress),
        "h2c": dict(h2c),
    }


def _ingress(value: object, process: str, nonce: str) -> dict[str, Any]:
    proof = _object(
        value,
        {
            "scope",
            "response_status",
            "frontend_http_version",
            "asgi_http_version",
            "process_uuid",
            "nonce_sha256",
        },
    )
    _correlation(proof, process, nonce)
    if (
        proof["scope"] != "public-apps-ingress"
        or type(proof["response_status"]) is not int
        or proof["response_status"] != 200
        or proof["frontend_http_version"] not in ("HTTP/1.1", "HTTP/2")
        or proof["asgi_http_version"] not in ("1.1", "2")
    ):
        raise ValueError("platform_public_ingress_not_qualified")
    return proof


def _h2c(value: object, process: str, nonce: str) -> dict[str, Any]:
    proof = _object(
        value,
        {
            "scope",
            "negotiation",
            "response_status",
            "asgi_http_version",
            "response_bytes",
            "proxy_protocol_claim",
            "process_uuid",
            "nonce_sha256",
        },
    )
    _correlation(proof, process, nonce)
    if (
        proof["scope"] != "deployed-listener"
        or proof["negotiation"] != "prior-knowledge"
        or type(proof["response_status"]) is not int
        or proof["response_status"] != 200
        or proof["asgi_http_version"] != "2"
        or type(proof["response_bytes"]) is not int
        or not 0 < proof["response_bytes"] <= 1024 * 1024
        or proof["proxy_protocol_claim"] is not False
    ):
        raise ValueError("platform_deployed_h2c_not_qualified")
    return proof


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("platform_shutdown_measurement_invalid")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("platform_shutdown_measurement_invalid")
    return result


def _strings(value: object, allowed: set[str]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) for item in value)
        or len(value) != len(set(value))
        or not set(value) <= allowed
    ):
        raise ValueError("platform_shutdown_observation_invalid")
    return value


def validate_shutdown_observation(
    root: Path, value: object, *, invocation_id: str | None = None
) -> dict[str, Any]:
    """Validate physical ownership, main return and both correlated transports."""
    if not isinstance(value, dict):
        raise ValueError("platform_shutdown_observation_invalid")
    elapsed = _number(value.get("signal_to_report_seconds"))
    returned = _number(value.get("signal_to_main_return_seconds"))
    configured = _number(value.get("configured_shutdown_seconds"))
    if not 0 <= elapsed <= returned <= configured < 15 or configured == 0:
        raise ValueError("platform_shutdown_exceeds_configured_or_platform_bound")
    outstanding = _strings(
        value.get("outstanding"),
        {"client_host", "postgres_root", "platform_resources", "api", "ui", "carrier_lifecycle"},
    )
    protocols = _strings(value.get("asgi_http_versions"), {"1.1", "2"})
    worker = value.get("worker_invocation_id")
    if (
        value.get("signal_name") != "SIGTERM"
        or value.get("complete") is not False
        or value.get("main_returned") is not True
        or "client_host" not in outstanding
        or not isinstance(worker, str)
        or (invocation_id is not None and worker != invocation_id)
    ):
        raise ValueError("platform_shutdown_physical_ownership_evidence_missing")
    transport = validate_transport(root, value.get("transport"), invocation_id=worker)
    validate_observer(value.get("observer"))
    if transport["ingress"]["asgi_http_version"] not in protocols or "2" not in protocols:
        raise ValueError("platform_shutdown_ingress_observation_mismatch")
    return transport


def validate_observer(value: object) -> dict[str, Any]:
    """Declare temporary instrumentation and opaque actual-stop correlation."""
    hashes = {"observer_sha256", "probe_sha256", "bundle_sha256", "stop_binding_sha256"}
    proof = _object(
        value,
        hashes
        | {
            "mode",
            "stop_state",
            "candidate_modules_byte_identical",
            "h2c_completed_before_sigterm",
        },
    )
    if (
        proof["mode"] != "instrumented-qualification-only"
        or proof["stop_state"] != "STOPPED"
        or proof["candidate_modules_byte_identical"] is not True
        or proof["h2c_completed_before_sigterm"] is not True
        or any(
            not isinstance(proof[name], str) or _HASH.fullmatch(proof[name]) is None
            for name in hashes
        )
    ):
        raise ValueError("platform_observer_provenance_invalid")
    return dict(proof)
