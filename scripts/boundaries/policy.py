"""Named import owners from architecture.md; no runtime dependency on Tether."""

from __future__ import annotations

import sys
from dataclasses import dataclass

PROVIDER_EXTRAS = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude_agent_sdk": "claude-agent",
    "google.genai": "google",
    "google.generativeai": "google",
    "vertexai": "google",
    "mistralai": "mistral",
    "cohere": "cohere",
    "groq": "groq",
}
LAYERS: dict[str, tuple[str, ...]] = {
    "tether": ("tether.types", "tether.ports", "tether.engine", "tether.sessions", "tether.client"),
    "tether.types": ("pydantic",),
    "tether.ports": ("tether.types",),
    "tether.models": ("tether.types", "tether.ports", "pydantic"),
    "tether.models.testing": ("tether.types", "tether.ports", "tether.models", "pydantic", "anyio"),
    "tether.engine": (
        "tether.types",
        "tether.ports",
        "tether.context",
        "tether.runtime.pydantic_ai",
        "anyio",
    ),
    "tether.runtime": ("tether.types", "tether.ports"),
    "tether.runtime.pydantic_ai": (
        "tether.types",
        "tether.ports",
        "tether.runtime",
        "tether.models",
        "pydantic_ai",
        "pydantic_graph",
        "pydantic",
        "anyio",
    ),
    "tether.context": ("tether.types", "tether.ports", "anyio"),
    "tether.sessions": ("tether.types", "tether.ports", "tether.engine", "anyio"),
    "tether.client": ("tether.types", "tether.sessions"),
    "tether.history": ("tether.types", "tether.ports", "anyio"),
    "tether.adapters": ("tether.types", "tether.ports"),
    "tether.adapters.pydantic_ai": (
        "tether.models",
        "tether.types",
        "tether.ports",
        "tether.runtime.pydantic_ai",
        "pydantic_ai",
        "pydantic",
        "httpx",
        "httpx2",
        "anyio",
    ),
    "tether.adapters.openai_compat": (
        "tether.types",
        "tether.ports",
        "tether.adapters.pydantic_ai",
    ),
    "tether.adapters.databricks": (
        "tether.types",
        "tether.ports",
        "tether.history",
        "tether.adapters.openai_compat",
        "tether.adapters.pydantic_ai",
        "databricks",
    ),
    "tether.stores": ("tether.types", "tether.ports"),
    "tether.stores.jsonl": (
        "tether.types",
        "tether.ports",
        "tether.stores.projections",
        "tether.stores.queries",
        "anyio",
    ),
    "tether.stores.postgres": (
        "tether.types",
        "tether.ports",
        "tether.stores.projections",
        "tether.stores.projection",
        "tether.stores.indexing",
        "tether.stores.usage",
        "tether.stores.queries",
        "psycopg",
        "psycopg_pool",
        "anyio",
    ),
    "tether.stores.postgres.embedded": (
        "tether.types",
        "tether.ports",
        "tether.stores.postgres.root",
        "tether.stores.postgres.source",
        "tether.stores.postgres.io",
        "anyio",
    ),
    "tether.stores.sqlite": ("tether.types", "tether.ports", "anyio"),
    "tether.adapters.otel": ("tether.types", "tether.ports", "opentelemetry"),
    "tether.scope": ("tether.types", "tether.ports"),
    "tether.guards": ("tether.types", "tether.ports"),
    "tether.testing": (
        "tether.types",
        "tether.ports",
        "tether.engine",
        "tether.sessions",
        "tether.runtime.pydantic_ai",
        "pytest",
        "anyio",
    ),
    "tether.cli": (
        "tether.client",
        "tether.sessions",
        "tether.adapters",
        "tether.stores",
        "pydantic_settings",
    ),
    "tether.tui": (
        "tether.client",
        "tether.sessions",
        "tether.adapters",
        "tether.stores",
        "pydantic_settings",
        "textual",
    ),
}


def within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def owner(module: str) -> str | None:
    matches = [p for p in LAYERS if p != "tether" and within(module, p)]
    return max(matches, key=len) if matches else ("tether" if module == "tether" else None)


def sdk_reference(name: str) -> bool:
    return any(
        within(name, p)
        for p in ("pydantic_ai", "pydantic_graph", "tether.runtime.pydantic_ai", *PROVIDER_EXTRAS)
    )


def sdk_allowed(layer: str, target: str) -> bool:
    private = any(part.startswith("_") for part in target.split(".")[1:])
    model_member = target.removeprefix("pydantic_ai.models.").split(".")[0]
    native = within(target, "pydantic_ai.providers") or (
        target.startswith("pydantic_ai.models.") and model_member[:1].islower()
    )
    return not private and not (layer == "tether.runtime.pydantic_ai" and native)


def allowed(module: str, target: str, extras: frozenset[str]) -> bool:
    layer = owner(module)
    if layer is None:
        return False
    if target.split(".")[0] in {"pydantic_ai", "pydantic_graph"} and not sdk_allowed(layer, target):
        return False
    provider = next((p for p in PROVIDER_EXTRAS if within(target, p)), None)
    if provider:
        return layer == "tether.adapters.pydantic_ai" and PROVIDER_EXTRAS[provider] in extras
    if within(target, "sqlite3"):
        return layer == "tether.stores.sqlite"
    if target.split(".")[0] in sys.stdlib_module_names:
        return True
    return (layer != "tether" and within(target, layer)) or any(
        within(target, p) for p in LAYERS[layer]
    )


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"
