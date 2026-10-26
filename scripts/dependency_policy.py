"""Audit reachable installed metadata, not unrelated packages or all lock extras."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import Distribution, distribution

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PROVIDER_DISTRIBUTIONS = frozenset(
    {
        "openai",
        "anthropic",
        "claude-agent-sdk",
        "google-genai",
        "google-generativeai",
        "google-cloud-aiplatform",
        "mistralai",
        "cohere",
        "groq",
        "boto3",
        "botocore",
        "huggingface-hub",
        "xai-sdk",
        "azure-ai-inference",
        "voyageai",
    }
)


def reachable_dependencies(
    root: str = "novirhiza-tether",
    *,
    extras: frozenset[str] = frozenset(),
    lookup: Callable[[str], Distribution] = distribution,
) -> dict[str, str]:
    pending = [(root, extras)]
    visited: set[tuple[str, frozenset[str]]] = set()
    packages: dict[str, str] = {}
    while pending:
        name, enabled = pending.pop()
        name = canonicalize_name(name)
        key = (name, enabled)
        if key in visited:
            continue
        visited.add(key)
        dist = lookup(name)
        packages[name] = dist.version
        for text in dist.requires or ():
            requirement = Requirement(text)
            contexts = enabled | {""}
            if requirement.marker is None or any(
                requirement.marker.evaluate({"extra": x}) for x in contexts
            ):
                child = lookup(requirement.name)
                if requirement.specifier and child.version not in requirement.specifier:
                    raise ValueError(
                        f"unsatisfied dependency: {requirement.name}{requirement.specifier}"
                    )
                pending.append((requirement.name, frozenset(requirement.extras)))
    return packages


def base_errors(packages: dict[str, str]) -> list[str]:
    forbidden = PROVIDER_DISTRIBUTIONS.intersection(packages)
    errors = [f"foundation-model provider SDK in base closure: {p}" for p in sorted(forbidden)]
    required = {"pydantic-ai-slim", "pydantic", "anyio"}
    errors.extend(
        f"selected base dependency missing: {p}" for p in sorted(required - packages.keys())
    )
    return errors
