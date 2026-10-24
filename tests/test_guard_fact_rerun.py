"""Provider redaction must not erase tool facts used by subsequent guard passes."""

import pytest
from test_gated_execution import DemoProvider, DemoTool, Harness

from tether.types.execution import GuardVerdict, ToolOutput

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_after_hook_rerun_preserves_tool_facts_for_every_guard(limits):
    class Tool(DemoTool):
        async def run(self, ctx, args):
            return ToolOutput(text="Original", facts={"fabricated_source": True})

    class Guard:
        id = "fabricated"

        def __init__(self):
            self.seen = []

        async def check(self, item, session):
            self.seen.append(item.output)
            assert item.facts == item.output.facts
            denied = item.output.text == "redacted" and item.facts.get("fabricated_source") is True
            return GuardVerdict(outcome="withheld" if denied else "ok")

    guard, provider = Guard(), DemoProvider()
    provider.redact = True
    h = Harness(limits, tool=Tool(), guards=(guard,), providers=(provider,))
    try:
        assert (await h.run()).stop_reason == "final"
        assert len(guard.seen) == 2
        assert all(item.facts == {"fabricated_source": True} for item in guard.seen)
        assert h.model.requests[-1].messages[-1].is_error
        assert h.model.requests[-1].messages[-1].content == "result_withheld"
    finally:
        assert await h.engine.aclose()
