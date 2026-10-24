"""Artifact negative controls cover published typing and legal metadata."""

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from check_distribution import LICENSES, inspect_artifacts


def artifacts(
    root: Path, *, typed: bool = True, licensed: bool = True, expression: bool = True
) -> None:
    names = {"tether/__init__.py": ""}
    if typed:
        names["tether/py.typed"] = ""
    if licensed:
        names.update({"tether-1.dist-info/licenses/" + name: "license" for name in LICENSES})
    names["tether-1.dist-info/METADATA"] = (
        "License-Expression: MIT OR Apache-2.0\n" if expression else ""
    )
    with zipfile.ZipFile(root / "tether-1.whl", "w") as wheel:
        for name, content in names.items():
            wheel.writestr(name, content)
    with tarfile.open(root / "tether-1.tar.gz", "w:gz") as source:
        for name in ["src/tether/py.typed", *LICENSES]:
            info = tarfile.TarInfo("tether-1/" + name)
            source.addfile(info, io.BytesIO())


def test_good_artifacts_and_ambiguous_outputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one fresh"):
        inspect_artifacts(tmp_path)
    artifacts(tmp_path)
    assert inspect_artifacts(tmp_path).name == "tether-1.whl"
    (tmp_path / "tether-2.whl").touch()
    with pytest.raises(ValueError, match="one fresh"):
        inspect_artifacts(tmp_path)


@pytest.mark.parametrize("option", ["typed", "licensed", "expression"])
def test_wheel_missing_required_metadata_fails(tmp_path: Path, option: str) -> None:
    artifacts(tmp_path, **{option: False})
    with pytest.raises(ValueError, match="wheel missing"):
        inspect_artifacts(tmp_path)


def test_source_missing_required_metadata_fails(tmp_path: Path) -> None:
    artifacts(tmp_path)
    with tarfile.open(tmp_path / "tether-1.tar.gz", "w:gz"):
        pass
    with pytest.raises(ValueError, match="source archive missing"):
        inspect_artifacts(tmp_path)
