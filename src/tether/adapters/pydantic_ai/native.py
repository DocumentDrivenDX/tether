"""Shared public native SDK composition for egress and offline wire projection."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2 as httpx
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition

from tether.runtime.pydantic_ai.messages import to_sdk_messages
from tether.types import ModelRequest, ModelUnavailable

NATIVE_CODEC = "openai-chat-text-tools-v1"

RESERVED = frozenset(
    {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "stream_options",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "extra_headers",
        "extra_body",
        "timeout",
        "response_format",
        "n",
        "reasoning_effort",
        "parallel_tool_calls",
    }
)


def validate_payload(request: ModelRequest, tiers: dict[str, str]) -> None:
    if request.model not in tiers:
        raise ModelUnavailable("unconfigured tier")
    if RESERVED.intersection(request.extra):
        raise ModelUnavailable("parameter_conflict")
    if request.effort is not None:
        raise ModelUnavailable("unsupported_effort")


async def request_native(
    request: ModelRequest,
    *,
    base_url: str,
    model_name: str,
    output_limit_field: str,
    transport: httpx.AsyncBaseTransport,
    supports_parallel_tool_calls: bool = True,
) -> ModelResponse:
    async with (
        httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        ) as http,
        AsyncOpenAI(
            api_key="-".join(("tether", "transport", "placeholder")),
            organization="",
            project="",
            admin_api_key="",
            webhook_secret="",
            base_url=base_url,
            max_retries=0,
            http_client=http,
        ) as client,
    ):
        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(openai_client=client),
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=output_limit_field
                == "max_completion_tokens",
                openai_supports_strict_tool_definition=False,
                openai_unsupported_model_settings=[]
                if supports_parallel_tool_calls
                else ["parallel_tool_calls"],
            ),
        )
        parameters = ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name=t.name,
                    description=t.description,
                    parameters_json_schema=t.input_schema,
                    strict=False,
                )
                for t in request.tools
            ]
        )
        settings = OpenAIChatModelSettings(
            max_tokens=request.max_output_tokens,
            extra_body=request.extra,
        )
        if supports_parallel_tool_calls:
            settings["parallel_tool_calls"] = False
        return await model.request(
            to_sdk_messages(request.messages, request.system), settings, parameters
        )


@asynccontextmanager
async def request_native_stream(
    request: ModelRequest,
    *,
    base_url: str,
    model_name: str,
    output_limit_field: str,
    transport: httpx.AsyncBaseTransport,
    supports_parallel_tool_calls: bool = True,
) -> AsyncGenerator[StreamedResponse, None]:
    """Open Pydantic AI's native streaming request over the mediated transport."""
    async with (
        httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
        ) as http,
        AsyncOpenAI(
            api_key="-".join(("tether", "transport", "placeholder")),
            organization="",
            project="",
            admin_api_key="",
            webhook_secret="",
            base_url=base_url,
            max_retries=0,
            http_client=http,
        ) as client,
    ):
        model = OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(openai_client=client),
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=output_limit_field
                == "max_completion_tokens",
                openai_supports_strict_tool_definition=False,
                openai_unsupported_model_settings=[]
                if supports_parallel_tool_calls
                else ["parallel_tool_calls"],
            ),
        )
        parameters = ModelRequestParameters(
            function_tools=[
                ToolDefinition(
                    name=t.name,
                    description=t.description,
                    parameters_json_schema=t.input_schema,
                    strict=False,
                )
                for t in request.tools
            ]
        )
        settings = OpenAIChatModelSettings(
            max_tokens=request.max_output_tokens,
            extra_body=request.extra,
        )
        if supports_parallel_tool_calls:
            settings["parallel_tool_calls"] = False
        async with model.request_stream(
            to_sdk_messages(request.messages, request.system), settings, parameters
        ) as streamed:
            yield streamed
