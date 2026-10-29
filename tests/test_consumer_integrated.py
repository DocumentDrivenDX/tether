"""The public fabricated example exercises all consumer seams in one composition."""

import runpy
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_fabricated_public_client_composes_all_new_capabilities(tmp_path):
    example = runpy.run_path(
        str(Path(__file__).parents[1] / "examples" / "consumer_capabilities.py")
    )
    reports = []

    async def report(value):
        reports.append(value)

    receipt = await example["exercise"](tmp_path, report)
    assert receipt["model_calls"] == 3
    assert receipt["staged_writes"] == 1
    assert receipt["preparations"] == 1
    assert len(reports) == 4
