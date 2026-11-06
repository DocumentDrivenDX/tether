"""Independent-consumer controller checks actual copied files and observed XML.

The process double supplies fabricated metadata and reports; these tests prove
refusal/packaging policy, not installation or runtime conformance.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
import web_embedder


def consumer_fixture(root: Path) -> dict[str, Path]:
    example = root / "examples/embed"
    example.mkdir(parents=True)
    (example / "pyproject.toml").write_text('[tool.pyright]\ntypeCheckingMode = "strict"\n')
    (example / "uv.lock").write_text("unqualified source lock must not be copied")
    (example / "README.md").write_text("Fabricated independent consumer")
    (example / ".venv").mkdir()
    (example / ".venv/do-not-copy").write_text("fabricated")
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "collect_tests.py").write_text('OPTIONS = ["--collect-only", "--no-cov", "-q"]')
    (scripts / "public_test_ids.py").write_text("def validate_public_test_id(value): pass\n")
    wheels = {}
    for name in ("novirhiza-tether", "novirhiza-tether-web-api", "novirhiza-tether-web-ui"):
        wheel = root / (name + ".whl")
        wheel.write_text("fabricated wheel " + name)
        wheels[name] = wheel
    return wheels


def assert_copied_consumer(consumer, root, wheels):
    assert not (consumer / ".venv/do-not-copy").exists()
    project = (consumer / "pyproject.toml").read_text()
    assert 'include = ["src"]' in project
    assert "[tool.uv.sources]" in project
    assert str(root) not in project
    for name, wheel in wheels.items():
        assert (consumer / "wheelhouse" / wheel.name).read_bytes() == wheel.read_bytes()
        assert f'{name} = {{ path = "wheelhouse/{wheel.name}" }}' in project
    assert "--no-cov" not in (consumer / "collect_public_tests.py").read_text()
    assert (consumer / "public_test_ids.py").read_bytes() == (
        root / "scripts/public_test_ids.py"
    ).read_bytes()


@pytest.mark.parametrize("outcome", ["passed", "skipped", "failed", "empty", "unknown"])
def test_embedder_requires_exact_nonempty_observed_test_outcomes(tmp_path, outcome):
    root, work = tmp_path / "source", tmp_path / "work"
    wheels = consumer_fixture(root)
    work.mkdir()
    commands = []
    selected = tuple(wheels)

    def child(arguments, cwd):
        commands.append(arguments)
        if "lock" in arguments:
            assert not (cwd / "uv.lock").exists()
            (cwd / "uv.lock").write_text("fabricated wheel-backed resolved lock")
        elif "-c" in arguments:
            return json.dumps(
                {
                    "metadata": [
                        {
                            "name": name,
                            "version": "0.2.0.dev0",
                            "requires": [],
                            "license_expression": "MIT",
                            "license": "",
                        }
                        for name in selected
                    ]
                }
            )
        elif any(value.endswith("collect_public_tests.py") for value in arguments):
            nodes = [] if outcome == "empty" else ["tests/test_consumer.py::test_public"]
            Path(arguments[-1]).write_text(json.dumps(nodes))
        elif "pytest" in arguments:
            report = next(
                value.removeprefix("--junitxml=")
                for value in arguments
                if value.startswith("--junitxml=")
            )
            tag = {"skipped": "<skipped/>", "failed": "<failure/>"}.get(outcome, "")
            case = "different" if outcome == "unknown" else "test_public"
            Path(report).write_text(
                '<testsuites><testsuite><testcase classname="tests.test_consumer" '
                f'name="{case}">{tag}</testcase></testsuite></testsuites>'
            )
        return ""

    async def checked(arguments, cwd):
        return await asyncio.to_thread(child, arguments, cwd)

    pending = web_embedder.qualify_embedder("/fabricated/uv", root, wheels, work, checked)
    if outcome != "passed":
        expected = {
            "empty": "independent_embedder_has_no_tests",
            "unknown": "unknown_or_duplicate_observed_test",
        }.get(outcome, "independent_embedder_required_cases_not_passed")
        with pytest.raises(ValueError, match=expected):
            asyncio.run(pending)
        return
    result = asyncio.run(pending)
    consumer = work / "independent-embedder"
    assert_copied_consumer(consumer, root, wheels)
    assert result["lock_sha256"] == web_embedder.digest((consumer / "uv.lock").read_bytes())
    assert result["test_results"] == [
        {"node_id": "examples/embed/tests/test_consumer.py::test_public", "outcome": "passed"}
    ]
    python = consumer / ".venv/bin/python"
    assert [
        sys.executable,
        "-m",
        "pyright",
        "--pythonpath",
        str(python),
        "--project",
        str(consumer),
    ] in commands
    assert result["strict_installed_typing"] is True
    assert result["sbom"]["bomFormat"] == "CycloneDX"


def test_embedder_install_failure_cannot_run_tests_or_emit_result(tmp_path):
    root, work = tmp_path / "source", tmp_path / "work"
    wheels = consumer_fixture(root)
    work.mkdir()
    commands = []

    async def checked(arguments, cwd):
        commands.append(arguments)
        if "sync" in arguments:
            raise ValueError("fabricated_dependency_solver_refusal")
        return ""

    with pytest.raises(ValueError, match="fabricated_dependency_solver_refusal"):
        asyncio.run(web_embedder.qualify_embedder("/fabricated/uv", root, wheels, work, checked))
    assert len(commands) == 2
    assert "lock" in commands[0]
    assert "sync" in commands[1]
