"""Package consumer and exact migration-debt negative controls."""

import json
from pathlib import Path

import pytest
from boundaries.checker import check_tree


def write_project(root: Path, files: dict[str, str]) -> Path:
    (root / "pyproject.toml").write_text('[project]\nname="fabricated"\n')
    for name, content in {"src/tether/types.py": "", **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_api_public_facade_and_framework_imports_pass(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/tether/client/__init__.py": "class ClientProtocol: pass",
            "packages/web-api/src/tether_web_api/factory.py": (
                "from tether.client import ClientProtocol\nfrom fastapi import FastAPI"
            ),
        },
    )
    assert check_tree(root) == []


@pytest.mark.parametrize(
    "target",
    ["tether.engine.Engine", "tether.stores.jsonl.JsonlRoot", "tether_web_ui.factory.App"],
)
def test_api_cannot_import_runtime_storage_or_ui(tmp_path, target):
    root = write_project(
        tmp_path, {"packages/web-api/src/tether_web_api/routes.py": f"import {target}"}
    )
    assert any("cannot import" in str(error) for error in check_tree(root))


def test_consumer_cannot_reach_deep_public_package_implementation(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/tether/client/__init__.py": "",
            "src/tether/client/protocol.py": "class ClientProtocol: pass",
            "packages/web-api/src/tether_web_api/routes.py": (
                "from tether.client.protocol import ClientProtocol"
            ),
        },
    )
    assert any("private module" in str(error) for error in check_tree(root))


def test_carrier_public_factories_pass_but_private_factory_import_refuses(tmp_path):
    files = {
        "packages/web-api/src/tether_web_api/__init__.py": "def create_api(): pass",
        "packages/web-api/src/tether_web_api/routes.py": "",
        "packages/web-ui/src/tether_web_ui/__init__.py": "def create_static_app(): pass",
        "packages/databricks/src/tether_databricks/app.py": (
            "from tether_web_api import create_api\nfrom tether_web_ui import create_static_app"
        ),
    }
    root = write_project(tmp_path, files)
    assert check_tree(root) == []
    (root / "packages/databricks/src/tether_databricks/app.py").write_text(
        "from tether_web_api.routes import private_factory"
    )
    assert any("private module" in str(error) for error in check_tree(root))


def test_duplicate_source_namespace_ownership_refuses(tmp_path):
    root = write_project(tmp_path, {"packages/other/src/tether/types.py": ""})
    assert any("duplicate namespace owner" in str(error) for error in check_tree(root))


def test_core_cannot_import_optional_web_or_transport(tmp_path):
    root = write_project(tmp_path, {"src/tether/client/configuration.py": "import fastapi"})
    assert check_tree(root)
    (root / "src/tether/client/configuration.py").write_text("import httpx")
    assert check_tree(root)
    (root / "src/tether/client/remote").mkdir()
    (root / "src/tether/client/configuration.py").write_text("")
    (root / "src/tether/client/remote/transport.py").write_text("import httpx")
    assert check_tree(root) == []


def test_optional_remote_entrypoints_allow_httpx_without_widening_other_consumers(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/tether/client/databricks.py": "from httpx import Auth",
            "src/tether/cli/remote.py": "import httpx",
        },
    )
    assert check_tree(root) == []
    (root / "src/tether/cli/local.py").write_text("import httpx")
    assert any("local.py" in str(error) for error in check_tree(root))
    (root / "src/tether/cli/local.py").unlink()
    (root / "src/tether/client/configuration.py").write_text("import httpx")
    assert any("configuration.py" in str(error) for error in check_tree(root))


def test_optional_remote_cli_still_cannot_import_private_client_modules(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/tether/client/protocol.py": "class ClientProtocol: pass",
            "src/tether/cli/remote.py": "from tether.client.protocol import ClientProtocol",
        },
    )
    assert any("private module" in str(error) for error in check_tree(root))


def test_only_named_optional_postgres_facade_can_own_driver_composition(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/tether/client/postgres.py": "import psycopg",
            "packages/databricks/src/tether_databricks/app.py": (
                "from tether.client.postgres import PostgresResources"
            ),
        },
    )
    assert check_tree(root) == []
    (root / "src/tether/client/configuration.py").write_text("import psycopg")
    assert any("cannot import psycopg" in str(error) for error in check_tree(root))


def test_exact_debt_cannot_allow_another_target_or_file(tmp_path):
    root = write_project(tmp_path, {"src/tether/cli/legacy.py": "from tether.engine import Engine"})
    path = root / "scripts/boundaries/interface_debt.json"
    path.parent.mkdir(parents=True)
    entry = {"path": "src/tether/cli/legacy.py", "target": "tether.engine.Engine", "owner": "WP-10"}
    path.write_text(json.dumps({"entries": [entry]}))
    assert check_tree(root) == []
    (root / "src/tether/cli/new.py").write_text("from tether.engine import Engine")
    assert any("new.py" in str(error) for error in check_tree(root))
    (root / "src/tether/cli/new.py").unlink()
    (root / "src/tether/cli/legacy.py").write_text("from tether.engine import Other")
    assert any("Other" in str(error) for error in check_tree(root))


@pytest.mark.parametrize(
    "entries",
    [
        None,
        [{"owner": "unowned"}],
        [{"path": "src/tether/cli/a.py", "target": "*", "owner": "WP-10"}],
    ],
)
def test_invalid_debt_inventory_never_becomes_a_blanket_exception(tmp_path, entries):
    root = write_project(tmp_path, {})
    path = root / "scripts/boundaries/interface_debt.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"entries": entries}))
    assert any("invalid interface debt" in str(error) for error in check_tree(root))


def test_public_testkit_uses_named_composition_root_without_opening_private_consumers(tmp_path):
    root = write_project(
        tmp_path,
        {
            "src/tether/client/testing.py": (
                "from tether.client._composition.fixtures import fabricated_storage"
            ),
            "src/tether/client/_composition/fixtures.py": (
                "from tether.stores.memory import MemoryFiles"
            ),
        },
    )
    assert check_tree(root) == []
    (root / "src/tether/client/testing.py").write_text(
        "from tether.stores.memory import MemoryFiles"
    )
    assert any("cannot import" in str(error) for error in check_tree(root))
