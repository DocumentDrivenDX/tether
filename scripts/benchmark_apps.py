"""Public remote-read samples; deployment qualification belongs to the caller."""

from __future__ import annotations

import time
from typing import TypedDict

import httpx
from benchmark_measurements import Distribution, distribution

from tether.client.remote import RemoteClient, RemoteClientSettings


class RemoteReadSamples(TypedDict):
    samples: int
    list_limit: int
    list_items_per_page: int
    capability_handshake: Distribution
    list_page: Distribution
    capability_requests: int
    list_requests: int
    transport: str
    scope: str


async def measure_remote_reads(
    settings: RemoteClientSettings,
    *,
    http_client: httpx.AsyncClient,
    samples: int = 100,
) -> RemoteReadSamples:
    """Perform real sequential GETs without retaining payloads or owning HTTPX.

    An external executor must verify the actual Apps deployment and immutable
    bundle before and after these observations. This function does not establish
    that a URL belongs to Apps or qualify a deployment by itself.
    """
    if type(samples) is not int or samples < 1:
        raise ValueError("remote_read_sample_count_invalid")
    if str(http_client.base_url).rstrip("/") != settings.base_url.rstrip("/"):
        raise ValueError("remote_read_transport_origin_mismatch")
    handshakes: list[float] = []
    pages: list[float] = []
    client: RemoteClient | None = None
    list_items_per_page: int | None = None
    try:
        for _ in range(samples):
            if client is not None:
                await client.aclose()
                client = None
            started = time.perf_counter_ns()
            client = await RemoteClient.open(settings, http_client=http_client)
            handshakes.append((time.perf_counter_ns() - started) / 1_000_000)
        if client is None:
            raise RuntimeError("remote_read_handshake_missing")
        for _ in range(samples):
            started = time.perf_counter_ns()
            page = await client.list(limit=50)
            pages.append((time.perf_counter_ns() - started) / 1_000_000)
            count = len(page.items)
            if count > 50 or (list_items_per_page is not None and count != list_items_per_page):
                raise RuntimeError("remote_read_list_cardinality_changed")
            list_items_per_page = count
    finally:
        if client is not None:
            await client.aclose()
    return {
        "samples": samples,
        "list_limit": 50,
        "list_items_per_page": list_items_per_page if list_items_per_page is not None else 0,
        "capability_handshake": distribution(handshakes),
        "list_page": distribution(pages),
        "capability_requests": len(handshakes),
        "list_requests": len(pages),
        "transport": "borrowed persistent HTTPX; sequential public RemoteClient requests",
        "scope": (
            "Handshake includes authentication callback, ingress and typed capability decode; "
            "list includes typed page decode. No deployment binding or latency budget claim."
        ),
    }
