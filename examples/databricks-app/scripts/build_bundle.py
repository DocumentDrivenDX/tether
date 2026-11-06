"""Build a self-contained bundle directory with the Tether Python wheels."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIRS = (
    ROOT,
    ROOT / "packages" / "web-api",
    ROOT / "packages" / "web-ui",
    ROOT / "packages" / "databricks",
)


def build(output: Path) -> None:
    destination = output.expanduser().resolve()
    if destination.exists():
        raise FileExistsError("output_directory_must_not_exist")
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        raise RuntimeError("uv_executable_unavailable")
    wheelhouse = destination / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    for package in PACKAGE_DIRS:
        subprocess.run(  # noqa: S603
            [uv_executable, "build", "--wheel", "--out-dir", str(wheelhouse)],
            cwd=package,
            check=True,
        )

    source = ROOT / "examples" / "databricks-app"
    shutil.copy2(source / "app.yaml", destination / "app.yaml")
    shutil.copy2(source / "databricks.yml", destination / "databricks.yml")
    shutil.copytree(source / "src", destination / "src")
    wheels = {wheel.name.split("-")[0]: wheel.name for wheel in wheelhouse.glob("*.whl")}
    required = (
        ("novirhiza_tether", "postgres"),
        ("novirhiza_tether_web_api", None),
        ("novirhiza_tether_web_ui", None),
        ("novirhiza_tether_databricks", None),
    )
    missing = [name for name, _ in required if name not in wheels]
    if missing:
        raise RuntimeError("required_tether_wheels_missing")
    source_lock = tomllib.loads((source / "uv.lock").read_text())
    source_project = tomllib.loads((source / "pyproject.toml").read_text())
    project_version = source_project["project"]["version"]
    locked_versions = {
        package["name"]: package["version"]
        for package in source_lock["package"]
        if "version" in package
    }
    project_dependencies = [f"{name}[{extra}]" if extra else name for name, extra in required]
    project_dependencies.extend(
        (
            f"databricks-sdk=={locked_versions['databricks-sdk']}",
            f"hypercorn=={locked_versions['hypercorn']}",
        )
    )
    project = [
        "[project]",
        'name = "tether-databricks-app-bundle"',
        f'version = "{project_version}"',
        'requires-python = ">=3.11"',
        "dependencies = [",
        *[f'  "{dependency}",' for dependency in project_dependencies],
        "]",
        "",
        "[tool.uv.sources]",
    ]
    for name, _ in required:
        project.append(f'{name} = {{ path = "wheelhouse/{wheels[name]}" }}')
    (destination / "pyproject.toml").write_text("\n".join(project) + "\n")
    subprocess.run([uv_executable, "lock"], cwd=destination, check=True)  # noqa: S603


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    build(arguments.output)


if __name__ == "__main__":
    main()
