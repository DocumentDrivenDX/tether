"""The Playwright-selected port must reach the loopback fixture server."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

import pytest

SERVER_DIRECTORY = Path(__file__).parent / "ui"
SERVER_PATH = SERVER_DIRECTORY / "server.py"


def _browser_port_parser(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(SERVER_DIRECTORY))
    spec = importlib.util.spec_from_file_location("tether_browser_fixture_server", SERVER_PATH)
    if spec is None or spec.loader is None:
        pytest.fail("browser_fixture_server_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module).browser_port


def test_fixture_uses_harness_selected_port_without_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse_port = _browser_port_parser(monkeypatch)
    assert parse_port({"TETHER_BROWSER_PORT": "41237"}) == 41237
    with pytest.raises(RuntimeError, match="browser_fixture_port_required"):
        parse_port({})


def test_fixture_rejects_malformed_or_out_of_range_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parse_port = _browser_port_parser(monkeypatch)
    for value in ("not-a-port", "0", "65536"):
        with pytest.raises(RuntimeError, match="browser_fixture_port_invalid"):
            parse_port({"TETHER_BROWSER_PORT": value})
