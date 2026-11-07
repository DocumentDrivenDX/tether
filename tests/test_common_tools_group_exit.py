"""The Bash tool waits for its killed process group to exit, not only the shell.

On Linux an orphaned descendant can still be running for a moment after the
group SIGKILL, until the kernel finishes delivery and init reaps it.
"""

import asyncio

import pytest

from tether.cli import common_tools
from tether.cli.common_tools import await_group_exit

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_waits_until_the_group_is_gone(monkeypatch):
    probes: list[int] = []

    def killpg(pgid: int, sig: int) -> None:
        probes.append(sig)
        if len(probes) >= 3:
            raise ProcessLookupError

    monkeypatch.setattr(common_tools.os, "killpg", killpg)
    await await_group_exit(4242)
    assert probes == [0, 0, 0]


async def test_a_reused_group_id_owned_by_another_user_counts_as_gone(monkeypatch):
    def killpg(pgid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(common_tools.os, "killpg", killpg)
    await await_group_exit(4242)


async def test_a_group_that_never_exits_is_reported_not_assumed(monkeypatch):
    monkeypatch.setattr(common_tools.os, "killpg", lambda pgid, sig: None)
    with pytest.raises(RuntimeError, match="process_group_exit_unconfirmed"):
        await await_group_exit(4242, seconds=0.05)


async def test_real_shell_descendant_is_gone_when_stop_returns(tmp_path):
    process = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        "sleep 30 & sleep 30",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    await common_tools.stop_process(process)
    assert process.returncode is not None
    with pytest.raises(ProcessLookupError):
        common_tools.os.killpg(process.pid, 0)
