"""Exact existing terminal imports awaiting WP-10; never infer new exceptions."""

from __future__ import annotations

import json
from pathlib import Path


def load_debt(project: Path) -> dict[str, frozenset[str]]:
    path = project / "scripts/boundaries/interface_debt.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("interface debt requires an explicit entries list")
    result: dict[str, set[str]] = {}
    for entry in entries:
        filename, target = entry.get("path"), entry.get("target")
        if (
            entry.get("owner") != "WP-10"
            or not isinstance(filename, str)
            or not filename.startswith(("src/tether/cli/", "src/tether/tui/"))
            or ".." in Path(filename).parts
            or not filename.endswith(".py")
            or not isinstance(target, str)
            or not target
            or "*" in target
        ):
            raise ValueError("invalid or unowned interface migration debt")
        targets = result.setdefault(filename, set())
        if target in targets:
            raise ValueError("duplicate interface migration debt")
        targets.add(target)
    return {filename: frozenset(targets) for filename, targets in result.items()}
