"""Independent settlement never infers a successful external write from journal delivery."""

import pytest
from test_consumer_commands import composition
from test_gated_execution import DemoTool

from tether.testing.settlement import MemorySettlement
from tether.types.execution import GuardVerdict, SettlementStage, ToolOutput

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StagedTool(DemoTool):
    async def run(self, ctx, args):
        await super().run(ctx, args)
        return ToolOutput(
            text="Fabricated staged result",
            settlement=SettlementStage(participant="fabricated", stage_id=ctx.invocation_id),
        )


class Withhold:
    id = "fabricated_withhold"

    async def check(self, value, session):
        return GuardVerdict(outcome="withheld")


@pytest.mark.parametrize("withheld", [False, True])
async def test_clean_result_commits_and_withheld_result_rolls_back(tmp_path, withheld):
    participant = MemorySettlement()
    async with composition(
        tmp_path, StagedTool(), settlements=(participant,), guards=(Withhold(),) if withheld else ()
    ) as (client, _app, _model):
        session = await client.create()
        result = await (
            await client.invoke_tool(session.id, "write", {"count": 1}, request_id="write")
        ).result()
        assert result.stop_reason == "final"
        binding = next(iter(participant.bindings.values()))
        assert binding.decision == ("rollback" if withheld else "commit")
        assert participant.calls == 1
        assert not (await client.effects(session.id)).items
        assert result.messages[0].is_error is withheld


async def test_lost_participant_ack_blocks_new_effect_and_reconciles_same_id_after_reopen(tmp_path):
    participant, tool = MemorySettlement(), StagedTool()
    participant.lose_acknowledgment = True
    async with composition(tmp_path, tool, settlements=(participant,)) as (client, _app, _model):
        session = await client.create()
        result = await (
            await client.invoke_tool(session.id, "write", {"count": 1}, request_id="write")
        ).result()
        assert result.stop_reason == "tool_denied"
        assert result.reason == "settlement_uncertain"
        assert (await client.effects(session.id)).items
        binding = next(iter(participant.bindings.values()))
        with pytest.raises(Exception, match="effect_unresolved"):
            await client.invoke_tool(session.id, "write", {"count": 2}, request_id="other")
        assert len(tool.calls) == 1
    async with composition(tmp_path, tool, settlements=(participant,)) as (client, _app, _model):
        handle = await client.reconcile_settlement(
            session.id, binding.settlement_id, request_id="resolve"
        )
        assert (await handle.result()).stop_reason == "final"
        assert participant.calls == 1
        assert not (await client.effects(session.id)).items
        await (
            await client.reconcile_settlement(
                session.id, binding.settlement_id, request_id="resolve-again"
            )
        ).result()
        assert participant.calls == 1
