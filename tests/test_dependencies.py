"""Dependency graph tests use actual local distribution metadata, without installs."""

from collections.abc import Callable
from importlib.metadata import Distribution, PackageNotFoundError, PathDistribution
from pathlib import Path

import pytest
from dependency_policy import PROVIDER_DISTRIBUTIONS, base_errors, reachable_dependencies
from packaging.utils import canonicalize_name


def metadata(root: Path, specs: dict[str, list[str]]) -> Callable[[str], Distribution]:
    packages: dict[str, Distribution] = {}
    for name, requirements in specs.items():
        folder = root / (name + ".dist-info")
        folder.mkdir(parents=True)
        lines = ["Metadata-Version: 2.3", f"Name: {name}", "Version: 1.0"]
        lines.extend("Requires-Dist: " + text for text in requirements)
        (folder / "METADATA").write_text("\n".join(lines) + "\n")
        packages[canonicalize_name(name)] = PathDistribution(folder)

    def lookup(name: str) -> Distribution:
        key = canonicalize_name(name)
        if key not in packages:
            raise PackageNotFoundError(name)
        return packages[key]

    return lookup


def baseline() -> dict[str, list[str]]:
    return {
        "novirhiza-tether": ["pydantic-ai-slim", "pydantic", "anyio"],
        "pydantic-ai-slim": ["pydantic-graph"],
        "pydantic-graph": [],
        "pydantic": [],
        "anyio": [],
    }


def test_unrelated_or_optional_installed_sdk_is_not_in_base(tmp_path: Path) -> None:
    data = baseline()
    data["novirhiza-tether"].append('openai; extra == "openai"')
    data["openai"] = []
    lookup = metadata(tmp_path, data)
    base = reachable_dependencies(lookup=lookup)
    assert not base_errors(base)
    assert "openai" not in base
    optional = reachable_dependencies(lookup=lookup, extras=frozenset({"openai"}))
    assert any("openai" in error for error in base_errors(optional))


def test_transitive_sdk_contamination_is_rejected(tmp_path: Path) -> None:
    data = baseline()
    data["pydantic-graph"] = ["anthropic"]
    data["anthropic"] = ["pydantic-ai-slim"]  # cycle terminates
    assert any(
        "anthropic" in e
        for e in base_errors(reachable_dependencies(lookup=metadata(tmp_path, data)))
    )


def test_child_extra_and_environment_marker(tmp_path: Path) -> None:
    data = baseline()
    data["pydantic-ai-slim"] = ["child[native]"]
    data["child"] = ['groq; extra == "native"', 'absent; sys_platform == "never"']
    data["groq"] = []
    assert "groq" in reachable_dependencies(lookup=metadata(tmp_path, data))


def test_missing_or_wrong_version_cannot_pass(tmp_path: Path) -> None:
    data = baseline()
    data["novirhiza-tether"].append("missing")
    with pytest.raises(PackageNotFoundError):
        reachable_dependencies(lookup=metadata(tmp_path, data))
    with pytest.raises(ValueError, match="unsatisfied"):
        reachable_dependencies(
            lookup=metadata(tmp_path / "versions", {"novirhiza-tether": ["child>=2"], "child": []})
        )
    assert base_errors({"novirhiza-tether": "1.0"})


@pytest.mark.parametrize("sdk", sorted(PROVIDER_DISTRIBUTIONS))
def test_each_named_provider_distribution_is_rejected(sdk: str) -> None:
    packages = {"pydantic-ai-slim": "1", "pydantic": "1", "anyio": "1", sdk: "1"}
    assert base_errors(packages) == [f"foundation-model provider SDK in base closure: {sdk}"]
