"""Parse the explicitly selected live Apps endpoint and path settings."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class LiveSettings:
    app_name: str
    url: str
    mount_path: str
    api_prefix: str


def settings_from_environment(env: dict[str, str]) -> LiveSettings:
    app_name = _required(env, "TETHER_QUALIFICATION_APP")
    url = safe_origin(_required(env, "TETHER_QUALIFICATION_APP_URL"))
    mount_path = env.get("TETHER_MOUNT_PATH", "").rstrip("/")
    api_prefix = env.get("TETHER_API_PREFIX", "/api/v1")
    if mount_path and (not mount_path.startswith("/") or ".." in mount_path):
        raise RuntimeError("qualification_mount_path_invalid")
    if not api_prefix.startswith("/") or api_prefix.endswith("/"):
        raise RuntimeError("qualification_api_prefix_invalid")
    return LiveSettings(app_name, url, mount_path, api_prefix)


def _required(env: dict[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise RuntimeError("qualification_settings_missing")
    return value


def safe_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise RuntimeError("qualification_app_url_invalid") from error
    try:
        port = parsed.port
    except ValueError:
        port = -1
    valid_port = port is None or 1 <= port <= 65535
    if (
        any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
        or "?" in value
        or "#" in value
        or "\\" in parsed.netloc
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not valid_port
        or parsed.netloc.endswith(":")
        or any(character.isspace() for character in parsed.hostname)
        or "%" in parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("qualification_app_url_invalid")
    return f"https://{parsed.netloc}"
