"""Collect exact pytest node ids through its public plugin API."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


class Inventory:
    def __init__(self, destination: Path) -> None:
        self.destination = destination

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.destination.write_text(
            json.dumps([item.nodeid for item in session.items]), encoding="utf-8"
        )


def main() -> int:
    return int(
        pytest.main(["--collect-only", "-q", "--no-cov"], plugins=[Inventory(Path(sys.argv[1]))])
    )


if __name__ == "__main__":
    raise SystemExit(main())
