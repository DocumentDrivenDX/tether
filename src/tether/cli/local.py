"""Explicit one-command local model selection; no environment or tokenizer downloads."""

from __future__ import annotations

import argparse
import asyncio
from http.client import HTTPMessage
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tether.types import JsonValue, Payload, PositiveInt

from .profile import FileAuthorization, ReferenceModelProfile, load_profile


class LocalModelOptions(Payload):
    base_url: str = ""
    databricks_profile: str | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=256)
    workspace: Path
    context_window_tokens: PositiveInt | None = None
    output_tokens: PositiveInt = 1024
    authorization_file: Path | None = None

    @model_validator(mode="after")
    def address(self) -> LocalModelOptions:
        if self.databricks_profile is not None:
            if self.base_url or self.authorization_file:
                raise ValueError(
                    "Databricks profile supplies host and authentication; "
                    "omit URL and authorization file"
                )
            return self
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) API base URL without credentials, query or fragment")
        if self.authorization_file is None and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError(
                "Anonymous local mode requires a loopback URL; use --authorization-file"
            )
        return self


def options(args: argparse.Namespace) -> LocalModelOptions | None:
    if args.openai_api_baseurl is None and args.databricks_profile is None:
        if any(
            value is not None
            for value in (
                args.model,
                args.workspace,
                args.context_window,
                args.output_tokens,
                args.authorization_file,
            )
        ):
            raise ValueError(
                "Model options require --gateway, --openai-api-baseurl or --databricks-profile"
            )
        return None
    if args.model_config is not None:
        raise ValueError("Choose --model-config or command-line connection options")
    return LocalModelOptions(
        base_url=args.openai_api_baseurl or "",
        databricks_profile=args.databricks_profile,
        model_name=args.model,
        workspace=(args.workspace or Path.cwd()).resolve(),
        context_window_tokens=args.context_window,
        output_tokens=args.output_tokens if args.output_tokens is not None else 1024,
        authorization_file=args.authorization_file.resolve() if args.authorization_file else None,
    )


class ServedModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=256)
    context_length: PositiveInt | None = None
    top_provider: dict[str, JsonValue] = Field(default_factory=dict)


class ServedModels(BaseModel):
    data: list[ServedModel] = Field(min_length=1, max_length=128)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        raise ValueError("Model discovery redirects are not supported")


def model_metadata(base_url: str, authorization: str | None) -> list[ServedModel]:
    request = Request(base_url.rstrip("/") + "/models")  # noqa: S310 — options validate HTTP(S)
    if authorization is not None:
        request.add_header("Authorization", authorization)

    with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=5) as response:
        data = response.read(65537)
    if len(data) > 65536:
        raise ValueError("Model discovery response exceeds 64 KiB")
    return ServedModels.model_validate_json(data).data


def positive(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


async def local_profile(selected: LocalModelOptions) -> ReferenceModelProfile:
    if selected.databricks_profile is not None:
        return await databricks_profile(selected)
    authorization = None
    if selected.authorization_file is not None:
        authorization = await FileAuthorization(selected.authorization_file).authorization()
        if authorization is None:
            raise ValueError("Authorization file is empty or invalid")
    try:
        models = await asyncio.to_thread(model_metadata, selected.base_url, authorization)
    except (OSError, ValueError):
        if selected.model_name is None:
            raise ValueError(
                "Cannot discover models. Check the URL (HTTP vs HTTPS) or supply --model"
            ) from None
        models = []
    return profile_from_models(selected, models)


def profile_from_models(
    selected: LocalModelOptions, models: list[ServedModel]
) -> ReferenceModelProfile:
    model = next((item for item in models if item.id == selected.model_name), None)
    if model is None and selected.model_name is None:
        model = next((item for item in models if item.id.endswith("-chat")), models[0])
    top = model.top_provider if model is not None else {}
    capacity = (model.context_length if model is not None else None) or positive(
        top.get("context_length")
    )
    window = selected.context_window_tokens or capacity or 16384
    output = min(
        selected.output_tokens, positive(top.get("max_completion_tokens")) or selected.output_tokens
    )
    if window <= output + 1024:
        raise ValueError(
            "Context budget must exceed --output-tokens plus the 1024-token safety margin"
        )
    return ReferenceModelProfile(
        id="local",
        base_url=selected.base_url.rstrip("/"),
        model_name=selected.model_name or (model.id if model is not None else ""),
        authorization_file=selected.authorization_file,
        databricks_profile=selected.databricks_profile,
        identity_mode="caller"
        if selected.authorization_file is not None or selected.databricks_profile is not None
        else "anonymous",
        output_limit_field="max_tokens",
        context_window_tokens=window,
        output_tokens=output,
        safety_margin_tokens=1024,
        max_response_bytes=1024 * 1024,
        workspace=selected.workspace,
        context_source="configured"
        if selected.context_window_tokens
        else "server"
        if capacity
        else "starter",
    )


async def resolve_profile(
    path: Path | None, selected: LocalModelOptions | None
) -> ReferenceModelProfile | None:
    if path is not None and selected is not None:
        raise ValueError("Choose --model-config or command-line connection options")
    return (
        await load_profile(path)
        if path is not None
        else await local_profile(selected)
        if selected
        else None
    )


async def databricks_profile(selected: LocalModelOptions) -> ReferenceModelProfile:
    from tether.adapters.databricks import CliProfile

    if selected.databricks_profile is None:
        raise ValueError("Databricks profile required")
    source = CliProfile(selected.databricks_profile)
    host = await source.host()
    source = CliProfile(selected.databricks_profile, expected_host=host)
    names = await source.models()
    if selected.model_name is not None and selected.model_name not in names:
        raise ValueError(
            "Selected model was not discovered among accessible system.ai chat services"
        )
    # Discovery supplies names/API compatibility, not verified context limits.
    resolved = selected.model_copy(update={"base_url": host + "/ai-gateway/mlflow/v1"})
    profile = profile_from_models(resolved, [ServedModel(id=name) for name in names])
    return profile.model_copy(
        update={
            "id": "databricks",
            "available_models": names if selected.model_name is None else (),
        }
    )
