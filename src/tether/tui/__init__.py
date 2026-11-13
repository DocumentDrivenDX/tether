"""Textual client UI and lazy compatibility exports for the local demo."""

from typing import TYPE_CHECKING

from ._presentation import ClientApp as ClientApp

if TYPE_CHECKING:
    from ._demo import DemoApp as DemoApp
    from ._demo import run as run


def __getattr__(name: str) -> object:
    if name == "DemoApp":
        from ._demo import DemoApp

        return DemoApp
    if name == "run":
        from ._demo import run

        return run
    raise AttributeError(name)
