"""Installed dependency edges include selected extras and reject missing closures."""

import pytest
from web_sbom import InstalledMetadata, installed_sbom


def package(name: str, *, requires=(), expression="") -> InstalledMetadata:
    return {
        "name": name,
        "version": "1.0",
        "requires": list(requires),
        "license_expression": expression,
        "license": "",
    }


def test_installed_extra_closure_generates_standard_components_and_edges():
    values = [
        package(
            "novirhiza-tether",
            requires=("child[pool]>=1; extra == 'postgres'",),
            expression="MIT OR Apache-2.0",
        ),
        package("child", requires=("pool>=1; extra == 'pool'",)),
        package("pool"),
    ]
    result = installed_sbom(values, ("novirhiza-tether",), ("postgres",), "fabricated")
    assert result["bomFormat"] == "CycloneDX"
    assert result["specVersion"] == "1.6"
    assert result["dependencies"] == [
        {"ref": "pkg:pypi/child@1.0", "dependsOn": ["pkg:pypi/pool@1.0"]},
        {"ref": "pkg:pypi/novirhiza-tether@1.0", "dependsOn": ["pkg:pypi/child@1.0"]},
        {"ref": "pkg:pypi/pool@1.0", "dependsOn": []},
    ]
    components = result["components"]
    assert isinstance(components, list)
    assert any(component.get("licenses") for component in components)
    base = installed_sbom(values[:1], ("novirhiza-tether",), (), "base")
    dependencies = base["dependencies"]
    assert isinstance(dependencies, list)
    assert dependencies[0]["dependsOn"] == []


@pytest.mark.parametrize("requirement", ["missing>=1", "child>=2", "child @ file:///private/tmp/x"])
def test_unresolved_or_private_installed_dependency_cannot_be_published(requirement):
    with pytest.raises(ValueError, match="sbom_"):
        installed_sbom(
            [package("novirhiza-tether", requires=(requirement,)), package("child")],
            ("novirhiza-tether",),
            (),
            "fabricated",
        )
