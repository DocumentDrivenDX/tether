"""Qualification cannot confuse server capability with negotiated ingress."""

import copy
from pathlib import Path
from typing import Any

import pytest
from transport_fixture import shutdown_observation
from web_transport_receipt import validate_shutdown_observation, validate_transport


def _change(value: dict[str, Any], path: str, replacement: object) -> None:
    parts = path.split(".")
    target = value
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = replacement


def test_deployed_h2c_and_http11_proxy_are_separate_correlated_facts(tmp_path: Path) -> None:
    value = shutdown_observation(tmp_path, synthetic=True)
    proof = validate_shutdown_observation(tmp_path, value, invocation_id="fabricated-worker")
    assert proof["ingress"]["asgi_http_version"] == "1.1"
    assert proof["h2c"]["asgi_http_version"] == "2"
    assert proof["h2c"]["proxy_protocol_claim"] is False
    assert proof is not value["transport"]
    proof["ingress"]["asgi_http_version"] = "2"
    assert value["transport"]["ingress"]["asgi_http_version"] == "1.1"


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("schema_version", True),
        ("process_uuid", "invalid"),
        ("nonce_sha256", "invalid"),
        ("invocation_id", "other-worker"),
        ("loaded_module_sha256.carrier_composition", "0" * 64),
        ("loaded_module_sha256.launcher", "0" * 64),
        ("ingress.process_uuid", "0" * 32),
        ("ingress.nonce_sha256", "0" * 64),
        ("ingress.scope", "local-only"),
        ("ingress.response_status", True),
        ("ingress.response_status", 401),
        ("ingress.frontend_http_version", "HTTP/3"),
        ("ingress.asgi_http_version", "3"),
        ("h2c.process_uuid", "0" * 32),
        ("h2c.nonce_sha256", "0" * 64),
        ("h2c.scope", "local-only"),
        ("h2c.negotiation", "assumed"),
        ("h2c.response_status", 502),
        ("h2c.asgi_http_version", "1.1"),
        ("h2c.response_bytes", 0),
        ("h2c.response_bytes", 1048577),
        ("h2c.response_bytes", True),
        ("h2c.proxy_protocol_claim", True),
    ],
)
def test_transport_refuses_mismatched_or_unobserved_facts(
    tmp_path: Path, path: str, replacement: object
) -> None:
    value = shutdown_observation(tmp_path, synthetic=True)["transport"]
    _change(value, path, replacement)
    with pytest.raises(ValueError, match="platform_"):
        validate_transport(tmp_path, value, invocation_id="fabricated-worker")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("signal_name", "SIGINT"),
        ("main_returned", False),
        ("complete", True),
        ("worker_invocation_id", "different-worker"),
        ("signal_to_report_seconds", -1),
        ("signal_to_report_seconds", 1.2),
        ("signal_to_report_seconds", float("nan")),
        ("signal_to_main_return_seconds", 0.5),
        ("signal_to_main_return_seconds", 14.1),
        ("signal_to_main_return_seconds", float("inf")),
        ("configured_shutdown_seconds", 15),
        ("configured_shutdown_seconds", 0),
        ("outstanding", []),
        ("outstanding", ["platform_resources"]),
        ("outstanding", ["client_host", "client_host"]),
        ("asgi_http_versions", ["1.1"]),
        ("asgi_http_versions", ["2"]),
        ("observer.mode", "uninstrumented"),
        ("observer.h2c_completed_before_sigterm", False),
        ("observer.stop_state", "STOPPING"),
        ("observer.candidate_modules_byte_identical", False),
        ("observer.observer_sha256", "invalid"),
        ("observer.probe_sha256", "invalid"),
        ("observer.bundle_sha256", "invalid"),
        ("observer.stop_binding_sha256", "invalid"),
    ],
)
def test_shutdown_refuses_late_incomplete_or_unbound_observations(
    tmp_path: Path, path: str, replacement: object
) -> None:
    value = shutdown_observation(tmp_path, synthetic=True)
    _change(value, path, replacement)
    with pytest.raises(ValueError, match="platform_"):
        validate_shutdown_observation(tmp_path, value, invocation_id="fabricated-worker")


@pytest.mark.parametrize("path", ["transport", "transport.ingress", "transport.h2c", "observer"])
def test_receipt_refuses_unknown_fields_instead_of_copying_raw_diagnostics(
    tmp_path: Path, path: str
) -> None:
    value = shutdown_observation(tmp_path, synthetic=True)
    target = value
    for part in path.split("."):
        target = target[part]
    target["untrusted_extra"] = "fabricated-private-marker"
    with pytest.raises(ValueError, match="platform_"):
        validate_shutdown_observation(tmp_path, value)


def test_receipt_requires_explicit_held_worker_and_current_loaded_source(tmp_path: Path) -> None:
    value = shutdown_observation(tmp_path, synthetic=True)
    missing = copy.deepcopy(value)
    del missing["worker_invocation_id"]
    with pytest.raises(ValueError, match="platform_"):
        validate_shutdown_observation(tmp_path, missing, invocation_id="fabricated-worker")
    (tmp_path / "examples/databricks-app/src/app.py").write_text("# Changed after observation\n")
    with pytest.raises(ValueError, match="loaded_source_mismatch"):
        validate_shutdown_observation(tmp_path, value)
