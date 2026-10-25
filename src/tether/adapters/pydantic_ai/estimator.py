"""Approximate complete-native-JSON BPE estimate, with explicit framing allowances."""

from __future__ import annotations

import asyncio
import json
from contextvars import Context
from importlib.metadata import version

import httpx2 as httpx

from tether.models.canonical import snapshot
from tether.runtime.pydantic_ai.attachments import ATTACHMENT_CODEC
from tether.runtime.pydantic_ai.messages import CODEC
from tether.types import JsonValue, ModelRequest
from tether.types.canonical import canonical, digest
from tether.types.context import ContextError, TokenEstimate

from .native import NATIVE_CODEC, request_native_stream, validate_payload
from .tokenizer import TokenizerProfile, local_encoding


class WireCapture(httpx.AsyncBaseTransport):
    """Private in-process serializer terminal. It cannot open a socket or reserve attempts."""

    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.body: bytes | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.body is not None or len(request.content) > self.maximum:
            raise ContextError("context_unmeasurable")
        self.body = bytes(request.content)
        body: JsonValue = json.loads(request.content)
        model_value = body.get("model") if isinstance(body, dict) else None
        model = model_value if isinstance(model_value, str) else "offline-projection"
        delta: dict[str, JsonValue] = {"role": "assistant", "content": "projection"}
        choice: dict[str, JsonValue] = {"index": 0, "delta": delta, "finish_reason": None}
        choices: list[JsonValue] = [choice]
        first: dict[str, JsonValue] = {
            "id": "offline-projection",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": choices,
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: " + canonical(first) + b"\n\n",
        )


class OpenAIWireEstimator:
    """Whole JSON includes literal settings overhead; quality is always approximate."""

    def __init__(
        self,
        *,
        tiers: dict[str, str],
        output_limit_field: str,
        profile: TokenizerProfile,
        vocabulary: bytes,
    ) -> None:
        if (
            not tiers
            or any(not key or not value for key, value in tiers.items())
            or output_limit_field not in {"max_tokens", "max_completion_tokens"}
        ):
            raise ValueError("invalid_estimator_profile")
        self._profile = TokenizerProfile.model_validate(profile.model_dump(mode="json"))
        self._encoding = local_encoding(vocabulary, self._profile)
        self._tiers, self._field = dict(tiers), output_limit_field
        binding: dict[str, JsonValue] = {
            "profile": self._profile.model_dump(mode="json"),
            "tiers": {k: v for k, v in self._tiers.items()},
            "output_limit_field": output_limit_field,
            "pydantic_ai": version("pydantic-ai-slim"),
            "openai": version("openai"),
            "tiktoken": version("tiktoken"),
            "pydantic": version("pydantic"),
            "httpx2": version("httpx2"),
            "native_codec": NATIVE_CODEC,
            "message_codec": CODEC,
            "attachment_codec": ATTACHMENT_CODEC,
            "method": "native-json-bpe-v1",
        }
        self._revision = digest(binding)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._active = 0

    def _admit(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ContextError("context_incompatible")
        if self._semaphore is None:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self._profile.max_count_workers)
        if self._active >= self._profile.max_count_operations:
            raise ContextError("context_exhausted")
        self._active += 1
        return self._semaphore

    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        # No physical identity/credential requirements apply to offline projection.
        semaphore = self._admit()
        try:
            value = snapshot(request)
            validate_payload(value, self._tiers)
            if len(canonical(value.model_dump(mode="json"))) > self._profile.max_request_bytes:
                raise ContextError("context_unmeasurable")
            capture = WireCapture(self._profile.max_request_bytes)
            async with request_native_stream(
                value,
                base_url="https://offline.invalid/v1",
                model_name=self._tiers[value.model],
                output_limit_field=self._field,
                transport=capture,
            ):
                pass
            if capture.body is None:
                raise ContextError("context_unmeasurable")
            async with semaphore:
                future = asyncio.get_running_loop().run_in_executor(
                    None, self._count_worker, capture.body
                )
                return await retained_count(future)
        except ContextError:
            raise
        except Exception:
            raise ContextError("context_unmeasurable") from None
        finally:
            self._active -= 1

    def _count_worker(self, body: bytes) -> TokenEstimate:
        return Context().run(self._count, body)

    def _count(self, body: bytes) -> TokenEstimate:
        text = body.decode("utf-8", errors="strict")
        payload = json.loads(text)
        profile = self._profile
        encoded = self._encoding.encode_ordinary(text)
        if self._encoding.decode_bytes(encoded) != body:
            raise ContextError("context_unmeasurable")
        tokens = (
            len(encoded)
            + profile.request_framing_tokens
            + profile.message_framing_tokens * len(payload["messages"])
            + profile.tool_framing_tokens * len(payload.get("tools", []))
        )
        return TokenEstimate(
            tokens=tokens,
            method="native-json-bpe-v1",
            revision=self._revision,
            quality="approximate",
        )


async def retained_count(future: asyncio.Future[TokenEstimate]) -> TokenEstimate:
    """The enclosing callback owns actual CPU work even after its waiter is abandoned."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            cancelled = True
            if future.cancelled():
                raise
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result
