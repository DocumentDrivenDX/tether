"""Model-port helpers shared by callers and contract test doubles."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from tether.ports import ModelPort
from tether.types import ModelRequest, ModelResponse, ModelUnavailable, StreamItem


@asynccontextmanager
async def managed(stream: AsyncIterator[StreamItem]) -> AsyncGenerator[AsyncIterator[StreamItem]]:
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


def valid_item(item: object) -> bool:
    return isinstance(item, StreamItem)


async def checked_stream(port: ModelPort, request: ModelRequest) -> AsyncIterator[StreamItem]:
    """Verify stream framing, including data after a terminal response."""
    final = False
    async with managed(port.stream(request)) as stream:
        async for item in stream:
            if final or not valid_item(item):
                raise ModelUnavailable("invalid_response")
            final = isinstance(item, ModelResponse)
            yield item
    if not final:
        raise ModelUnavailable("incomplete_stream")


async def complete(port: ModelPort, request: ModelRequest) -> ModelResponse:
    final: ModelResponse | None = None
    async with managed(checked_stream(port, request)) as stream:
        async for item in stream:
            if isinstance(item, ModelResponse):
                final = item
    if final is None:
        raise ModelUnavailable("incomplete_stream")
    return final
