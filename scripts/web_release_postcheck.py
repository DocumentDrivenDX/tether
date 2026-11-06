"""Finalize current observed acceptance without rerunning an unchanged aggregate."""

import argparse
import json
import sys
from pathlib import Path

from web_closure import audit
from web_gate_receipt import AGGREGATE, verify_aggregate
from web_platform_receipt import PlatformEvidence, verify_platform


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, default=AGGREGATE)
    parser.add_argument("--profile")
    parser.add_argument("--live-receipt", type=Path)
    parser.add_argument("--app-origin")
    arguments = parser.parse_args()
    try:
        verify_aggregate(Path.cwd(), arguments.aggregate)
        verify_platform(
            Path.cwd(),
            arguments.profile,
            PlatformEvidence(arguments.live_receipt, arguments.app_origin),
        )
        result = audit(Path.cwd())
        print(json.dumps(result, indent=2))
        return bool(result["errors"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("Release postcheck refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
