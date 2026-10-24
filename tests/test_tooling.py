"""Exercise gate failures with real files, Git baselines and bounded subprocesses."""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest
from check_ceilings import check, compare, disables_shape, suppressed, values
from check_file_size import check_sizes
from process import run_command

ROOT = Path(__file__).resolve().parents[1]


def test_caps_reject_increases_and_invalid_values() -> None:
    config = (ROOT / "pyproject.toml").read_text()
    assert not compare(None, config)
    assert compare(config, config.replace("max-complexity = 10", "max-complexity = 11"))
    assert compare(config.replace("max-complexity = 10", "max-complexity = 9"), config)
    for changed in ("true", "0", '"ten"'):
        with pytest.raises(ValueError, match="invalid ceiling"):
            values(config.replace("max-complexity = 10", "max-complexity = " + changed))
    with pytest.raises(KeyError):
        values("")


def test_global_line_caps_include_spikes_and_skip_environments(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.tether.ceilings]\nsource_lines=2\ntest_lines=3\n"
    )
    for name in ("src/module.py", "docs/spike/probe.py", "tests/test_probe.py", ".venv/no.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pass\n" * 4)
    problems = check_sizes(tmp_path)
    assert len(problems) == 3
    assert any("docs/spike" in p for p in problems)


def test_git_baseline_and_suppression_failures(tmp_path: Path) -> None:
    async def scenario() -> None:
        git = shutil.which("git")
        assert git
        await run_command([git, "init", str(tmp_path)])
        config = (ROOT / "pyproject.toml").read_text()
        path = tmp_path / "pyproject.toml"
        path.write_text(config)
        await run_command([git, "add", "."], cwd=tmp_path)
        result = await run_command(
            [
                git,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "baseline",
            ],
            cwd=tmp_path,
        )
        assert result.returncode == 0
        assert not await check(tmp_path)
        assert await check(tmp_path, "absent-ref")
        path.write_text(config.replace('"C901", "PLR",', '"C901",'))
        assert "shape rules removed" in " ".join(await check(tmp_path))
        path.write_text(config.replace('ignore = ["PLR2004"', 'ignore = ["PLR0912", "PLR2004"'))
        assert "disabled" in " ".join(await check(tmp_path))
        path.write_text(config)
        (tmp_path / "src").mkdir()
        (tmp_path / "src/bad.py").write_text("pass  # " + "no" + "qa: C901\n")
        assert "suppression" in " ".join(await check(tmp_path))
        path.write_text("")
        assert "invalid ceiling" in " ".join(await check(tmp_path))

    asyncio.run(scenario())


def test_process_capture_exit_timeout_and_validation() -> None:
    async def scenario() -> None:
        result = await run_command([sys.executable, "-c", "print('ok'); raise SystemExit(3)"])
        assert result.returncode == 3
        assert result.stdout == "ok\n"
        assert not result.stderr
        assert (await run_command([sys.executable, "-c", "pass"], capture=False)).returncode == 0
        with pytest.raises(ValueError, match="absolute"):
            await run_command(["python"])
        with pytest.raises(TimeoutError):
            await run_command(
                [sys.executable, "-c", "import asyncio; asyncio.run(asyncio.sleep(20))"],
                deadline_seconds=0.02,
            )

    asyncio.run(scenario())


def test_typing_and_environment_negative_controls(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "bad.py"
        source.write_text("def bad(value: int) -> str:\n    return value\n")
        result = await run_command([sys.executable, "-m", "pyright", str(source)])
        assert result.returncode != 0
        assert "reportReturnType" in result.stdout
        source.write_text("import os\nvalue = os.getenv('SECRET')\n")
        result = await run_command(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--config",
                str(ROOT / "pyproject.toml"),
                "--no-force-exclude",
                str(source),
            ]
        )
        assert result.returncode != 0
        assert "banned-api" in result.stdout

    asyncio.run(scenario())


@pytest.mark.parametrize("code", ["ALL", "C", "C90", "C901", "PL", "PLR", "PLR0912"])
def test_broad_shape_ignores_are_rejected(code: str) -> None:
    assert disables_shape(code)
    assert suppressed("pass  # " + "no" + "qa: " + code)


def test_comments_only_and_blanket_suppressions() -> None:
    assert suppressed("pass  # " + "no" + "qa")
    assert not suppressed('text = "' + "no" + 'qa: C901"')
    assert not suppressed("pass # " + "no" + "qa: F401")
