"""Collect exact pytest node ids through its public plugin API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from public_test_ids import validate_public_test_id


def _validated_ids(items: list[pytest.Item]) -> list[str]:
    nodes = [item.nodeid for item in items]
    try:
        for node in nodes:
            validate_public_test_id(node)
    except ValueError:
        pytest.exit("test_id_contains_credential_shape", returncode=1)
    return nodes


class Inventory:
    def __init__(self, destination: Path) -> None:
        self.destination = destination

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        _validated_ids(items)

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        # pytest calls this in finally even when modifyitems exits. Validate
        # before our destination write and the default terminal reporter.
        nodes = _validated_ids(session.items)
        self.destination.write_text(json.dumps(nodes), encoding="utf-8")


def main() -> int:
    return int(
        pytest.main(
            ["--collect-only", "-q", "--no-cov", *sys.argv[2:]],
            plugins=[Inventory(Path(sys.argv[1]))],
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
