"""OpenAPI document assembly for the versioned HTTP wire contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from .schema import schema_registry
from .settings import ApiSettings


def _convert_refs(value: Any) -> Any:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        converted: dict[str, Any] = {}
        for key, item in mapping.items():
            if key == "$ref" and isinstance(item, str):
                converted[key] = item.replace("#/$defs/", "#/components/schemas/")
            else:
                converted[key] = _convert_refs(item)
        return converted
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [_convert_refs(item) for item in items]
    return value


def _openapi_registry() -> dict[str, Any]:
    return {name: _convert_refs(schema) for name, schema in schema_registry()["$defs"].items()}


def _response_contract(prefix: str) -> dict[tuple[str, str], list[tuple[str, bool, bool]]]:
    return {
        ("get", prefix + "/capabilities"): [("ClientCapabilities", False, False)],
        ("get", prefix + "/sessions"): [("PageSessionSummary", False, False)],
        ("post", prefix + "/sessions"): [("SessionSummary", False, False)],
        ("get", prefix + "/sessions/by-ref/{ref}"): [("SessionSummary", True, False)],
        ("get", prefix + "/sessions/{session_id}"): [("SessionView", False, False)],
        ("get", prefix + "/sessions/{session_id}/snapshot"): [("SessionSnapshot", False, False)],
        ("get", prefix + "/sessions/{session_id}/entries"): [("EntryPage", False, False)],
        ("get", prefix + "/sessions/{session_id}/history"): [("HistoryPage", False, False)],
        ("get", prefix + "/sessions/{session_id}/pending"): [("PendingConfirmation", True, False)],
        ("get", prefix + "/sessions/{session_id}/effects"): [("EffectPage", False, False)],
        ("post", prefix + "/sessions/{session_id}/call-details"): [("CallDetail", False, True)],
        ("post", prefix + "/sessions/{session_id}/turns"): [("AcceptedTurn", False, False)],
        ("get", prefix + "/sessions/{session_id}/turns/{turn_id}"): [("TurnResult", True, False)],
        ("delete", prefix + "/sessions/{session_id}/turns/{turn_id}"): [("boolean", False, False)],
        ("post", prefix + "/sessions/{session_id}/cancel"): [("boolean", False, False)],
        (
            "post",
            prefix + "/sessions/{session_id}/confirmations/{invocation_id}",
        ): [("ConfirmResponse", False, False), ("AcceptedTurn", False, False)],
        ("post", prefix + "/sessions/{session_id}/resume"): [("AcceptedTurn", False, False)],
        (
            "post",
            prefix + "/sessions/{session_id}/effects/{invocation_id}/resolve",
        ): [("SessionSummary", False, False)],
        ("patch", prefix + "/sessions/{session_id}"): [("SessionSummary", False, False)],
        ("post", prefix + "/sessions/{session_id}/close"): [("SessionSummary", False, False)],
        ("post", prefix + "/sessions/{session_id}/history-links"): [("Entry", False, False)],
        ("post", prefix + "/sessions/{session_id}/restore"): [("SessionSummary", False, False)],
        ("get", prefix + "/events"): [("event-stream", False, False)],
    }


def _response_schema(name: str, nullable: bool, array: bool) -> tuple[dict[str, Any], str]:
    if name == "event-stream":
        return {"type": "string"}, "text/event-stream"
    if name == "boolean":
        return {"type": "boolean"}, "application/json"
    ref: dict[str, Any] = {"$ref": f"#/components/schemas/{name}"}
    if array:
        ref = {"type": "array", "items": ref}
    elif nullable:
        ref = {"anyOf": [ref, {"type": "null"}]}
    return ref, "application/json"


def _add_response_contracts(document: dict[str, Any], settings: ApiSettings) -> None:
    paths = document.get("paths", {})
    for (method, path), outcomes in _response_contract(settings.api_prefix).items():
        operation = paths.get(path, {}).get(method)
        if operation is None:
            continue
        for index, (name, nullable, array) in enumerate(outcomes):
            status = "202" if method == "post" and name == "AcceptedTurn" else "200"
            if method == "post" and path == settings.api_prefix + "/sessions":
                status = "201"
            schema, media_type = _response_schema(name, nullable, array)
            if method == "post" and "confirmations" in path and index == 1:
                status = "202"
            response = operation.setdefault("responses", {}).setdefault(
                status, {"description": "Success"}
            )
            response["content"] = {media_type: {"schema": schema}}
        _add_problem_responses(operation)


def _add_problem_responses(operation: dict[str, Any]) -> None:
    for status in (400, 401, 403, 404, 408, 409, 413, 429, 501, 503):
        operation.setdefault("responses", {}).setdefault(
            str(status),
            {
                "description": "Problem Details",
                "content": {
                    "application/problem+json": {
                        "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                    }
                },
            },
        )


def install_openapi(app: FastAPI, settings: ApiSettings) -> None:
    """Attach the published registry endpoint and precise response schemas."""

    @app.get(settings.api_prefix + "/schema/http-v1.json", include_in_schema=False)
    async def schema_document() -> JSONResponse:
        return JSONResponse(schema_registry())

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            document = get_openapi(
                title=app.title,
                version=app.version,
                routes=app.routes,
                description=(
                    f"Wire models are published at {settings.api_prefix}/schema/http-v1.json."
                ),
            )
            components: Mapping[str, Any] = _openapi_registry()
            document.setdefault("components", {}).setdefault("schemas", {}).update(components)
            _add_response_contracts(document, settings)
            app.openapi_schema = document
        return app.openapi_schema

    app.openapi = custom_openapi
