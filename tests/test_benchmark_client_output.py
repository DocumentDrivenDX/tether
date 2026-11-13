"""Bounded spawn-to-first-byte controls for the actual CLI measurement path."""

import asyncio
import os
import sys
from pathlib import Path

import benchmark_client_environment as child_env
import benchmark_client_support as support
import pytest
import selected_profiles_process
from context_lifetime_controls import wait_event_or_task
from process_lifetime_controls import ShieldCancellationObserver, unarmed_timeouts


@pytest.mark.skipif(os.name != "posix", reason="console-script shebang control is POSIX-only")
def test_first_output_refuses_no_output_and_nonzero_cli(tmp_path: Path) -> None:
    executable = tmp_path / "tether-failing"
    executable.write_text(f"#!{sys.executable}\nraise SystemExit(7)\n", encoding="utf-8")
    executable.chmod(0o700)
    with pytest.raises(RuntimeError, match="benchmark_tether_no_first_output"):
        asyncio.run(support._first_output_sample(executable, tmp_path, 3))

    executable.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write('x')\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    with pytest.raises(RuntimeError, match="benchmark_tether_list_failed"):
        asyncio.run(support._first_output_sample(executable, tmp_path, 3))


def test_first_output_requires_at_least_100_samples(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="benchmark_first_output_limits_invalid"):
        asyncio.run(support.first_output(tmp_path, samples=99))


def test_child_environment_is_allowlisted_and_pins_checkout_source() -> None:
    child = support._child_environment(
        {
            "PATH": "/fabricated/bin:/usr/bin",
            "HOME": "/fabricated/home",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": "/fabricated/tmp",
            "DATABRICKS_HOST": "fabricated-host",
            "DATABRICKS_CLIENT_SECRET": "fabricated-secret",
            "PGPASSWORD": "fabricated-password",
            "AWS_ACCESS_KEY_ID": "fabricated-key",
            "PYTHONPATH": "/fabricated/foreign-source",
            "VIRTUAL_ENV": "/fabricated/foreign-venv",
        }
    )
    assert child["PATH"] == "/fabricated/bin:/usr/bin"
    assert child["HOME"] == "/fabricated/home"
    assert child["LC_ALL"] == "C.UTF-8"
    assert child["TMPDIR"] == "/fabricated/tmp"
    assert child["PYTHONPATH"] == str(child_env.SOURCE_ROOT)
    assert child["PYTHONDONTWRITEBYTECODE"] == "1"
    assert not any(
        key.startswith(("DATABRICKS_", "PG", "AWS_")) or key == "VIRTUAL_ENV" for key in child
    )
    assert "fabricated-secret" not in " ".join(child.values())
    with pytest.raises(ValueError, match="benchmark_child_environment_override_invalid"):
        support._child_environment({}, {"DATABRICKS_HOST": "fabricated-host"})


@pytest.mark.skipif(os.name != "posix", reason="console script shebang is POSIX-only")
def test_tether_console_script_requires_exact_active_venv_shebang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "tether"
    expected = Path(sys.executable).absolute()
    executable.write_text(f"#!{expected}\npass\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(child_env.shutil, "which", lambda name, path: str(executable))
    assert child_env.find_verified_tether_executable({"PATH": str(tmp_path)}) == executable

    foreign = tmp_path / "foreign-python"
    executable.write_text(f"#!{foreign}\npass\n", encoding="utf-8")
    executable.chmod(0o700)
    with pytest.raises(RuntimeError, match="benchmark_tether_interpreter_mismatch"):
        child_env.find_verified_tether_executable({"PATH": str(tmp_path)})


def test_first_output_sample_retains_process_cleanup_after_repeated_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        original = asyncio.create_subprocess_exec
        spawn_entered = asyncio.Event()
        release_spawn = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []
        observer = ShieldCancellationObserver(2)
        observer.install(selected_profiles_process, monkeypatch)
        executable = tmp_path / "tether-held-spawn"
        executable.write_text(
            f"#!{sys.executable}\nimport sys\nsys.stdout.write('fabricated\\n')\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)

        async def delayed_spawn(*args, **kwargs):
            spawn_entered.set()
            await release_spawn.wait()
            child = await original(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(child_env.asyncio, "create_subprocess_exec", delayed_spawn)
        with unarmed_timeouts(support) as selected_timeouts:
            task = asyncio.create_task(support._first_output_sample(executable, tmp_path, 3))
            observer.target = task
            try:
                await wait_event_or_task(spawn_entered, task)
                task.cancel()
                await wait_event_or_task(observer.acknowledged[0], task)
                task.cancel()
                await wait_event_or_task(observer.acknowledged[1], task)
                release_spawn.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert selected_timeouts == [3]
            finally:
                release_spawn.set()
                await asyncio.gather(task, return_exceptions=True)
        assert len(children) == 1
        assert children[0].returncode is not None

    asyncio.run(run())


def test_clean_capture_waits_through_repeated_cancellation_during_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        original = asyncio.create_subprocess_exec
        spawn_entered = asyncio.Event()
        release_spawn = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []
        observer = ShieldCancellationObserver(2)
        observer.install(selected_profiles_process, monkeypatch)

        async def blocked_spawn(*args, **kwargs):
            spawn_entered.set()
            await release_spawn.wait()
            child = await original(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(child_env.asyncio, "create_subprocess_exec", blocked_spawn)
        environment = child_env.child_environment({"PATH": os.defpath, "HOME": str(Path.home())})
        with unarmed_timeouts(child_env) as selected_timeouts:
            task = asyncio.create_task(
                child_env.run_clean_capture(
                    (sys.executable, "-c", "pass"), environment=environment, timeout_seconds=2
                )
            )
            observer.target = task
            try:
                await wait_event_or_task(spawn_entered, task)
                task.cancel()
                await wait_event_or_task(observer.acknowledged[0], task)
                task.cancel()
                await wait_event_or_task(observer.acknowledged[1], task)
                release_spawn.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert selected_timeouts == [2]
            finally:
                release_spawn.set()
                await asyncio.gather(task, return_exceptions=True)
        assert len(children) == 1
        assert children[0].returncode is not None

    asyncio.run(run())


def test_clean_capture_reaps_process_returned_during_timed_out_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        original = asyncio.create_subprocess_exec
        spawn_entered = asyncio.Event()
        release_spawn = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []

        async def delayed_spawn(*args, **kwargs):
            spawn_entered.set()
            await release_spawn.wait()
            child = await original(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(child_env.asyncio, "create_subprocess_exec", delayed_spawn)
        environment = child_env.child_environment({"PATH": os.defpath, "HOME": str(Path.home())})
        capture = asyncio.create_task(
            child_env.run_clean_capture(
                (sys.executable, "-c", "import time; time.sleep(10)"),
                environment=environment,
                timeout_seconds=0.05,
            )
        )
        await spawn_entered.wait()
        assert not capture.done()
        release_spawn.set()
        with pytest.raises(TimeoutError):
            await capture
        assert len(children) == 1
        assert children[0].returncode is not None

    asyncio.run(run())


@pytest.mark.skipif(os.name != "posix", reason="PTY process-group control is POSIX-only")
def test_textual_first_paint_cancellation_stops_inherited_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        original_spawn = asyncio.create_subprocess_exec
        original_add_reader = asyncio.get_running_loop().add_reader
        spawned = asyncio.Event()
        registered = asyncio.Event()
        children: list[asyncio.subprocess.Process] = []

        async def observe_spawn(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            assert os.getpgid(child.pid) == os.getpgrp()
            spawned.set()
            return child

        def observe_reader(*args, **kwargs):
            result = original_add_reader(*args, **kwargs)
            registered.set()
            return result

        monkeypatch.setattr(support.asyncio, "create_subprocess_exec", observe_spawn)
        monkeypatch.setattr(asyncio.get_running_loop(), "add_reader", observe_reader)
        task = asyncio.create_task(support._textual_paint_sample(support._child_environment({})))
        await spawned.wait()
        await registered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(children) == 1
        assert children[0].returncode is not None

    asyncio.run(run())
