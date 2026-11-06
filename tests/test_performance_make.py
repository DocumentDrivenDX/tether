"""The reference performance target forwards its external Apps receipt safely."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
MAKE = shutil.which("make", path="/usr/bin:/bin")


def _fake_uv(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "uv"
    capture = tmp_path / "captured-argv.json"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['FAKE_UV_CAPTURE'], 'w') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n"
    )
    executable.chmod(0o755)
    return executable, capture


def _make_env(tmp_path: Path) -> dict[str, str]:
    _, capture = _fake_uv(tmp_path)
    return {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "FAKE_UV_CAPTURE": str(capture),
        "PERF_OUTPUT": str(tmp_path / "performance output"),
        "PERF_LOCK": str(tmp_path / "performance.lock"),
        "PERF_MACHINE_PROFILE": str(tmp_path / "known machine profile.json"),
    }


async def _run_make(arguments: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    if MAKE is None:
        raise RuntimeError("make_executable_unavailable")
    child = await asyncio.create_subprocess_exec(
        MAKE,
        *arguments,
        cwd=ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await child.communicate()
    return child.returncode or 0, stdout.decode(), stderr.decode()


def test_performance_target_forwards_receipt_path_as_one_literal_argument(tmp_path: Path) -> None:
    _, capture = _fake_uv(tmp_path)
    marker = tmp_path / "must-not-execute"
    receipt = f"{tmp_path}/Apps receipt ' quoted $(touch {marker}) `touch {marker}` ;.json"
    env = _make_env(tmp_path)
    env["PERF_APPS_MEASUREMENT"] = receipt
    reservation = str(tmp_path / "exclusive reservation.json")
    env["PERF_RESERVATION"] = reservation

    returncode, _stdout, stderr = asyncio.run(
        _run_make(
            ["--no-print-directory", "-f", str(MAKEFILE), "web-performance-check"],
            env,
        )
    )

    assert returncode == 0, stderr
    assert not marker.exists()
    arguments = json.loads(capture.read_text())
    assert arguments == [
        "run",
        "--locked",
        "python",
        "scripts/web_performance.py",
        "--baselines",
        "docs/helix/04-build/evidence/web-platform/performance",
        "--output",
        str(tmp_path / "performance output"),
        "--apps-measurement",
        receipt,
        "--reservation-file",
        reservation,
        "--machine-profile",
        str(tmp_path / "known machine profile.json"),
        "--lock-file",
        str(tmp_path / "performance.lock"),
    ]


def test_performance_target_runs_without_apps_observation(tmp_path: Path) -> None:
    _, capture = _fake_uv(tmp_path)
    env = _make_env(tmp_path)
    env.pop("PERF_APPS_MEASUREMENT", None)
    env["PERF_RESERVATION"] = str(tmp_path / "exclusive reservation.json")

    returncode, _stdout, stderr = asyncio.run(
        _run_make(
            ["--no-print-directory", "-f", str(MAKEFILE), "web-performance-check"],
            env,
        )
    )

    assert returncode == 0, stderr
    arguments = json.loads(capture.read_text())
    assert "--apps-measurement" not in arguments


def test_performance_target_refuses_missing_reservation_before_uv(tmp_path: Path) -> None:
    _, capture = _fake_uv(tmp_path)
    env = _make_env(tmp_path)
    env["PERF_APPS_MEASUREMENT"] = str(tmp_path / "apps measurement.json")

    returncode, _stdout, stderr = asyncio.run(
        _run_make(
            ["--no-print-directory", "-f", str(MAKEFILE), "web-performance-check"],
            env,
        )
    )

    assert returncode == 2
    assert "PERF_RESERVATION is required" in stderr
    assert not capture.exists()


def test_make_command_line_value_is_rejected_without_evaluation(tmp_path: Path) -> None:
    _, capture = _fake_uv(tmp_path)
    marker = tmp_path / "must-not-execute"
    value = f"receipt $(shell touch {marker})"
    returncode, _stdout, stderr = asyncio.run(
        _run_make(
            [
                "--no-print-directory",
                "-f",
                str(MAKEFILE),
                "web-performance-check",
                f"PERF_APPS_MEASUREMENT={value}",
            ],
            _make_env(tmp_path),
        )
    )

    assert returncode != 0
    assert "must be supplied in the environment" in stderr
    assert not marker.exists()
    assert not capture.exists()


def test_reservation_command_line_value_is_rejected_without_evaluation(tmp_path: Path) -> None:
    _, capture = _fake_uv(tmp_path)
    marker = tmp_path / "must-not-execute-reservation"
    value = f"reservation $(shell touch {marker})"
    env = _make_env(tmp_path)
    env["PERF_APPS_MEASUREMENT"] = str(tmp_path / "apps measurement.json")
    returncode, _stdout, stderr = asyncio.run(
        _run_make(
            [
                "--no-print-directory",
                "-f",
                str(MAKEFILE),
                "web-performance-check",
                f"PERF_RESERVATION={value}",
            ],
            env,
        )
    )

    assert returncode != 0
    assert "PERF_RESERVATION must be supplied in the environment" in stderr
    assert not marker.exists()
    assert not capture.exists()


def test_machine_profile_command_line_value_is_rejected_without_evaluation(tmp_path: Path) -> None:
    _, capture = _fake_uv(tmp_path)
    marker = tmp_path / "must-not-execute-machine-profile"
    value = f"profile $(shell touch {marker})"
    env = _make_env(tmp_path)
    env["PERF_APPS_MEASUREMENT"] = str(tmp_path / "apps measurement.json")
    returncode, _stdout, stderr = asyncio.run(
        _run_make(
            [
                "--no-print-directory",
                "-f",
                str(MAKEFILE),
                "web-performance-check",
                f"PERF_MACHINE_PROFILE={value}",
            ],
            env,
        )
    )

    assert returncode != 0
    assert "PERF_MACHINE_PROFILE must be supplied in the environment" in stderr
    assert not marker.exists()
    assert not capture.exists()


def test_manual_workflow_maps_reservation_machine_and_optional_apps_observation() -> None:
    workflow = (ROOT / ".github/workflows/performance.yml").read_text()
    assert "apps_measurement:" in workflow
    assert (
        "description: Optional Apps latency observation JSON; excluded from performance pass/fail"
        in workflow
    )
    assert "required: false" in workflow
    assert "type: string" in workflow
    assert "reservation_file:" in workflow
    assert "machine_profile:" in workflow
    assert "PERF_RESERVATION: ${{ inputs.reservation_file }}" in workflow
    assert "PERF_MACHINE_PROFILE: ${{ inputs.machine_profile }}" in workflow
    assert "PERF_APPS_MEASUREMENT: ${{ inputs.apps_measurement }}" in workflow
    assert "${{ runner.temp }}/tether-performance/" in workflow
    assert "include-hidden-files: true" in workflow
    assert 'make web-performance-check PERF_OUTPUT="$RUNNER_TEMP/tether-performance"' in workflow
    assert "run: make web-performance-check ${{ inputs.apps_measurement }}" not in workflow
