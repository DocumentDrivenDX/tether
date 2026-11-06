"""Fail release when any required criterion lacks exact observed passing evidence."""

import argparse
import json
from pathlib import Path

from web_closure import audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    try:
        result = audit(Path.cwd(), inventory_only=args.inventory_only)
    except (OSError, ValueError, TypeError) as error:
        print("Acceptance closure refused: " + str(error))
        return 1
    print(json.dumps(result, indent=2))
    return bool(result["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
