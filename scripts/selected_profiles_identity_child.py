"""Private SDK and credential-source logic for the bounded OAuth child."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config

MAX_INPUT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True)
class CredentialChildRequest:
    profile: str
    config_file: str
    expected_host: str
    source: str
    secret_reference: str | None = None
    secret: str | None = field(default=None, repr=False)


def parse_credential_child_request(raw: bytes) -> CredentialChildRequest:
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError
    source = value.get("source")
    if not isinstance(source, str) or source not in {"keychain", "ram"}:
        raise ValueError
    credential_key = "secret_reference" if source == "keychain" else "secret"
    expected_keys = {"profile", "config_file", "expected_host", "source", credential_key}
    if set(value) != expected_keys:
        raise ValueError
    profile, config_file, expected_host = (
        value[key] for key in ("profile", "config_file", "expected_host")
    )
    if (
        not isinstance(profile, str)
        or not profile
        or len(profile) > 255
        or not isinstance(config_file, str)
        or not Path(config_file).is_absolute()
        or len(config_file) > 4096
        or not isinstance(expected_host, str)
        or not expected_host
        or len(expected_host) > 2048
    ):
        raise ValueError
    if source == "keychain":
        reference = value["secret_reference"]
        if (
            not isinstance(reference, str)
            or not Path(reference).is_absolute()
            or len(reference) > 4096
        ):
            raise ValueError
        return CredentialChildRequest(
            profile, config_file, expected_host, source, secret_reference=reference
        )
    secret = value["secret"]
    if not isinstance(secret, str) or not secret or len(secret) > 8 * 1024:
        raise ValueError
    return CredentialChildRequest(profile, config_file, expected_host, source, secret=secret)


def keychain_secret(reference_path: str) -> str:
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    service, account = reference.get("service"), reference.get("account")
    if (
        not isinstance(service, str)
        or not service
        or len(service) > 512
        or not isinstance(account, str)
        or not account
        or len(account) > 512
    ):
        raise ValueError
    result = subprocess.run(  # noqa: S603 - fixed system keychain binary, validated metadata arguments
        ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    secret = result.stdout.rstrip("\n")
    if not secret:
        raise ValueError
    return secret


def failure_category(error: Exception) -> str:
    from databricks.sdk.errors import (
        DeadlineExceeded,
        OperationTimeout,
        PermissionDenied,
        TemporarilyUnavailable,
        TooManyRequests,
        Unauthenticated,
    )
    from requests import RequestException

    if isinstance(error, RequestException):
        return "transient_network"
    if isinstance(
        error,
        (DeadlineExceeded, OperationTimeout, TemporarilyUnavailable, TooManyRequests),
    ):
        return "transient_network"
    if isinstance(error, (PermissionDenied, Unauthenticated)):
        return "transient_auth"
    if isinstance(error, (ValueError, FileNotFoundError, subprocess.CalledProcessError)):
        return "rejected"
    return "other"


def encode_child_result(
    *, host: str | None = None, authorization: str | None = None, failure: str | None = None
) -> bytes:
    status = "ok" if failure is None else "error"
    encoded = json.dumps(
        {"status": status, "host": host, "authorization": authorization, "failure": failure},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise RuntimeError("identity_credential_child_output_limit")
    return encoded + b"\n"


def credential_child_main(raw: bytes) -> int:
    try:
        request = parse_credential_child_request(raw)
        client_secret = (
            keychain_secret(request.secret_reference)
            if request.source == "keychain" and request.secret_reference is not None
            else request.secret
        )
        if not client_secret:
            raise ValueError
        config = Config(
            profile=request.profile,
            config_file=request.config_file,
            client_secret=client_secret,
            auth_type="oauth-m2m",
            http_timeout_seconds=8,
            retry_timeout_seconds=2,
        )
        workspace = WorkspaceClient(config=config)
        host = workspace.config.host
        if host != request.expected_host:
            output = encode_child_result(failure="host_mismatch")
        else:
            headers = dict(workspace.config.authenticate())
            authorization: Any = headers.get("Authorization") or headers.get("authorization")
            if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
                output = encode_child_result(failure="rejected")
            else:
                output = encode_child_result(host=host, authorization=authorization)
    except Exception as error:
        output = encode_child_result(failure=failure_category(error))
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0
