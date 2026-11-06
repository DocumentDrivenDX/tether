"""CycloneDX inventory from installed metadata, with evaluated dependency edges."""

from __future__ import annotations

from collections import defaultdict
from typing import TypedDict
from urllib.parse import quote

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


class InstalledMetadata(TypedDict):
    name: str
    version: str
    requires: list[str]
    license_expression: str
    license: str


def dependency_edges(
    by_name: dict[str, InstalledMetadata], extras: tuple[str, ...]
) -> dict[str, set[str]]:
    enabled: dict[str, set[str]] = defaultdict(set)
    enabled["novirhiza-tether"].update(extras)
    changed = True
    edges: dict[str, set[str]] = defaultdict(set)
    while changed:
        changed = False
        for name, package in by_name.items():
            for raw in package["requires"]:
                dependency = Requirement(raw)
                if dependency.marker and not any(
                    dependency.marker.evaluate({"extra": extra}) for extra in ["", *enabled[name]]
                ):
                    continue
                child = canonicalize_name(dependency.name)
                if child not in by_name:
                    raise ValueError("sbom_missing_installed_dependency:" + child)
                if dependency.url:
                    raise ValueError("sbom_unqualified_direct_reference:" + child)
                if dependency.specifier and not dependency.specifier.contains(
                    by_name[child]["version"], prereleases=True
                ):
                    raise ValueError("sbom_installed_dependency_version_mismatch:" + child)
                edges[name].add(child)
                previous = len(enabled[child])
                enabled[child].update(dependency.extras)
                changed |= previous != len(enabled[child])
    return edges


def installed_sbom(
    packages: list[InstalledMetadata],
    roots: tuple[str, ...],
    extras: tuple[str, ...],
    profile: str,
) -> dict[str, object]:
    by_name: dict[str, InstalledMetadata] = {
        canonicalize_name(package["name"]): package for package in packages
    }
    if len(by_name) != len(packages) or not packages:
        raise ValueError("empty_or_duplicate_installed_components")
    for root in roots:
        if canonicalize_name(root) not in by_name:
            raise ValueError("sbom_missing_installed_root:" + root)
    edges = dependency_edges(by_name, extras)
    references = {
        name: "pkg:pypi/" + quote(name) + "@" + quote(package["version"])
        for name, package in by_name.items()
    }
    components: list[dict[str, object]] = []
    for name, package in sorted(by_name.items()):
        component: dict[str, object] = {
            "type": "library",
            "bom-ref": references[name],
            "name": name,
            "version": package["version"],
            "purl": references[name],
        }
        if package["license_expression"]:
            component["licenses"] = [{"expression": package["license_expression"]}]
        elif package["license"]:
            component["properties"] = [
                {"name": "tether:declared-license-metadata", "value": package["license"]}
            ]
        components.append(component)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "tether-installed-" + profile}},
        "components": components,
        "dependencies": [
            {
                "ref": references[name],
                "dependsOn": sorted(references[child] for child in edges[name]),
            }
            for name in sorted(by_name)
        ],
    }
