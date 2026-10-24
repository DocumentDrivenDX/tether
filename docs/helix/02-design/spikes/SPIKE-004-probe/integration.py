"""SPIKE-004: real Agent, public hooks, fabricated HTTPX transport and durable fixtures."""
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import uuid
import httpx2 as httpx
from pydantic_ai import Agent, ConversationTypeAdapter, DeferredToolRequests, DeferredToolResults, ModelMessagesTypeAdapter
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import ModelResponse, ModelRequest as SDKRequest, TextPart, ToolCallPart, ToolReturnPart, RetryPromptPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.usage import RequestUsage
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import Tool
from fixture_store import *

ACTIVE = ContextVar('spike_engine')
new_id = lambda: uuid.uuid4().hex


class Credential:
    def __init__(self, actor): self.actor, self.reads = actor, 0
    async def authorization(self):
        self.reads += 1
        # Synthetic value assembled only at runtime, never serialized.
        return ' '.join(['Synthetic', self.actor, str(self.reads)])


class TerminalInvalid(BoundaryError): pass


class ProbeModel(Model):
    def __init__(self):
        super().__init__()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.transport))
    @property
    def model_name(self): return 'fabricated-boundary-model'
    @property
    def system(self): return 'fabricated'
    async def transport(self, request):
        engine = ACTIVE.get()
        tags = json.loads(request.headers['Databricks-Ai-Gateway-Request-Tags'])
        attempt = next(r for r in engine.store.facts('attempt') if r['facts']['identity']['attempt_id'] == tags['tether_attempt_id'])
        envelope = dict(method=request.method, url=str(request.url), tags=tags, body=json.loads(request.content))
        if attempt['facts']['request_digest'] != digest(envelope): raise IntegrityError('wire_changed_after_start')
        engine.egress.append(dict(tags=tags,
                                  authorization=request.headers.get('authorization'),
                                  body=json.loads(request.content)))
        engine.transport_count += 1
        if engine.transport_failures:
            engine.transport_failures -= 1
            return httpx.Response(503, headers={'x-request-id': 'header-'+new_id()})
        messages = json.loads(request.content)['messages']
        has_result = any(p['part_kind'] == 'tool-return' for m in messages for p in m['parts'])
        if engine.reply_plan is not None:
            value = engine.reply_plan(json.loads(request.content), engine.transport_count)
        elif has_result or engine.text_only:
            value = dict(parts=[dict(kind='text', text='fabricated complete')], finish='stop')
        else:
            value = dict(parts=[dict(kind='tool', name=name, args=engine.raw_args,
                                    id='sdk-'+new_id()) for name in engine.batch], finish='tool_call')
        if engine.terminal_override is not None: value['finish'] = engine.terminal_override
        value['response_id'] = 'body-'+new_id()
        return httpx.Response(200, json=value, headers={'x-request-id': 'header-'+new_id()})

    async def request(self, messages, model_settings, model_request_parameters):
        _, parameters = self.prepare_request(model_settings, model_request_parameters)
        e = ACTIVE.get()
        call_id = new_id()
        purpose = 'correction' if any(isinstance(p, RetryPromptPart) for p in messages[-1].parts) else e.purpose
        await e.verify_projection(messages)
        wire_messages = ModelMessagesTypeAdapter.dump_python(messages, mode='json')
        for _ in range(e.max_attempts):
            attempt = new_id()   # reserve before rendering tags/body and digest
            identity = dict(session_id=e.store.session, turn_id=e.turn_id,
                            model_call_id=call_id, attempt_id=attempt, purpose=purpose)
            wire = dict(model=self.model_name, messages=wire_messages, max_output_tokens=128,
                        tools=[dict(name=t.name, schema=t.parameters_json_schema) for t in (*parameters.function_tools, *parameters.output_tools)])
            tags = {'tether_'+k: v for k,v in identity.items()}
            envelope = dict(method='POST', url='https://fabricated.invalid/model', tags=tags, body=wire)
            await e.authorize('CALL_MODEL')
            credential = await e.credential.authorization()
            await e.store.commit('attempt', key='attempt:'+attempt, token=e.token,
                                 identity=identity, request_digest=digest(envelope))
            request_ref = await e.store.put('request:'+attempt, dict(request=envelope))
            # Capturing local request content succeeds before dependent egress.
            await e.store.commit('exchange_request', token=e.token, identity=identity, ref=request_ref)
            response = await self.client.post('https://fabricated.invalid/model', content=canonical(wire),
                    headers={'Databricks-Ai-Gateway-Request-Tags': canonical(tags).decode(), 'authorization': credential})
            receipt = dict(provider_request_id=response.headers['x-request-id'],
                           response_id=None, schema_profile='fabricated-httpx-v1')
            if response.status_code == 503:
                await e.store.commit('receipt', token=e.token, identity=identity, receipt=receipt, status=503)
                continue
            value = response.json(); receipt['response_id'] = value['response_id']
            ref = await e.store.source('response:'+attempt, value, e.token)
            await e.store.commit('receipt', token=e.token, identity=identity, receipt=receipt,
                                 status=200, response_ref=ref)
            parts = value['parts']; finish = value['finish']
            tools = [p for p in parts if p['kind'] == 'tool']
            if finish not in ('stop', 'tool_call') or (finish == 'stop' and tools) or (finish == 'tool_call' and not tools):
                raise TerminalInvalid('invalid_terminal')
            if any(p['name'] not in {t['name'] for t in wire['tools']} for p in tools): raise TerminalInvalid('unknown_tool')
            if len({p['id'] for p in tools}) != len(tools): raise TerminalInvalid('duplicate_call_id')
            out = []
            for pos, part in enumerate(parts):
                if part['kind'] == 'text': out.append(TextPart(part['text'])); continue
                if part['name'] not in ('commit', 'other'):
                    await e.store.commit('output_proposal',token=e.token,call_id=part['id'],tool=part['name'],source_ref=ref)
                    out.append(ToolCallPart(part['name'], part['args'], tool_call_id=part['id'])); continue
                invocation = digest([e.store.session, ref['key'], pos])
                manifest = dict(invocation=invocation, call_id=part['id'], tool=part['name'],
                    raw_digest=digest(part['args']), tool_digest=e.definition(part['name']),
                    source_ref=ref, position=pos, replay=e.replay)
                await e.store.commit('manifest', key='manifest:'+invocation, token=e.token, **manifest)
                e.calls[part['id']] = manifest
                out.append(ToolCallPart(part['name'], part['args'], tool_call_id=part['id']))
            return ModelResponse(parts=out, model_name=self.model_name, finish_reason=finish,
                                 provider_response_id=value['response_id'], usage=RequestUsage(unmeasured_requests=_+1))
        raise BoundaryError('attempts_exhausted')


class BoundaryCapability(AbstractCapability):
    async def before_tool_validate(self, ctx, *, call, tool_def, args):
        e = ctx.deps
        manifest = e.calls[call.tool_call_id]
        if e.unresolved() and not e.recovering_same(manifest): raise EffectUnresolved('effect_unresolved')
        await e.authorize('CALL_TOOL')
        if manifest['tool'] != tool_def.name or manifest['tool_digest'] != e.definition(tool_def.name) or manifest['replay'] != e.replay: raise ApprovalInvalid('tool_changed')
        await e.authorize('READ_SESSION')
        source = await e.store.read(manifest['source_ref'], e.actor)
        if digest(call.args_as_dict()) != manifest['raw_digest'] or digest(source['parts'][manifest['position']]['args']) != manifest['raw_digest']:
            raise IntegrityError('raw_proposal_changed')
        await e.store.commit('gate', token=e.token, invocation=manifest['invocation'], stage='definition')
        return args

    async def after_tool_validate(self, ctx, *, call, tool_def, args):
        e = ctx.deps; m = e.calls[call.tool_call_id]; inv = m['invocation']
        # Pydantic's validation supplied canonical primitive values/defaults.
        canonical(args)
        e.normalized[inv] = json.loads(canonical(args))
        e.provider_seen.append(json.loads(canonical(args)))
        await e.store.commit('gate', token=e.token, invocation=inv, stage='provider')
        if e.provider_denies: raise Denied('provider_denied')
        binding = dict(invocation=inv, args_digest=digest(args), tool_digest=e.definition(tool_def.name),
                       spec_digest=e.spec_digest, actor=e.actor)
        approved = next((r['facts'] for r in e.store.facts('approved') if r['facts']['invocation'] == inv), None)
        if approved is None:
            ref = await e.store.put('pending:'+inv, dict(arguments=args))
            await e.store.commit('pending', key='pending:'+inv, token=e.token, **binding, ref=ref)
            raise ApprovalRequired()
        if approved != binding: raise ApprovalInvalid('approval_binding_changed')
        if e.intent(inv) and e.intent(inv)['args_digest'] != digest(args): raise ApprovalInvalid('intent_changed')
        return args


@dataclass
class Engine:
    store: FixtureStore
    model: ProbeModel
    actor: str = 'actor-a'
    replay: str = 'unsafe'
    version: str = '1'
    spec: str = 'spec-1'
    deny: set = field(default_factory=set)
    policy_raises: bool = False
    provider_denies: bool = False
    raw_args: dict = field(default_factory=lambda: {'amount': '3'})
    batch: tuple = ('commit',)
    transport_failures: int = 0
    max_attempts: int = 2
    terminal_override: str | None = None
    crash: str | None = None
    purpose: str = 'answer'
    text_only: bool = False
    attempt_budget: int = 20
    reply_plan: object = None

    def __post_init__(self):
        self.token = self.store.generation; self.turn_id = new_id()
        self.credential = Credential(self.actor)
        self.tool_digest = digest(dict(version=self.version, replay=self.replay, effect='effectful'))
        self.spec_digest = digest(self.spec)
        self.calls = {r['facts']['call_id']: r['facts'] for r in self.store.facts('manifest')}
        self.normalized, self.provider_seen, self.egress = {}, [], []
        self.transport_count = 0
        self.tools = {name:Tool(self.business_wrapper,takes_ctx=True,name=name,sequential=True)
                      for name in (() if self.purpose=='summary' else ('commit','other'))}
        self.agent = Agent(self.model, deps_type=Engine, output_type=[str, DeferredToolRequests],
                           tools=list(self.tools.values()), capabilities=[BoundaryCapability()], retries=0)

    def policy_decision(self, action):
        if self.policy_raises: raise RuntimeError('fabricated_policy_outage')
        return self.actor == self.store.owner and action not in self.deny

    async def authorize(self, action):
        try: allowed = self.policy_decision(action)
        except Exception: allowed = False
        if action == 'CALL_MODEL' and sum(r['facts']['identity']['turn_id'] == self.turn_id for r in self.store.facts('attempt')) >= self.attempt_budget:
            allowed = False
        await self.store.commit('authorization', token=self.token, action=action, allowed=allowed,
                                actor=self.actor, turn_id=self.turn_id)
        if not allowed: raise Denied('policy_denied')

    def definition(self, name):
        tool = self.tools[name].tool_def
        return digest(dict(name=name, version_digest=self.tool_digest,
                           description=tool.description, schema=tool.parameters_json_schema))

    def intent(self, invocation):
        return next((r['facts'] for r in self.store.facts('intent') if r['facts']['invocation'] == invocation), None)
    def result(self, invocation):
        return next((r['facts'] for r in self.store.facts('result') if r['facts']['invocation'] == invocation), None)
    def unresolved(self):
        return [r['facts']['invocation'] for r in self.store.facts('intent') if not self.result(r['facts']['invocation'])]
    def recovering_same(self, manifest):
        return self.replay == manifest['replay'] == 'safe' and self.intent(manifest['invocation']) is not None

    async def business_wrapper(self, ctx, amount: int, count: int = 2) -> str:
        m = self.calls[ctx.tool_call_id]; inv = m['invocation']
        args = dict(amount=amount, count=count)
        if digest(args) != digest(self.normalized.get(inv)): raise ApprovalInvalid('post_gate_mutation')
        if self.result(inv): raise AlreadyAnswered('result_already_committed')
        if self.crash == 'before_intent': os._exit(70)
        if self.intent(inv):
            if not self.recovering_same(m): raise EffectUnresolved('unsafe_replay')
        else:
            await self.store.commit('intent', key='intent:'+inv, token=self.token, invocation=inv,
                args_digest=digest(args), tool_digest=self.definition(m['tool']), spec_digest=self.spec_digest, turn_id=self.turn_id)
        if self.crash == 'before_effect': os._exit(72)
        # Only replay-safe fixture mode deduplicates; unsafe mode counts every dispatch.
        output = await self.store.effect(inv, amount * count, replay_safe=m['replay']=='safe')
        if self.crash == 'after_effect': os._exit(71)
        if self.crash == 'raise_after_effect': raise BoundaryError('fabricated_effect_error')
        if self.crash == 'cancel_after_effect': raise asyncio.CancelledError()
        # Tether owns normalized tool-result bytes before returning them to Agent.
        ref = await self.store.put('result:'+inv, dict(role='tool', call_id=ctx.tool_call_id, text=output))
        await self.store.commit('result', key='result:'+inv, token=self.token,
                                invocation=inv, ref=ref, status='ok')
        if self.crash == 'after_result': os._exit(73)
        return output

    async def verify_projection(self, messages):
        await self.authorize('READ_SESSION')
        sources = await self.store.hydrate_sources(self.actor)
        users = {v['text'] for v in sources if v.get('role')=='user'}
        responses = {v['response_id']:v for v in sources if 'response_id' in v}
        for message in messages:
            if isinstance(message,ModelResponse):
                source = responses.get(message.provider_response_id)
                if source is None or source['finish'] != message.finish_reason:
                    raise IntegrityError('unobserved_model_response')
                actual=[]
                for part in message.parts:
                    if isinstance(part,TextPart): actual.append(dict(kind='text',text=part.content))
                    elif isinstance(part,ToolCallPart): actual.append(dict(kind='tool',name=part.tool_name,args=part.args_as_dict(),id=part.tool_call_id))
                    else: raise IntegrityError('unsupported_response_component')
                if actual != source['parts']: raise IntegrityError('response_projection_changed')
            else:
                for part in message.parts:
                    if isinstance(part,UserPromptPart):
                        if not isinstance(part.content,str) or part.content not in users:
                            raise IntegrityError('unaccepted_user_content')
                    elif isinstance(part,ToolReturnPart):
                        m=self.calls.get(part.tool_call_id)
                        record=self.result(m['invocation']) if m else None
                        if not record:
                            output = next((r['facts'] for r in self.store.facts('output_proposal') if r['facts']['call_id']==part.tool_call_id),None)
                            if output and part.tool_name==output['tool'] and part.content=='Final result processed.':
                                continue # version-pinned SDK schema-output acknowledgment, never an external effect
                            raise IntegrityError('unrecorded_tool_result')
                        value=await self.store.read(record['ref'],self.actor)
                        if part.tool_name!=m['tool'] or part.content!=value['text']:
                            raise IntegrityError('tool_result_projection_changed')
                    elif isinstance(part,RetryPromptPart):
                        # SDK-produced validation feedback is derived, never a user/effect fact.
                        # Externally supplied runner state still requires a committed checkpoint.
                        if not responses: raise IntegrityError('unobserved_retry')
                    else: raise IntegrityError('unsupported_request_component')

    async def run(self, prompt='fabricated request', *, history=None, approved=None, conversation=None, continuation_prompt=None, _reconstructed=False):
        if self.actor != self.store.owner: raise Denied('session_owner')
        await self.authorize('READ_SESSION')
        await self.store.hydrate_sources(self.actor)
        if not _reconstructed:
            if history is not None:
                expected = self.store.checkpoint()['messages_sha256']
                if digest(ModelMessagesTypeAdapter.dump_python(history,mode='json')) != expected:
                    raise ApprovalInvalid('unverified_derived_history')
            if conversation is not None:
                expected = self.store.checkpoint()['conversation_sha256']
                if digest(ConversationTypeAdapter.dump_python(conversation,mode='json')) != expected:
                    raise ApprovalInvalid('unverified_runner_state')
        if history is not None: await self.verify_projection(history)
        if conversation is not None: await self.verify_projection(conversation.messages)
        user_prompt = prompt if history is None and conversation is None else continuation_prompt
        if user_prompt is not None:
            await self.store.source('user:'+new_id(), dict(role='user', text=user_prompt), self.token)
        active = ACTIVE.set(self)
        try:
            with ToolManager.parallel_execution_mode('sequential'):
                result = await self.agent.run(user_prompt, deps=self,
                    message_history=history if conversation is None else None, conversation=conversation, deferred_tool_results=approved)
            await self.verify_projection(result.all_messages())
            await self.store.save_derived(result.all_messages_json(),ConversationTypeAdapter.dump_json(result.conversation),self.token)
            return result
        finally: ACTIVE.reset(active)

    async def grant(self, *, invocation=None):
        await self.authorize('APPROVE')
        pending = self.store.facts('pending')[-1]['facts'] if invocation is None else next(
            r['facts'] for r in self.store.facts('pending') if r['facts']['invocation'] == invocation)
        if any(r['facts']['invocation'] == pending['invocation'] for r in self.store.facts('approved')):
            raise AlreadyAnswered('approval_already_answered')
        await self.authorize('READ_SESSION')
        args = (await self.store.read(pending['ref'], self.actor))['arguments']
        name = next(m['tool'] for m in self.calls.values() if m['invocation'] == pending['invocation'])
        if pending['tool_digest'] != self.definition(name) or pending['spec_digest'] != self.spec_digest:
            raise ApprovalInvalid('definition_changed')
        if digest(args) != pending['args_digest']: raise ApprovalInvalid('pending_changed')
        binding = {k: pending[k] for k in ('invocation','args_digest','tool_digest','spec_digest','actor')}
        await self.store.commit('approved', key='approved:'+pending['invocation'], token=self.token, **binding)
        return pending['invocation']

    async def resume(self):
        await self.authorize('RESUME')
        await self.authorize('READ_SESSION')
        raw = await self.store.load_derived(self.actor)
        history = ModelMessagesTypeAdapter.validate_json(raw)
        conversation = ConversationTypeAdapter.validate_json(await self.store.load_runner(self.actor))
        await self.verify_projection(history)
        await self.verify_projection(conversation.messages)
        pending = self.store.facts('pending')[-1]['facts']; inv = pending['invocation']
        if self.result(inv):
            if any(isinstance(p, ToolReturnPart) and p.tool_call_id in self.calls and self.calls[p.tool_call_id]['invocation'] == inv for m in history for p in m.parts):
                return 'already_completed'
            value = await self.store.read(self.result(inv)['ref'], self.actor)
            m = next(m for m in self.calls.values() if m['invocation'] == inv)
            history.append(SDKRequest(parts=[ToolReturnPart(tool_name=m['tool'], tool_call_id=m['call_id'], content=value['text'])]))
            conversation.messages = history
            return await self.run(conversation=conversation,_reconstructed=True)
        if self.intent(inv) and next(m['replay'] for m in self.calls.values() if m['invocation'] == inv) == 'unsafe':
            await self.store.commit('unknown', key='unknown:'+inv, token=self.token, invocation=inv)
            return 'outcome_unknown'
        call_id = next(m['call_id'] for m in self.calls.values() if m['invocation'] == inv)
        return await self.run(conversation=conversation, approved=DeferredToolResults(approvals={call_id: True}))
