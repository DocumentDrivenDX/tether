"""Typed subprocess policy for the installed-package gate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from process import Result


@dataclass(frozen=True)
class PackageCommandPolicy:
    deadline_seconds: float | None
    own_process_group: bool


STANDALONE_POLICY = PackageCommandPolicy(None, True)
INHERITED_POLICY = PackageCommandPolicy(None, False)
_POLICY: ContextVar[PackageCommandPolicy] = ContextVar(
    "package_command_policy", default=STANDALONE_POLICY
)
PackageRunner = Callable[..., Awaitable[Result]]


@contextmanager
def use_package_command_policy(policy: PackageCommandPolicy) -> Iterator[None]:
    token = _POLICY.set(policy)
    try:
        yield
    finally:
        _POLICY.reset(token)


async def run_package_command(runner: PackageRunner, arguments: list[str], *, cwd: Path) -> Result:
    policy = _POLICY.get()
    return await runner(
        arguments,
        cwd=cwd,
        deadline_seconds=policy.deadline_seconds,
        own_process_group=policy.own_process_group,
    )
