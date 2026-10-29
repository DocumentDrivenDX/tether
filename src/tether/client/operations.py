"""Owner-bound operation commands share the session service's task lifetime."""

from collections.abc import Mapping

from tether.sessions.service import SessionService
from tether.types import JsonValue
from tether.types.client import TurnHandle
from tether.types.commands import AuxiliaryCall, InvokeTool, ReconcileSettlement, SpecTransition
from tether.types.execution import Actor


class OperationClient:
    _service: SessionService
    _actor: Actor

    async def invoke_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
        *,
        request_id: str,
        spec: str | None = None,
    ) -> TurnHandle:
        return await self._service.commands.invoke(
            self._actor,
            session_id,
            InvokeTool(
                request_id=request_id, tool_name=tool_name, arguments=dict(arguments), spec=spec
            ),
        )

    async def auxiliary(
        self,
        session_id: str,
        profile: str,
        *,
        through_index: int,
        request_id: str,
        spec: str | None = None,
    ) -> TurnHandle:
        return await self._service.commands.invoke(
            self._actor,
            session_id,
            AuxiliaryCall(
                request_id=request_id, profile=profile, through_index=through_index, spec=spec
            ),
        )

    async def reconcile_settlement(
        self,
        session_id: str,
        settlement_id: str,
        *,
        request_id: str,
    ) -> TurnHandle:
        return await self._service.commands.invoke(
            self._actor,
            session_id,
            ReconcileSettlement(request_id=request_id, settlement_id=settlement_id),
        )

    async def transition_spec(
        self,
        session_id: str,
        spec: str,
        *,
        request_id: str,
        expected_spec_digest: str | None = None,
    ) -> TurnHandle:
        return await self._service.commands.invoke(
            self._actor,
            session_id,
            SpecTransition(
                request_id=request_id, spec=spec, expected_spec_digest=expected_spec_digest
            ),
        )
