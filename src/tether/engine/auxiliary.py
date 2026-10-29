"""Tool-free bounded auxiliary calls reuse current gates, attempts and usage."""

import asyncio
from collections.abc import Callable
from uuid import uuid4

from tether.ports import ModelPort
from tether.runtime.pydantic_ai.response import bounded_response
from tether.types import Message, ModelAttemptDenied, ModelRequest, ModelUnavailable, RequestTags
from tether.types.auxiliary import AuxiliaryProfile
from tether.types.canonical import canonical, digest
from tether.types.commands import AuxiliaryCall
from tether.types.context import ContextError, TokenEstimate
from tether.types.execution import AgentSpec, ExecutionDenied, NewEntry, TurnResult
from tether.types.history import HistoryProvider

from .attempts import JournalAttemptRecorder
from .context import ContextBoundary
from .governance import Governance
from .workers import Workers


class AuxiliaryOperation:
    def __init__(
        self,
        gov: Governance,
        command: AuxiliaryCall,
        spec: AgentSpec,
        profile: AuxiliaryProfile,
        context: ContextBoundary,
        model: ModelPort,
        workers: Workers,
        history: HistoryProvider | None,
    ) -> None:
        self.gov, self.command, self.spec, self.profile = gov, command, spec, profile
        self.context, self.model, self.workers, self.history = context, model, workers, history
        self.config = context.configuration(profile.model)
        self.capacity = self.config.limits[profile.model]
        self.sources = tuple(
            e for e in gov.log.entries if e.kind == "message" and e.index <= command.through_index
        )
        self.messages: tuple[Message, ...] = ()
        self.estimate: TokenEstimate | None = None
        self.request: ModelRequest | None = None
        self.recorder: JournalAttemptRecorder | None = None

    async def read(self) -> tuple[Message, ...]:
        messages: list[Message] = []
        size = 0
        for entry in self.sources:
            message = await self.gov.read(entry.history_ref, source_index=entry.index)
            size += len(canonical(message.model_dump(mode="json")))
            if size > self.config.max_source_bytes:
                raise ExecutionDenied("auxiliary_source_bytes")
            messages.append(message)
        result = tuple(messages)
        return result

    async def recheck(self) -> None:
        actor = self.gov.actor
        if actor.credential is None:
            raise ModelAttemptDenied("missing_credential")
        async with asyncio.timeout(self.profile.limits.request_timeout_seconds):
            authorization = await actor.credential.authorization()
        if not isinstance(authorization, str) or not authorization.strip():
            raise ModelAttemptDenied("missing_credential")
        if await self.read() != self.messages:
            raise ExecutionDenied("auxiliary_source_changed")
        if self.context.estimator is None or self.request is None:
            raise ContextError("context_unconfigured")
        estimate = await self.workers.call(
            self.gov.log.session.id, self.context.estimator.estimate, self.request
        )
        if estimate != self.estimate:
            raise ExecutionDenied("auxiliary_estimate_changed")

    def admit(self) -> None:
        if self.recorder is None or len(self.recorder.started) >= self.profile.limits.max_attempts:
            raise ModelAttemptDenied("attempt_limit")

    async def execute(self) -> TurnResult:
        gov, profile = self.gov, self.profile
        async with asyncio.timeout(self.config.timeout_seconds):
            self.messages = await self.read()
            tags = RequestTags(
                session_id=gov.log.session.id,
                principal=gov.actor.principal or gov.actor.id,
                spec_id=self.spec.id,
                spec_version=self.spec.version,
                turn_id=gov.turn_id,
                model_call_id=str(uuid4()),
                purpose="auxiliary",
                purpose_profile=profile.id,
            )
            self.request = ModelRequest(
                model=profile.model,
                tags=tags,
                system=profile.instructions,
                messages=self.messages,
                max_output_tokens=self.capacity.output_tokens,
                credential=gov.actor.credential,
            )
            if self.context.estimator is None:
                raise ContextError("context_unconfigured")
            self.estimate = await self.workers.call(
                gov.log.session.id, self.context.estimator.estimate, self.request
            )
            if self.estimate.tokens > self.capacity.input_tokens:
                raise ContextError("context_exhausted")
            await self.recheck()
            recorder = JournalAttemptRecorder(
                gov, tags, profile.model, self.history, self.recheck, admit=self.admit
            )
            self.recorder = recorder
            await gov.authorize("call_model", "model:" + profile.model, purpose="auxiliary")
            with gov.observers.model(
                self.request,
                owner=gov.log.session.owner,
                usage=lambda call: gov.accounting.call_usage(call),
            ) as trace:
                response = await self.workers.call(
                    gov.log.session.id,
                    bounded_response,
                    self.model,
                    self.request.model_copy(update={"attempts": recorder, "traceparent": trace}),
                    profile.limits,
                )
            if response.tool_calls or response.finish_reason != "stop":
                raise ModelUnavailable("invalid_auxiliary_response")
            await self.recheck()
            message = Message(role="assistant", content=response.content)
            ref = await gov.history.put(
                actor=gov.actor,
                session_id=gov.log.session.id,
                key="auxiliary:" + self.command.request_id,
                message=message,
            )
            await gov.commit(
                [
                    NewEntry(
                        kind="auxiliary_result",
                        turn_id=gov.turn_id,
                        history_ref=ref,
                        facts={
                            "profile": profile.id,
                            "profile_digest": digest(profile.model_dump(mode="json")),
                            "through_index": self.command.through_index,
                        },
                    ),
                    NewEntry(
                        kind="model_call",
                        turn_id=gov.turn_id,
                        facts={
                            "status": "ok",
                            "purpose": "auxiliary",
                            "purpose_profile": profile.id,
                            "model_call_id": tags.model_call_id,
                            "attempts_complete": True,
                            "attempt_ids": list(recorder.started),
                            "usage": response.usage.model_dump(mode="json")
                            if response.usage
                            else None,
                        },
                    ),
                ]
            )
            return TurnResult(turn_id=gov.turn_id, stop_reason="final", messages=(message,))


async def run_auxiliary(
    operation: AuxiliaryOperation, accepted: "Callable[[], None] | None"
) -> TurnResult:
    gov, command, spec = operation.gov, operation.command, operation.spec
    await gov.commit(
        [
            NewEntry(
                kind="turn_started",
                turn_id=gov.turn_id,
                key="request:" + command.request_id,
                facts={
                    "origin": "auxiliary",
                    "input_digest": command.fingerprint,
                    "spec_id": spec.id,
                    "spec_version": spec.version,
                    "spec_digest": digest(spec.model_dump(mode="json")),
                    "profile_digest": digest(operation.profile.model_dump(mode="json")),
                    "through_index": command.through_index,
                },
            )
        ]
    )
    if accepted is not None:
        accepted()
    try:
        result = await operation.execute()
    except asyncio.CancelledError:
        await asyncio.shield(gov.fact("turn_ended", stop_reason="cancelled"))
        raise
    except (ExecutionDenied, ModelAttemptDenied, ContextError) as error:
        result = TurnResult(
            turn_id=gov.turn_id,
            stop_reason="model_denied",
            reason=error.reason if not isinstance(error, ContextError) else error.code,
        )
    except ModelUnavailable as error:
        result = TurnResult(
            turn_id=gov.turn_id, stop_reason="model_unavailable", reason=error.reason
        )
    await gov.fact("turn_ended", stop_reason=result.stop_reason, reason=result.reason)
    return result
