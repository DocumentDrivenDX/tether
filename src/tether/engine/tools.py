"""Ordered tool gates. No SDK type or tool scheduling enters this module."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import uuid4

from tether.context.provenance import compatible
from tether.types import Message, ToolCall
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    AgentSpec,
    ApprovalBinding,
    CallManifest,
    Entry,
    ExecutionDenied,
    GuardInput,
    PendingConfirmation,
    Provider,
    Refusal,
    ResultGuard,
    RetrievalDenied,
    Tool,
    ToolContext,
    ToolOutput,
    copy_actor,
)
from tether.types.tooling import Normalized, definition, normalize

from .edits import executable as edited_arguments
from .effects import unresolved_invocations, unresolved_settlements
from .governance import Governance, PolicyDenied, reason_code
from .observers.metadata import Scope
from .providers import provider_hook
from .registry import PinnedTool as PinnedTool
from .registry import Registry as Registry
from .results import prepare_result
from .settlement import finish as settle_result
from .workers import Workers


@dataclass(frozen=True)
class Invocation:
    source: Entry
    manifest: CallManifest
    id: str


class ToolExecutor:
    def __init__(
        self,
        gov: Governance,
        registry: Registry,
        spec: AgentSpec,
        workers: Workers,
        guards: tuple[ResultGuard, ...],
        *,
        current_registry: Callable[[], Registry] | None = None,
    ) -> None:
        self.gov, self.registry, self.spec = gov, registry, spec
        self.workers, self.guards = workers, guards
        self.current_registry = (
            current_registry if current_registry is not None else lambda: registry
        )
        self.calls: dict[str, Invocation] = {}
        self.pending: dict[str, PendingConfirmation] = {}
        self.contexts: dict[str, ToolContext] = {}
        self.replays: dict[str, Entry] = {}

    def bind(self, source: Entry) -> None:
        self.calls = {
            m.call_id: Invocation(
                source, m, digest([self.gov.log.session.id, source.index, m.position])
            )
            for m in source.call_manifest
        }

    def unresolved(self, recovering: str | None = None) -> bool:
        ids = set(unresolved_invocations(self.gov.log.entries))
        if recovering is not None and recovering in self.replays:
            ids.discard(recovering)
        return bool(ids)

    async def execute(self, call: ToolCall) -> Message | None:
        if self.pending:
            return None
        inv = self.calls.get(call.id)
        if inv is None or call.name != inv.manifest.tool:
            raise ExecutionDenied("invalid_invocation_binding")
        existing = next((e for e in self.gov.log.entries if e.key == "result:" + inv.id), None)
        if existing is not None:
            return await self.gov.read(existing.history_ref, source_index=existing.index)
        try:
            pinned = self.registry.tools[call.name]
            normalized = await self._gates(inv, call, pinned)
            if await self._approval(inv, pinned.tool, normalized):
                return None
        except (TimeoutError, ValueError, TypeError):
            return await self.result(inv, "denied", "validation_unavailable")
        except ExecutionDenied as error:
            return await self.result(
                inv,
                "invalid_arguments" if error.reason == "invalid_arguments" else "denied",
                error.reason,
            )
        return await self._dispatch(inv, pinned.tool, normalized)

    async def _gates(self, inv: Invocation, call: ToolCall, pinned: PinnedTool) -> Normalized:
        if inv.manifest.effect == "effectful" and unresolved_settlements(self.gov.log.entries):
            raise ExecutionDenied("settlement_unresolved")
        if inv.manifest.effect == "effectful" and self.unresolved(inv.id):
            await self.gov.fact(
                "authorization",
                action="call_tool",
                resource=call.name,
                allowed=False,
                reason="effect_unresolved",
                invocation_id=inv.id,
            )
            raise ExecutionDenied("effect_unresolved")
        await self.gov.authorize(
            "call_tool",
            call.name,
            requires=pinned.tool.requires,
            permitted=frozenset(self.spec.tools),
        )
        if (
            call.name not in self.spec.tools
            or digest(definition(pinned.tool)) != inv.manifest.tool_digest
            or digest(definition(pinned.original)) != inv.manifest.tool_digest
        ):
            raise ExecutionDenied("definition_changed")
        source = await self.gov.read(inv.source.history_ref, source_index=inv.source.index)
        proposal = source.tool_calls[inv.manifest.position]
        if proposal != call or digest(proposal.arguments) != inv.manifest.args_digest:
            raise ExecutionDenied("raw_proposal_changed")
        async with asyncio.timeout(self.gov.limits.hook_timeout_seconds):
            normalized = await self.workers.call(
                self.gov.log.session.id,
                normalize,
                pinned.tool,
                proposal.arguments,
                self.gov.limits.max_argument_bytes,
            )
        edited = await edited_arguments(
            self.gov,
            self.workers,
            pinned.tool,
            inv.id,
            inv.source,
            inv.manifest,
            proposal.arguments,
        )
        if edited is not None:
            normalized = edited
        executable = call.model_copy(update={"arguments": normalized.arguments}, deep=True)
        for provider in self.registry.providers:
            result = await self._hook(
                provider, "before_call", self.gov.log.session, self.spec, executable
            )
            if result is not None:
                if not isinstance(result, Refusal):
                    raise ExecutionDenied(f"x.{provider.id}.provider_error")
                raise ExecutionDenied(self._provider_reason(provider, result.reason))
        self.contexts[inv.id] = await self._context(inv)
        return normalized

    async def _context(self, inv: Invocation) -> ToolContext:
        capabilities: dict[type, object] = {}
        for provider in self.registry.providers:
            supplied = await self._hook(
                provider, "capabilities", self.gov.actor, self.gov.log.session
            )
            if not isinstance(supplied, Mapping):
                raise ExecutionDenied("provider_output_invalid")
            supplied = cast(Mapping[object, object], supplied)
            if any(not isinstance(k, type) or k in capabilities for k in supplied):
                raise ExecutionDenied("provider_output_invalid")
            capabilities.update(cast(Mapping[type, object], supplied))

        async def retrieve(resource: str, fetch: Callable[[], object]) -> object:
            return await self._read(inv, resource, fetch)

        return ToolContext(
            actor=copy_actor(self.gov.actor),
            invocation_id=inv.id,
            scope=self.gov.log.session.held,
            capabilities=capabilities,
            retrieve=retrieve,
        )

    async def _read(self, inv: Invocation, resource: str, fetch: Callable[[], object]) -> object:
        try:
            accepted = await self.gov.authorize("retrieve", resource)
        except PolicyDenied as error:
            raise RetrievalDenied(error.reason) from None
        with self.gov.observers.steps.retrieval(
            self.gov.scope,
            invocation_id=inv.id,
            source_position=inv.source.index,
            source_turn_id=inv.source.turn_id,
            authorization_position=accepted.index,
            resource=resource,
        ):
            return await self.workers.call(self.gov.log.session.id, fetch)

    async def _approval(self, inv: Invocation, tool: Tool, args: Normalized) -> bool:
        binding = ApprovalBinding(
            session_id=self.gov.log.session.id,
            invocation_id=inv.id,
            args_digest=args.sha256,
            tool_digest=inv.manifest.tool_digest,
            spec_digest=digest(self.spec.model_dump(mode="json")),
            actor_id=self.gov.actor.id,
        )
        edited = next((e for e in self.gov.log.entries if e.key == "approval_edit:" + inv.id), None)
        if edited is not None:
            binding = binding.model_copy(
                update={
                    "proposal_digest": edited.facts.get("proposal_digest"),
                    "shown_digest": edited.facts.get("shown_digest"),
                    "executable_digest": args.sha256,
                }
            )
        answered = next((e for e in self.gov.log.entries if e.key == "approval:" + inv.id), None)
        if answered is not None:
            current = self.current_registry().tools.get(tool.name)
            if current is None or current.sha256 != inv.manifest.tool_digest:
                raise ExecutionDenied("approval_invalid")
            if answered.facts.get("binding") != binding.model_dump(mode="json"):
                raise ExecutionDenied("approval_invalid")
            state = answered.facts.get("state")
            if state != "approved":
                raise ExecutionDenied(str(state))
            return False
        asks = False
        if tool.always_ask:
            async with asyncio.timeout(self.gov.limits.hook_timeout_seconds):
                asks = await self.workers.call(
                    self.gov.log.session.id,
                    tool.asks_first,
                    self.contexts[inv.id],
                    args.value.model_copy(deep=True),
                )
                if type(asks) is not bool:
                    raise ExecutionDenied("approval_invalid")
        if self.gov.log.session.approval != "manual" and not asks:
            return False
        if inv.source.facts.get("origin") == "client":
            raise ExecutionDenied("approval_required")
        now = self.gov.clock()
        ttl = self.gov.limits.approval_ttl_seconds
        pending = PendingConfirmation(
            **binding.model_dump(),
            tool=tool.name,
            arguments=args.arguments,
            description=tool.description,
            history_ref=inv.source.history_ref,
            requested_at=now,
            expires_at=now + timedelta(seconds=ttl) if ttl else None,
        )
        await self.gov.fact(
            "confirmation",
            key="pending:" + inv.id,
            state="requested",
            binding=binding.model_dump(mode="json"),
            expires_at=pending.expires_at.isoformat() if pending.expires_at else None,
        )
        self.pending[inv.id] = pending
        return True

    async def _dispatch(self, inv: Invocation, tool: Tool, args: Normalized) -> Message:
        existing_intent = next(
            (e for e in self.gov.log.entries if e.key == "intent:" + inv.id), None
        )
        if existing_intent is not None:
            await self._replay(inv, tool, args, existing_intent)
        needs_intent = tool.effect == "effectful" or tool.replay == "unsafe"
        if needs_intent and existing_intent is None:
            existing_intent = await self.gov.fact(
                "tool_call",
                key="intent:" + inv.id,
                source_turn_id=inv.source.turn_id,
                status="started",
                invocation_id=inv.id,
                tool=tool.name,
                call_id=inv.manifest.call_id,
                effect=tool.effect,
                replay=tool.replay,
                args_digest=args.sha256,
                tool_digest=inv.manifest.tool_digest,
                spec_digest=digest(self.spec.model_dump(mode="json")),
                approval_index=next(
                    (e.index for e in self.gov.log.entries if e.key == "approval:" + inv.id), None
                ),
            )
        observation = self.gov.observers.worker(
            scope=Scope(
                owner=self.gov.log.session.owner,
                session_id=self.gov.log.session.id,
                principal=self.gov.actor.principal or self.gov.actor.id,
                phase_turn_id=self.gov.log.turn_id or self.gov.turn_id,
                spec_id=self.spec.id,
                spec_version=self.spec.version,
            ),
            source_turn_id=inv.source.turn_id,
            source_position=inv.source.index,
            call_position=inv.manifest.position,
            invocation_id=inv.id,
            intent_position=existing_intent.index if existing_intent is not None else None,
        )
        # Nothing may catch or normalize a failed intent append into dispatch permission.
        try:
            async with asyncio.timeout(
                tool.timeout_seconds or self.gov.limits.tool_timeout_seconds
            ):
                value = await self.workers.call(
                    self.gov.log.session.id,
                    tool.run,
                    self.contexts[inv.id],
                    args.value.model_copy(deep=True),
                    observe=observation,
                )
            output = (
                ToolOutput.model_validate(value.model_dump(mode="json"))
                if isinstance(value, ToolOutput)
                else ToolOutput(text=value)
            )
        except asyncio.CancelledError:
            raise  # The accepted effectful intent remains unresolved.
        except Exception:
            reason = "outcome_unknown" if tool.effect == "effectful" else "tool_error"
            return await self.result(
                inv, "interrupted" if tool.effect == "effectful" else "error", reason
            )
        message = Message(
            role="tool",
            content=output.text,
            tool_call_id=inv.manifest.call_id,
            attachments=output.attachments,
        )
        message = await self._guard(tool, output, message)
        message = await self._after(inv, tool, args, message, output)
        status = "withheld" if message.is_error else "ok"
        if output.settlement is not None:
            message, entries = await prepare_result(
                self.gov,
                inv.id,
                inv.manifest,
                status,
                "withheld" if message.is_error else None,
                message,
            )
            if not await settle_result(
                self.gov,
                self.workers,
                output.settlement,
                inv.id,
                entries,
                withheld=message.is_error,
            ):
                raise ExecutionDenied("settlement_uncertain")
            return message
        return await self.result(inv, status, "withheld" if message.is_error else None, message)

    async def _guard(self, tool: Tool, output: ToolOutput, message: Message) -> Message:
        if len(canonical(output.model_dump(mode="json"))) > self.gov.limits.max_result_bytes:
            return message.model_copy(
                update={"content": "result_too_large", "is_error": True, "attachments": ()}
            )
        for guard in self.guards:
            try:
                async with asyncio.timeout(self.gov.limits.hook_timeout_seconds):
                    verdict = await guard.check(
                        GuardInput(
                            tool=tool.name,
                            output=output.model_copy(deep=True),
                            traits=tool.traits.copy(),
                            facts=output.facts.copy(),
                        ),
                        self.gov.log.session.model_copy(deep=True),
                    )
                if verdict.outcome != "ok":
                    raise ExecutionDenied("result_withheld")
            except Exception:
                return message.model_copy(
                    update={"content": "result_withheld", "is_error": True, "attachments": ()}
                )
        return message

    async def _after(
        self, inv: Invocation, tool: Tool, args: Normalized, message: Message, output: ToolOutput
    ) -> Message:
        for provider in self.registry.providers:
            try:
                changed = await self._hook(
                    provider,
                    "after_call",
                    self.gov.log.session,
                    self.spec,
                    ToolCall(id=inv.manifest.call_id, name=tool.name, arguments=args.arguments),
                    message,
                )
                if not isinstance(changed, Message):
                    raise ExecutionDenied("invalid_provider_result")
                changed = Message.model_validate(changed.model_dump(mode="json"))
                if not compatible(message, changed):
                    raise ExecutionDenied("invalid_provider_result")
                if changed != message:
                    changed = await self._guard(
                        tool,
                        output.model_copy(
                            update={"text": changed.content, "attachments": changed.attachments},
                            deep=True,
                        ),
                        changed,
                    )
                message = changed
            except (ExecutionDenied, ValueError, TypeError):
                message = message.model_copy(
                    update={"content": "result_withheld", "is_error": True, "attachments": ()}
                )
        return message

    async def _hook(self, provider: Provider, method: str, *args: object) -> object:
        return await provider_hook(self.gov, self.workers, provider, method, *args)

    @staticmethod
    def _provider_reason(provider: Provider, value: str) -> str:
        code = reason_code(value, f"x.{provider.id}.invalid_reason")
        return code if code.startswith(f"x.{provider.id}.") else f"x.{provider.id}.invalid_reason"

    async def result(
        self, inv: Invocation, status: str, reason: str | None, message: Message | None = None
    ) -> Message:
        message, entries = await prepare_result(
            self.gov, inv.id, inv.manifest, status, reason, message
        )
        await self.gov.commit(entries)
        self.pending.pop(inv.id, None)
        return message

    async def _replay(self, inv: Invocation, tool: Tool, args: Normalized, intent: Entry) -> None:
        planned = self.replays.pop(inv.id, None)
        if planned is None or planned != intent or tool.replay != "safe":
            raise ExecutionDenied("recovery_required")
        if (
            intent.facts.get("args_digest"),
            intent.facts.get("tool_digest"),
            intent.facts.get("spec_digest"),
        ) != (
            args.sha256,
            digest(definition(tool)),
            digest(self.spec.model_dump(mode="json")),
        ):
            raise ExecutionDenied("recovery_binding_changed")
        await self.gov.fact(
            "x.effects.replayed",
            key="retry:" + str(uuid4()),
            invocation_id=inv.id,
            intent_index=intent.index,
            args_digest=args.sha256,
            tool_digest=inv.manifest.tool_digest,
        )
