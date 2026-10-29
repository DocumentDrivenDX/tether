"""Mediated native SDK transport: reserve, render, start, egress, observe."""

from __future__ import annotations

import json
from base64 import b64encode

import httpx2 as httpx
from pydantic import JsonValue

from tether.models.attempts import validate_identity
from tether.models.canonical import canonical, digest
from tether.runtime.pydantic_ai.messages import json_object
from tether.types import (
    AttemptIdentity,
    AttemptRecorder,
    Credential,
    ModelAttemptDenied,
    ModelExecutionLimits,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    ResponseReceipt,
)

from .native import NATIVE_CODEC
from .wire import StreamingWireValidator, observed_usage, validate_wire


class CapturedStream(httpx.AsyncByteStream):
    def __init__(
        self,
        response: httpx.Response,
        transport: AttemptTransport,
        validator: StreamingWireValidator,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        recorder: AttemptRecorder,
        status_code: int,
    ) -> None:
        self.response, self.transport, self.validator = response, transport, validator
        self.identity, self.receipt, self.recorder = identity, receipt, recorder
        self.status_code = status_code
        self.data = bytearray()
        self.completed = False

    async def __aiter__(self):
        if self.completed:
            return
        try:
            async for chunk in self.response.aiter_bytes():
                self.validator.feed(chunk)
                self.data.extend(chunk)
                if self.validator.result is not None and not self.completed:
                    await self._complete(self.validator.result)
                yield chunk
            if not self.completed:
                await self._complete(self.validator.finish())
        except Exception as error:
            if self.transport.local_error is None:
                if isinstance(error, ModelUnavailable):
                    self.transport.local_error = error
                elif isinstance(error, httpx.TransportError):
                    self.transport.local_error = ModelUnavailable("transport_error")
                else:
                    self.transport.local_error = ModelUnavailable("invalid_response")
            raise httpx.StreamError("invalid_stream") from error
        finally:
            await self.response.aclose()

    async def aclose(self) -> None:
        await self.response.aclose()

    async def _complete(self, result: ModelResponse) -> None:
        try:
            await self.transport.complete_stream(
                result,
                bytes(self.data),
                self.identity,
                self.receipt,
                self.recorder,
                self.status_code,
            )
        except Exception as error:
            self.transport.local_error = error
            raise
        self.completed = True


class AttemptTransport(httpx.AsyncBaseTransport):
    """One request-local transport. Its caller owns the underlying pooled transport."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        request: ModelRequest,
        limits: ModelExecutionLimits,
        application_credential: Credential | None,
        *,
        request_tags_header: str = "X-Tether-Request-Tags",
    ) -> None:
        self.request_tags_header = request_tags_header
        self.inner = inner
        self.request = request
        self.limits = limits
        self.application_credential = application_credential
        self.local_error: BaseException | None = None
        self.retry_after: str | None = None
        self.response: ModelResponse | None = None
        self.receipt: ResponseReceipt | None = None
        self.attempt_count = 0
        self.identity: AttemptIdentity | None = None
        self.wire_model: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._handle(request)
        except httpx.TransportError:
            raise
        except BaseException as error:
            self.local_error = error
            raise

    async def _authorization(self) -> str | None:
        mode = self.request.identity_mode
        credential = self.request.credential if mode == "caller" else self.application_credential
        if mode == "anonymous":
            return None
        if credential is None:
            raise ModelUnavailable("missing_credential")
        try:
            value = await credential.authorization()
        except Exception:
            raise ModelUnavailable("identity_unavailable") from None
        if not isinstance(value, str) or not value.strip():
            raise ModelUnavailable("missing_credential")
        if not value.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ModelUnavailable("identity_unavailable")
        return value

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        recorder = self.request.attempts
        if recorder is None or self.attempt_count >= 1:
            raise ModelAttemptDenied("attempt_limit")
        request.headers = httpx.Headers(
            {
                "host": request.url.netloc.decode("ascii"),
                "content-type": "application/json",
                "content-length": str(len(request.content)),
                "accept": "application/json",
                "accept-encoding": "identity",
            }
        )
        identity = await recorder.reserve()
        validate_identity(self.request, identity)
        tags = self.request.tags.model_dump(mode="json") | identity.model_dump(
            mode="json", exclude_none=True
        )
        header_tags: dict[str, JsonValue] = tags
        if self.request_tags_header == "Databricks-Ai-Gateway-Request-Tags":
            header_tags = {key: str(value) for key, value in tags.items() if value is not None}
        request.headers[self.request_tags_header] = canonical(header_tags).decode()
        if self.request.traceparent:
            request.headers["traceparent"] = self.request.traceparent
        request.headers.pop("authorization", None)
        body_value: JsonValue = json.loads(request.content, object_pairs_hook=json_object)
        if not isinstance(body_value, dict):
            raise ModelUnavailable("invalid_response")
        body = body_value
        model_value = body.get("model")
        self.wire_model = model_value if isinstance(model_value, str) else None
        wire: JsonValue = dict(
            method=request.method,
            url=str(request.url),
            body=body,
            tags=tags,
            traceparent=self.request.traceparent,
        )
        authorization = await self._authorization()
        await recorder.start(
            identity,
            request_digest=digest(wire),
            wire_request=wire,
            codec=NATIVE_CODEC,
        )
        self.identity = identity.model_copy(deep=True)
        if authorization is not None:
            request.headers["authorization"] = authorization
        self.attempt_count += 1
        response = await self.inner.handle_async_request(request)
        if body.get("stream") is True and 200 <= response.status_code < 300:
            return await self._stream_response(response, request, identity, recorder)
        try:
            data = await self._capture(response, identity, str(request.url), recorder)
            headers = dict(response.headers)
            for name in ("content-encoding", "content-length", "transfer-encoding"):
                headers.pop(name, None)
            return httpx.Response(
                response.status_code, headers=headers, content=data, request=request
            )
        finally:
            await response.aclose()

    async def _stream_response(
        self,
        response: httpx.Response,
        request: httpx.Request,
        identity: AttemptIdentity,
        recorder: AttemptRecorder,
    ) -> httpx.Response:
        receipt = ResponseReceipt(
            provider="openai-compatible",
            endpoint=str(request.url),
            schema_profile="openai-chat-text-tools-v1",
            provider_request_id=response.headers.get("x-request-id"),
            request_id_source=("header" if response.headers.get("x-request-id") else "none"),
        )
        self.receipt = receipt
        self.retry_after = response.headers.get("retry-after")
        if response.headers.get("content-encoding", "identity").lower().strip() not in {
            "",
            "identity",
        }:
            await response.aclose()
            raise ModelUnavailable("unsupported_content_encoding", receipt=receipt)
        if "text/event-stream" not in response.headers.get("content-type", "").lower():
            try:
                await self._capture(response, identity, str(request.url), recorder)
                if self.response is None:
                    raise ModelUnavailable("invalid_response", receipt=receipt)
                payload = synthetic_stream(self.response, self.wire_model or self.request.model)
                if len(payload) > self.limits.max_response_bytes:
                    raise ModelUnavailable("response_too_large", receipt=receipt)
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=payload,
                    request=request,
                )
            finally:
                await response.aclose()
        await recorder.observe(identity, receipt)
        headers = dict(response.headers)
        for name in ("content-encoding", "content-length", "transfer-encoding"):
            headers.pop(name, None)
        stream = CapturedStream(
            response,
            self,
            StreamingWireValidator(self.request.tools, self.limits),
            identity,
            receipt,
            recorder,
            response.status_code,
        )
        return httpx.Response(response.status_code, headers=headers, stream=stream, request=request)

    async def complete_stream(
        self,
        response: ModelResponse,
        data: bytes,
        identity: AttemptIdentity,
        receipt: ResponseReceipt,
        recorder: AttemptRecorder,
        status_code: int,
    ) -> None:
        receipt = receipt.model_copy(update={"response_id": response.request_id})
        self.receipt = receipt
        self.response = response.model_copy(
            update={"receipt": receipt, "request_id": receipt.provider_request_id}
        )
        await recorder.observe(
            identity,
            receipt,
            wire_response=wire_observation(status_code, data),
            usage=response.usage,
        )

    async def _capture(
        self,
        response: httpx.Response,
        identity: AttemptIdentity,
        endpoint: str,
        recorder: AttemptRecorder,
    ) -> bytes:
        receipt = ResponseReceipt(
            provider="openai-compatible",
            endpoint=endpoint,
            schema_profile="openai-chat-text-tools-v1",
            provider_request_id=response.headers.get("x-request-id"),
            request_id_source=("header" if response.headers.get("x-request-id") else "none"),
        )
        self.receipt = receipt
        self.retry_after = response.headers.get("retry-after")
        await recorder.observe(identity, receipt)
        if response.headers.get("content-encoding", "identity").lower().strip() not in {
            "",
            "identity",
        }:
            raise ModelUnavailable(
                "unsupported_content_encoding", receipt.provider_request_id, receipt
            )
        data = await self._read_bounded(response)
        if response.status_code < 200 or response.status_code >= 300:
            await recorder.observe(
                identity,
                receipt,
                wire_response=wire_observation(response.status_code, data),
                usage=observed_usage(data),
            )
            raise ModelUnavailable(
                f"http {response.status_code}", receipt.provider_request_id, receipt
            )
        try:
            normalized = validate_wire(data, self.request.tools, self.limits)
        except ModelUnavailable as error:
            await recorder.observe(
                identity,
                receipt,
                wire_response=wire_observation(response.status_code, data),
                usage=observed_usage(data),
            )
            raise ModelUnavailable(error.reason, receipt.provider_request_id, receipt) from None
        receipt = receipt.model_copy(update={"response_id": normalized.request_id})
        self.receipt = receipt
        self.response = normalized.model_copy(
            update={"receipt": receipt, "request_id": receipt.provider_request_id}
        )
        await recorder.observe(
            identity,
            receipt,
            wire_response=wire_observation(response.status_code, data),
            usage=normalized.usage,
        )
        return data

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        data = bytearray()
        async for chunk in response.aiter_bytes():
            if len(data) + len(chunk) > self.limits.max_response_bytes:
                raise ModelUnavailable("response_too_large", receipt=self.receipt)
            data.extend(chunk)
        return bytes(data)


def wire_observation(status: int, data: bytes) -> JsonValue:
    try:
        body: JsonValue = json.loads(data, object_pairs_hook=json_object)
        canonical(body)
    except (ValueError, UnicodeError):
        body = {"raw_base64": b64encode(data).decode("ascii")}
    observation: dict[str, JsonValue] = {"status": status, "body": body}
    return observation


def synthetic_stream(response: ModelResponse, model: str) -> bytes:
    """Adapt a server that ignored stream=true to a single validated SDK frame."""
    identifier = (
        response.receipt.response_id
        if response.receipt is not None and response.receipt.response_id
        else response.request_id or "tether-stream-fallback"
    )
    delta: dict[str, JsonValue] = {"role": "assistant"}
    if response.content:
        delta["content"] = response.content
    if response.tool_calls:
        delta["tool_calls"] = [
            {
                "index": index,
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": canonical(call.arguments).decode("utf-8"),
                },
            }
            for index, call in enumerate(response.tool_calls)
        ]
    finish = {
        "stop": "stop",
        "tool_calls": "tool_calls",
        "length": "length",
        "filtered": "content_filter",
        "unknown": "unknown",
    }[response.finish_reason]
    chunks: list[JsonValue] = [
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        },
        {
            "id": identifier,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        },
    ]
    if response.usage is not None:
        usage: dict[str, JsonValue] = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }
        prompt_details: dict[str, JsonValue] = {}
        completion_details: dict[str, JsonValue] = {}
        if response.usage.cache_read_tokens is not None:
            prompt_details["cached_tokens"] = response.usage.cache_read_tokens
        if response.usage.cache_write_tokens is not None:
            prompt_details["cache_write_tokens"] = response.usage.cache_write_tokens
        if response.usage.reasoning_tokens is not None:
            completion_details["reasoning_tokens"] = response.usage.reasoning_tokens
        if prompt_details:
            usage["prompt_tokens_details"] = prompt_details
        if completion_details:
            usage["completion_tokens_details"] = completion_details
        chunks.append(
            {
                "id": identifier,
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [],
                "usage": usage,
            }
        )
    return (
        b"".join(b"data: " + canonical(chunk) + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"
    )
