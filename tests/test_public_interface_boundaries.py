"""Public constructors and client-only terminal import boundaries."""

import inspect
import subprocess
import sys

import pytest

from tether.client import Client, ClientHost


@pytest.mark.parametrize("factory_type", [Client, ClientHost])
def test_composed_types_have_factory_only_constructors(factory_type):
    assert not inspect.signature(factory_type).parameters
    with pytest.raises(TypeError, match="ClientHost"):
        factory_type()


def test_client_tui_import_does_not_load_demo_composition():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from tether.tui import ClientApp; "
            "assert 'tether.cli.demo' not in sys.modules; "
            "assert 'tether.tui._demo' not in sys.modules",
        ],
        check=True,
    )
