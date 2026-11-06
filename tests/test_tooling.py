"""Exercise gate failures with real files, Git baselines and bounded subprocesses."""

import asyncio
import contextlib
import os
import shutil
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import process as process_module
import pytest
from check_ceilings import check, compare, disables_shape, suppressed, values
from check_file_size import SKIP_DIRS, check_sizes, python_files
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


def test_size_and_shape_scan_prunes_dependency_trees_before_reading_them(tmp_path, monkeypatch):
    for name in SKIP_DIRS:
        directory = tmp_path / "packages" / name
        directory.mkdir(parents=True)
        (directory / "ignored.py").write_text("pass\n")
    source = tmp_path / "packages" / "owned" / "module.py"
    source.parent.mkdir()
    source.write_text("pass\n")
    original = os.scandir

    def scanned(path):
        assert Path(path).name not in SKIP_DIRS
        return original(path)

    monkeypatch.setattr(os, "scandir", scanned)
    assert list(python_files(tmp_path)) == [source]


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


def test_process_capture_exit_and_validation() -> None:
    async def scenario() -> None:
        result = await run_command([sys.executable, "-c", "print('ok'); raise SystemExit(3)"])
        assert result.returncode == 3
        assert result.stdout == "ok\n"
        assert not result.stderr
        assert (await run_command([sys.executable, "-c", "pass"], capture=False)).returncode == 0
        with pytest.raises(ValueError, match="absolute"):
            await run_command(["python"])
        for deadline in (0, float("inf"), float("nan")):
            with pytest.raises(ValueError, match="finite and positive"):
                await run_command([sys.executable, "-c", "pass"], deadline_seconds=deadline)

    asyncio.run(scenario())


def test_owned_watchdog_waits_for_barrier_then_reaps_descendant_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        wait_started = asyncio.Event()
        descendant_started = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGUSR1, descendant_started.set)
        spawned: list[int] = []
        original_spawn = process_module._spawn_command

        async def recording_spawn(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            spawned.append(child.pid)
            return child

        async def held_watchdog(streams, deadline):
            wait_started.set()
            await descendant_started.wait()
            return set()

        monkeypatch.setattr(process_module, "_wait_for_streams", held_watchdog)
        monkeypatch.setattr(process_module, "_spawn_command", recording_spawn)
        descendant = (
            "import asyncio, os, signal, sys; "
            "os.kill(int(sys.argv[1]), signal.SIGUSR1); "
            "asyncio.run(asyncio.Event().wait())"
        )
        child = (
            "import asyncio, os, subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {descendant!r}, str(os.getppid())]); "
            "asyncio.run(asyncio.Event().wait())"
        )
        task: asyncio.Task[process_module.Result] | None = None
        try:
            task = asyncio.create_task(
                run_command(
                    [sys.executable, "-c", child],
                    deadline_seconds=1800,
                    own_process_group=True,
                )
            )
            await wait_started.wait()
            with pytest.raises(process_module.CommandIncompleteError) as error:
                await task
        finally:
            if task is not None and not task.done():
                task.cancel()
                while not task.done():
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.shield(task)
                if not task.cancelled():
                    task.result()
            loop.remove_signal_handler(signal.SIGUSR1)
        assert (error.value.reason, error.value.phase) == ("watchdog_exhausted", "execution")
        assert len(spawned) == 1
        with pytest.raises(ProcessLookupError):
            os.killpg(spawned[0], 0)

    asyncio.run(scenario())


def test_stream_capture_failure_still_reaps_owned_child_and_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        wait_started = asyncio.Event()
        release_wait = asyncio.Event()
        group_exists = True
        signals: list[int] = []

        def fake_killpg(pgid: int, sig: int) -> None:
            assert pgid == 432109
            signals.append(sig)
            if sig == 0 and not group_exists:
                raise ProcessLookupError

        async def child_wait() -> int:
            nonlocal group_exists
            wait_started.set()
            await release_wait.wait()
            group_exists = False
            return 0

        async def failed_streams() -> tuple[bytes | None, bytes | None]:
            raise OSError("fabricated stream failure")

        child = cast(
            asyncio.subprocess.Process,
            SimpleNamespace(pid=432109, returncode=0, wait=child_wait),
        )
        streams = asyncio.create_task(failed_streams())
        monkeypatch.setattr(process_module.os, "killpg", fake_killpg)
        cleanup = asyncio.create_task(process_module._cleanup(child, streams, True))
        await wait_started.wait()
        assert not cleanup.done()
        release_wait.set()
        with pytest.raises(process_module.CommandIncompleteError) as error:
            await cleanup
        assert (error.value.reason, error.value.phase) == ("cleanup_unconfirmed", "cleanup")
        assert signals[0] == signal.SIGKILL
        assert 0 in signals

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
