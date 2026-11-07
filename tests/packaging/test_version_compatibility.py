"""Published-artifact refusal requires a genuine exact-cohort solver conflict."""

import asyncio

import pytest
import web_compatibility
from process import Result


@pytest.mark.parametrize(
    ("code", "output", "passed"),
    [
        (0, "", False),
        (1, "DNS network unavailable", False),
        (1, "No solution found: fastapi missing", False),
        (1, "No solution found novirhiza-tether==0.3.1 requirements are unsatisfiable", True),
    ],
)
def test_only_expected_old_core_constraint_refusal_is_evidence(
    tmp_path, monkeypatch, code, output, passed
):
    calls = []
    solved = []

    async def checked(arguments, _cwd):
        calls.append(arguments)
        return ""

    async def run(arguments, **_kwargs):
        assert _kwargs == {
            "cwd": tmp_path,
            "deadline_seconds": None,
            "own_process_group": True,
        }
        assert "--dry-run" in arguments
        assert arguments[-1] in {web_compatibility.OLD_CORE, web_compatibility.PUBLISHED_CORE}
        solved.append(arguments[-1])
        return Result(code, "", output)

    monkeypatch.setattr(web_compatibility, "run_command", run)
    monkeypatch.setattr(
        web_compatibility,
        "_installed_core_version",
        lambda environment: "0.1.0" if "0.1.0" in environment.name else "0.2.0",
    )
    pending = web_compatibility.refuse_old_core(
        "/fabricated/uv", tmp_path / "candidate.whl", tmp_path, checked
    )
    if passed:
        result = asyncio.run(pending)
        assert result["candidate_api_refused"]
        assert result["installed_old_core"] == "0.1.0"
        assert [item["version"] for item in result["published_core_rejections"]] == [
            "0.1.0",
            "0.2.0",
        ]
        assert len(result["test_results"]) == 2
        assert solved == [web_compatibility.OLD_CORE, web_compatibility.PUBLISHED_CORE]
        assert calls[1][-1].endswith(
            "#sha256=df427a4c9660edd8712219c10fd1390134b1fc5b26e3427f72c76ae58adb8fd4"
        )
        assert calls[3][-1].endswith(
            "#sha256=d275bfe8ec54b893077277aee4cddd4a59ff20c9bc2f800a13e32b2de6a61c99"
        )
        # Inspect installed metadata with the trusted parent, never a potentially
        # executable .pth or startup module from the unqualified distribution.
        assert all("-c" not in call for call in calls)
    else:
        with pytest.raises(ValueError, match="expected_old_core_version_conflict"):
            asyncio.run(pending)


def test_published_stable_core_failure_cannot_inherit_the_older_core_pass(tmp_path, monkeypatch):
    async def checked(_arguments, _cwd):
        return ""

    async def run(arguments, **_kwargs):
        if arguments[-1] == web_compatibility.OLD_CORE:
            return Result(
                1,
                "",
                "No solution found novirhiza-tether==0.3.1 requirements are unsatisfiable",
            )
        return Result(1, "", "DNS network unavailable")

    monkeypatch.setattr(web_compatibility, "run_command", run)
    monkeypatch.setattr(
        web_compatibility,
        "_installed_core_version",
        lambda environment: "0.1.0" if "0.1.0" in environment.name else "0.2.0",
    )
    with pytest.raises(ValueError, match="expected_old_core_version_conflict"):
        asyncio.run(
            web_compatibility.refuse_old_core(
                "/fabricated/uv", tmp_path / "candidate.whl", tmp_path, checked
            )
        )


def test_installed_public_core_version_is_read_without_loading_package_code(tmp_path):
    info = tmp_path / "lib" / "site-packages" / "novirhiza_tether-0.2.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Name: novirhiza-tether\nVersion: 0.2.0\n")
    (info.parent / "fabricated.pth").write_text("raise RuntimeError('must never execute')\n")
    assert web_compatibility._installed_core_version(tmp_path) == "0.2.0"


def test_installed_public_core_ambiguous_metadata_is_refused(tmp_path):
    for version in ("0.1.0", "0.2.0"):
        info = tmp_path / ("novirhiza_tether-" + version + ".dist-info")
        info.mkdir()
        (info / "METADATA").write_text("Name: novirhiza-tether\nVersion: " + version + "\n")
    with pytest.raises(ValueError, match="published_core_installed_metadata_missing"):
        web_compatibility._installed_core_version(tmp_path)


@pytest.mark.parametrize(
    "project",
    [
        "pyproject.toml",
        "packages/web-api/pyproject.toml",
        "packages/web-ui/pyproject.toml",
        "packages/databricks/pyproject.toml",
        "examples/embed/pyproject.toml",
        "examples/otel/pyproject.toml",
        "examples/databricks-app/pyproject.toml",
    ],
)
def test_internal_candidate_dependencies_require_the_exact_tested_cohort(project):
    import tomllib
    from pathlib import Path

    from packaging.requirements import Requirement

    root = Path(__file__).resolve().parents[2]
    metadata = tomllib.loads((root / project).read_text())
    assert metadata["project"]["version"] == "0.3.1"
    dependencies = list(metadata["project"].get("dependencies", []))
    for values in metadata["project"].get("optional-dependencies", {}).values():
        dependencies.extend(values)
    for values in metadata.get("dependency-groups", {}).values():
        dependencies.extend(item for item in values if isinstance(item, str))
    for value in dependencies:
        requirement = Requirement(value)
        if requirement.name.startswith("novirhiza-tether"):
            assert str(requirement.specifier) == "==0.3.1"
