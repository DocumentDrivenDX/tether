"""Check the installed base closure; run after uv sync --locked."""

import json
from importlib.metadata import PackageNotFoundError

from dependency_policy import base_errors, reachable_dependencies


def main() -> int:
    try:
        packages = reachable_dependencies()
    except (PackageNotFoundError, ValueError) as error:
        print(f"Dependency audit failed: {error}")
        return 1
    errors = base_errors(packages)
    for error in errors:
        print(error)
    if not errors:
        print(json.dumps({"base_dependencies": packages, "provider_sdk_count": 0}, sort_keys=True))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
