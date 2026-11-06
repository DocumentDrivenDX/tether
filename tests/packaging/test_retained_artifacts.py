"""Private capture retention is explicit and never reuses existing user files."""

from pathlib import Path

import pytest
from check_web_packages import workspace
from web_quality import package_command


def test_default_artifact_workspace_cleans_up(tmp_path):
    with workspace(tmp_path, None) as temporary:
        directory = Path(temporary)
        assert directory.is_dir()
    assert not directory.exists()


def test_explicit_workspace_retains_exact_installed_entrypoint_for_capture(tmp_path):
    root, retained = tmp_path / "checkout", tmp_path / "private-artifacts"
    root.mkdir()
    with workspace(root, retained) as temporary:
        assert temporary == str(retained)
        (retained / "fabricated-wheel").write_text("fabricated artifact")
    assert (retained / "fabricated-wheel").read_text() == "fabricated artifact"
    assert package_command(retained)[-2:] == ["--retain-workdir", str(retained)]
    with pytest.raises(FileExistsError):
        workspace(root, retained)


def test_retention_cannot_pollute_checkout_or_overwrite_its_content(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    with pytest.raises(ValueError, match="outside_checkout"):
        workspace(root, root / "retained")
    assert not (root / "retained").exists()
