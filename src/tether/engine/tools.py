"""Ordered tool gates. No SDK type or tool scheduling enters this module."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

from tether.types import JsonValue, Message, ToolCall
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    ENGINE_CONTRACT_VERSION,
    AgentSpec,
    ApprovalBinding,
    CallManifest,
    Entry,
    ExecutionDenied,
    GuardInput,
    NewEntry,
    PendingConfirmation,
    Provider,
    Refusal,
    ResultGuard,
    Tool,
    ToolContext,
    ToolOutput,
    copy_actor,
)
from tether.types.tooling import Normalized, definition, normalize

from .effects import unresolved_effects
from .governance import Governance, reason_code
from .providers import provider_hook
from .results import result_content_key
from .workers import Workers


@dataclass(frozen=True)
class PinnedTool:
    tool: Tool
    sha256: str


class Registry:
    def __init__(self, tools: tuple[Tool, ...], providers: tuple[Provider, ...]) -> None:
        self.tools: dict[str, PinnedTool] = {}
        self.providers = providers
        ids = [p.id for p in providers]
        if len(set(ids)) != len(ids) or any(
            not 1 <= p.contract <= ENGINE_CONTRACT_VERSION for p in providers
        ):
            raise ValueError("provider_conflict")
        for tool in (*tools, *(t for p in providers for t in p.tools())):
            if tool.name in self.tools:
                raise ValueError("provider_conflict")
            self.tools[tool.name] = PinnedTool(tool, digest(definition(tool)))

    def manifest(self, call: ToolCall, position: int) -> CallManifest:
        pinned = self.tools[call.name]
        return CallManifest(
            position=position,
            call_id=call.id,
            tool=call.name,
            args_digest=digest(call.arguments),
            tool_digest=pinned.sha256,
            effect=pinned.tool.effect,
            replay=pinned.tool.replay,
        )


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
    ) -> None:
        self.gov, self.registry, self.spec = gov, registry, spec
        self.workers, self.guards = workers, guards
        self.calls: dict[str, Invocation] = {}
        self.pending: dict[str, PendingConfirmation] = {}
        self.contexts: dict[str, ToolContext] = {}

    def bind(self, source: Entry) -> None:
        self.calls = {
            m.call_id: Invocation(
                source, m, digest([self.gov.log.session.id, source.index, m.position])
            )
            for m in source.call_manifest
        }

    def unresolved(self) -> bool:
        return unresolved_effects(self.gov.log.entries)

    async def execute(self, call: ToolCall) -> Message | None:
        if self.pending:
            return None
        inv = self.calls.get(call.id)
        if inv is None or call.name != inv.manifest.tool:
            raise ExecutionDenied("invalid_invocation_binding")
        existing = next((e for e in self.gov.log.entries if e.key == "result:" + inv.id), None)
        if existing is not None:
            return await self.gov.read(existing.history_ref)
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
        if inv.manifest.effect == "effectful" and self.unresolved():
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
        ):
            raise ExecutionDenied("definition_changed")
        source = await self.gov.read(inv.source.history_ref)
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
        return ToolContext(
            actor=copy_actor(self.gov.actor),
            invocation_id=inv.id,
            scope=self.gov.log.session.held,
            capabilities=capabilities,
            retrieve=self._read,
        )

    async def _read(self, resource: str, fetch: Callable[[], object]) -> object:
        await self.gov.authorize("retrieve", resource)
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
        answered = next((e for e in self.gov.log.entries if e.key == "approval:" + inv.id), None)
        if answered is not None:
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
        if any(e.key == "intent:" + inv.id for e in self.gov.log.entries):
            raise ExecutionDenied("recovery_required")
        needs_intent = tool.effect == "effectful" or tool.replay == "unsafe"
        if needs_intent:
            await self.gov.fact(
                "tool_call",
                key="intent:" + inv.id,
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
                )
            output = value if isinstance(value, ToolOutput) else ToolOutput(text=value)
        except asyncio.CancelledError:
            raise  # The accepted effectful intent remains unresolved.
        except Exception:
            reason = "outcome_unknown" if tool.effect == "effectful" else "tool_error"
            return await self.result(
                inv, "interrupted" if tool.effect == "effectful" else "error", reason
            )
        message = Message(role="tool", content=output.text, tool_call_id=inv.manifest.call_id)
        message = await self._guard(tool, output, message)
        message = await self._after(inv, tool, args, message)
        status = "withheld" if message.is_error else "ok"
        return await self.result(inv, status, "withheld" if message.is_error else None, message)

    async def _guard(self, tool: Tool, output: ToolOutput, message: Message) -> Message:
        if output.attachments:
            return message.model_copy(update={"content": "unsupported_content", "is_error": True})
        if len(canonical(output.model_dump(mode="json"))) > self.gov.limits.max_result_bytes:
            return message.model_copy(update={"content": "result_too_large", "is_error": True})
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
                return message.model_copy(update={"content": "result_withheld", "is_error": True})
        return message

    async def _after(
        self, inv: Invocation, tool: Tool, args: Normalized, message: Message
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
                if not isinstance(changed, Message) or (
                    changed.role,
                    changed.tool_call_id,
                    changed.is_error,
                    changed.tool_calls,
                    changed.attachments,
                ) != (message.role, message.tool_call_id, message.is_error, (), ()):
                    raise ExecutionDenied("invalid_provider_result")
                if changed != message:
                    changed = await self._guard(tool, ToolOutput(text=changed.content), changed)
                message = changed
            except ExecutionDenied:
                message = message.model_copy(
                    update={"content": "result_withheld", "is_error": True}
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
        message = message or Message(
            role="tool", content=reason or status, tool_call_id=inv.manifest.call_id, is_error=True
        )
        ref = await self.gov.history.put(
            actor=self.gov.actor,
            session_id=self.gov.log.session.id,
            key=result_content_key(inv.id, message),
            message=message,
        )
        facts: dict[str, JsonValue] = {
            "invocation_id": inv.id,
            "tool": inv.manifest.tool,
            "status": status,
            "reason": reason,
            "effect": inv.manifest.effect,
            "replay": inv.manifest.replay,
        }
        entries = [
            NewEntry(
                kind="message",
                key="message:result:" + inv.id,
                turn_id=self.gov.turn_id,
                history_ref=ref,
                facts={
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "is_error": message.is_error,
                },
            ),
            NewEntry(
                kind="tool_call",
                turn_id=self.gov.turn_id,
                key="result:" + inv.id,
                facts=facts,
                history_ref=ref,
            ),
        ]
        await self.gov.commit(entries)
        self.pending.pop(inv.id, None)
        return message
