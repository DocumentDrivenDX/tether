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

from .wire import observed_usage, validate_wire


class AttemptTransport(httpx.AsyncBaseTransport):
    """One request-local transport. Its caller owns the underlying pooled transport."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        request: ModelRequest,
        limits: ModelExecutionLimits,
        application_credential: Credential | None,
    ) -> None:
        self.inner = inner
        self.request = request
        self.limits = limits
        self.application_credential = application_credential
        self.local_error: BaseException | None = None
        self.retry_after: str | None = None
        self.response: ModelResponse | None = None
        self.receipt: ResponseReceipt | None = None
        self.attempt_count = 0

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
        identity = await recorder.reserve()
        validate_identity(self.request, identity)
        tags = self.request.tags.model_dump(mode="json") | identity.model_dump(
            mode="json", exclude_none=True
        )
        request.headers["X-Tether-Request-Tags"] = canonical(tags).decode()
        if self.request.traceparent:
            request.headers["traceparent"] = self.request.traceparent
        request.headers.pop("authorization", None)
        body = json.loads(request.content, object_pairs_hook=json_object)
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
            codec="openai-chat-text-tools-v1",
        )
        if authorization is not None:
            request.headers["authorization"] = authorization
        self.attempt_count += 1
        response = await self.inner.handle_async_request(request)
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
