import httpx
import pytest
from qualify_web_platform import profile_from_environment

from tether.client.databricks import SERVICE_CALLER_CREDENTIAL_HEADER, service_caller_auth


def _bearer(value: str) -> str:
    return " ".join(("Bearer", value))


def valid_environment() -> dict[str, str]:
    return {
        "TETHER_QUALIFICATION_APP": "fabricated-app",
        "TETHER_QUALIFICATION_APP_URL": "https://example.invalid",
        "TETHER_PG_HOST": "db.example.invalid",
        "TETHER_PG_DATABASE": "qualification",
        "TETHER_PG_SCHEMA": "tether_qualification",
        "TETHER_PG_ENDPOINT": "fabricated-endpoint",
        "TETHER_PG_SSLROOTCERT": "qualification-ca.pem",
    }


def test_profile_requires_all_explicit_live_settings() -> None:
    values = valid_environment()
    del values["TETHER_PG_ENDPOINT"]
    with pytest.raises(ValueError, match="qualification_settings_missing"):
        profile_from_environment(values)


def test_profile_refuses_non_https_url_and_invalid_schema() -> None:
    values = valid_environment()
    values["TETHER_QUALIFICATION_APP_URL"] = "http://example.invalid"
    with pytest.raises(ValueError, match="qualification_app_url_invalid"):
        profile_from_environment(values)
    values = valid_environment()
    values["TETHER_PG_SCHEMA"] = "bad;drop"
    with pytest.raises(ValueError, match="qualification_schema_invalid"):
        profile_from_environment(values)


def test_profile_uses_explicit_api_prefix() -> None:
    values = valid_environment()
    values["TETHER_API_PREFIX"] = "/root/api/v1"
    settings = profile_from_environment(values)
    assert settings.api_prefix == "/root/api/v1"


def test_profile_selects_only_known_qualification_scenarios() -> None:
    values = valid_environment()
    assert profile_from_environment(values).scenario == "basic"
    values["TETHER_QUALIFICATION_SCENARIO"] = "shared-session"
    assert profile_from_environment(values).scenario == "shared-session"
    values["TETHER_QUALIFICATION_SCENARIO"] = "unknown"
    with pytest.raises(ValueError, match="qualification_scenario_invalid"):
        profile_from_environment(values)


@pytest.mark.anyio
async def test_projected_service_auth_copies_bearer_without_replacing_authorization() -> None:
    seen: list[tuple[str | None, str | None]] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.headers.get("Authorization"),
                request.headers.get(SERVICE_CALLER_CREDENTIAL_HEADER),
            )
        )
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), auth=service_caller_auth()
    ) as client:
        await client.get(
            "https://example.invalid",
            headers={"Authorization": _bearer("qualification-bearer")},
        )
    assert seen == [(_bearer("qualification-bearer"), _bearer("qualification-bearer"))]
