"""Negative controls for the architecture gate, using fabricated source trees."""

import ast
from pathlib import Path

import pytest
from boundaries.checker import check_tree, inspect_module
from boundaries.imports import qualified
from boundaries.policy import allowed, owner
from hypothesis import given
from hypothesis import strategies as st


def project(root: Path, files: dict[str, str], extras: bool = False) -> Path:
    root.mkdir(exist_ok=True)
    metadata = '[project]\nname="fabricated"\n'
    if extras:
        metadata += '[project.optional-dependencies]\nopenai=["openai"]\n'
    (root / "pyproject.toml").write_text(metadata)
    for name, source in files.items():
        path = root / "src" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


@pytest.mark.parametrize(
    "source",
    [
        "import openai",
        "def f():\n import anthropic",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n import openai",
        "from pydantic_ai import Agent",
        "import httpx2",
        "import tiktoken",
        "import sqlite3",
        "import importlib as loader\nloader.import_module('openai')",
        "from importlib import import_module as load\nload('anthropic')",
        "__import__('openai')",
        "__import__(name)",
        "from pydantic_ai import *",
        "from tether.sessions import Store",
    ],
)
def test_core_forbidden_imports(tmp_path: Path, source: str) -> None:
    root = project(tmp_path, {"tether/types.py": source})
    assert check_tree(root)


@pytest.mark.parametrize("target", ["pydantic_ai._agent_graph", "pydantic_graph._graph"])
def test_private_graph_imports_even_in_owner(tmp_path: Path, target: str) -> None:
    root = project(tmp_path, {"tether/runtime/pydantic_ai/bridge.py": f"import {target}"})
    assert check_tree(root)


def test_selected_runtime_allowed_and_provider_requires_extra(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "tether/runtime/pydantic_ai/bridge.py": "from pydantic_ai import Agent",
            "tether/adapters/pydantic_ai/native.py": "import openai",
        },
    )
    assert any("openai" in str(p) for p in check_tree(root))
    project(root, {}, extras=True)
    assert not check_tree(root)
    assert not allowed("tether.runtime.pydantic_ai", "pydantic_ai.models.openai", frozenset())
    assert not allowed("tether.runtime.pydantic_ai", "pydantic_ai.providers", frozenset())


def test_relative_cycle_and_function_local_edge(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "tether/engine.py": "def f():\n from .runtime.pydantic_ai import bridge",
            "tether/runtime/pydantic_ai/bridge.py": "from ...engine import Engine",
        },
    )
    errors = check_tree(root)
    assert any("cycle" in str(p) for p in errors)
    assert any("cannot import tether.engine" in str(p) for p in errors)


def test_public_type_cannot_expose_internal_sdk_owner(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "tether/engine.py": (
                'import tether.runtime.pydantic_ai as rt\ndef expose() -> "rt.Agent": ...'
            )
        },
    )
    assert any("public annotation" in str(p) for p in check_tree(root))


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"tether/unknown.py": ""},
        {"tether/types.py": "not python !"},
        {"tether/types.py": "from ...outside import data"},
    ],
)
def test_missing_unknown_or_unparseable_source_fails_closed(
    tmp_path: Path, files: dict[str, str]
) -> None:
    assert check_tree(project(tmp_path, files))


def test_literal_and_forward_annotations_are_distinct(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "tether/types.py": (
                'from typing import Literal\ndef text(value: Literal["two words"]) -> str: ...'
            )
        },
    )
    assert not check_tree(root)


@given(
    st.sampled_from(["tether.engine", "tether.types", "tether.client"]),
    st.text(alphabet="abc", min_size=1, max_size=8),
)
def test_new_submodule_cannot_change_owner_permission(module: str, suffix: str) -> None:
    assert owner(module + "." + suffix) == module
    assert not allowed(module + "." + suffix, "openai", frozenset({"openai"}))


def test_package_relative_stdlib_and_self_edges(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "tether/__init__.py": "from . import ports",
            "tether/ports.py": "from .types import Value\nimport typing",
            "tether/types.py": "from pydantic import BaseModel",
        },
    )
    assert not check_tree(root)
    assert qualified(ast.Constant(value=3), {}) == ""
    file = root / "src/tether/types.py"
    assert inspect_module(file, root / "src", frozenset())[0] == []


@pytest.mark.parametrize("provider", ["openai", "anthropic", "google", "mistral", "groq"])
def test_runtime_cannot_own_native_provider_model(tmp_path: Path, provider: str) -> None:
    root = project(
        tmp_path,
        {
            "tether/runtime/pydantic_ai/bridge.py": f"import pydantic_ai.models.{provider}",
        },
    )
    assert check_tree(root)


def test_package_root_cannot_reexport_internal_sdk_owner(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {
            "tether/__init__.py": "from tether.runtime.pydantic_ai import Agent",
        },
    )
    assert check_tree(root)
