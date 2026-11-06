"""Artifact closure controls reject drift and source checkout escapes."""

import asyncio
import json
import sys
import zipfile
from pathlib import Path

import pytest
from check_web_packages import compare_builds
from process import run_command
from web_artifacts import (
    MATRIX,
    PROJECTS,
    archive_contents,
    installed_probe,
    inventory,
    wheel_metadata,
)


def wheel(path: Path, *, payload=b"fabricated", version="1.0") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("fabricated/__init__.py", payload)
        archive.writestr("fabricated.dist-info/METADATA", f"Name: fabricated\nVersion: {version}\n")


def test_rebuild_compares_payload_and_metadata_not_only_filename(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    wheel(first / "fabricated.whl")
    wheel(second / "fabricated.whl")
    receipt = compare_builds(first, second)
    assert isinstance(receipt["fabricated.whl"], dict)
    assert receipt["fabricated.whl"].get("normalized_contents_identical")
    wheel(second / "fabricated.whl", payload=b"changed")
    with pytest.raises(ValueError, match="rebuild_contents_differ"):
        compare_builds(first, second)


def test_missing_second_artifact_refuses_reproducibility(tmp_path):
    wheel(tmp_path / "first" / "fabricated.whl")
    with pytest.raises(ValueError, match="rebuild_artifact_inventory_changed"):
        compare_builds(tmp_path / "first", tmp_path / "second")


def test_wheel_metadata_and_inventory_cover_dependency_changes(tmp_path):
    path = tmp_path / "artifact.whl"
    wheel(path)
    assert wheel_metadata(path) == {"name": "fabricated", "version": "1.0", "requires": []}
    assert len(archive_contents(path)) == 2
    (tmp_path / "pyproject.toml").write_text("initial")
    first = inventory(tmp_path)
    (tmp_path / "pyproject.toml").write_text("dependency changed")
    assert first != inventory(tmp_path)


def test_executed_guides_and_capture_recipe_are_bound_but_receipts_are_not(tmp_path):
    selected = (
        "docs/embedding.md",
        "docs/web-api.md",
        "docs/react.md",
        "docs/remote-terminal.md",
        "docs/capture/capture_web.py",
    )
    for name in selected:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fabricated checked example")
    first = inventory(tmp_path)
    assert set(selected) <= first.keys()
    (tmp_path / selected[0]).write_text("changed executable quickstart")
    second = inventory(tmp_path)
    assert first != second
    evidence = tmp_path / "docs/helix/04-build/evidence/receipt.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("fabricated result")
    assert inventory(tmp_path) == second


def test_empty_or_ambiguous_metadata_is_refused(tmp_path):
    path = tmp_path / "invalid.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("only.py", "")
    with pytest.raises(ValueError, match="wheel_requires_one_metadata_file"):
        wheel_metadata(path)


def test_matrix_covers_each_distribution_and_existing_extras():
    selected = {project for projects, _, _ in MATRIX.values() for project in projects}
    extras = {extra for _, extras, _ in MATRIX.values() for extra in extras}
    assert selected == set(PROJECTS)
    assert extras == {"tui", "remote", "postgres", "embedded-postgres", "openai"}
    assert "novirhiza-tether" in MATRIX["ui"][2]


def test_probe_rejects_checkout_import_even_when_module_imports(tmp_path):
    (tmp_path / "tether").mkdir()
    (tmp_path / "tether/__init__.py").write_text("")
    (tmp_path / "tether/client.py").write_text("")
    script = "import sys\nsys.path.insert(0, " + repr(str(tmp_path)) + ")\n"
    script += installed_probe(("novirhiza-tether",), (), ())
    result = asyncio.run(run_command([sys.executable, "-I", "-c", script], cwd=tmp_path))
    assert result.returncode != 0
    assert "source_path_escape" in result.stderr


def test_probe_fails_unexpected_dependency(tmp_path):
    script = installed_probe((), (), ("pip",))
    # The current tool environment may omit pip: use a known installed distribution.
    script = script.replace("('pip',)", "('pydantic',)")
    result = asyncio.run(run_command([sys.executable, "-I", "-c", script], cwd=tmp_path))
    assert result.returncode != 0
    assert "unexpected_dependency:pydantic" in result.stderr


def test_probe_receipt_is_machine_readable(tmp_path):
    result = asyncio.run(
        run_command([sys.executable, "-I", "-c", installed_probe((), (), ())], cwd=tmp_path)
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["modules"] == {}


def test_distribution_checks_require_typing_namespace_and_exact_licenses(tmp_path):
    import io
    import tarfile

    from web_artifacts import distribution_checks

    names = ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE")
    for name in names:
        (tmp_path / name).write_text("Fabricated unit license " + name)
    archive = tmp_path / "core.whl"
    source = tmp_path / "core.tar.gz"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("tether/__init__.py", "")
        output.writestr("tether/py.typed", "")
        output.writestr("core.dist-info/METADATA", "Name: novirhiza-tether\nVersion: 1.0\n")
        for name in names:
            output.writestr("core.dist-info/licenses/" + name, (tmp_path / name).read_bytes())
    with tarfile.open(source, "w:gz") as output:
        for name in names:
            data = (tmp_path / name).read_bytes()
            info = tarfile.TarInfo("core-1.0/" + name)
            info.size = len(data)
            output.addfile(info, io.BytesIO(data))
    checked = distribution_checks(tmp_path, archive, source, "novirhiza-tether")
    assert checked["public_typing_marker"] is True
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("tether_web_api/escape.py", "")
    with pytest.raises(ValueError, match="namespace_leak"):
        distribution_checks(tmp_path, archive, source, "novirhiza-tether")


def test_installed_matrix_uses_exact_registry_lock_pins_and_refuses_ambiguity(tmp_path):
    from check_web_packages import locked_runtime_pins

    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname="fabricated"\nversion="1.2"\nsource={registry="https://pypi.org/simple"}\n'
    )
    assert locked_runtime_pins(tmp_path) == ["fabricated==1.2"]
    lock.write_text(
        lock.read_text()
        + '\n[[package]]\nname="fabricated"\nversion="2.0"\nsource={registry="https://pypi.org/simple"}\n'
    )
    with pytest.raises(ValueError, match="ambiguous_runtime_lock_profile"):
        locked_runtime_pins(tmp_path)
