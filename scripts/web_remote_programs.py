"""Source strings executed in the isolated installed server and client profiles."""

# @covers US-066-AC8: these programs run in different clean environments.
SERVER = """import asyncio,sys
from fastapi import Request
from hypercorn.asyncio import serve
from hypercorn.config import Config
import tether.client as api
from tether.client.testing import demo_policy,fabricated_model_context,scripted_model
from tether_web_api import ApiFailure,ApiSettings,create_api
class Identity:
    async def actor_for(self,request:Request)->api.Actor:
        if request.headers.get('authorization') != 'Bearer '+sys.argv[2]:
            raise ApiFailure(401,'authentication_required')
        return api.Actor(id='fabricated-installed')
async def main():
    context=fabricated_model_context()
    settings=api.ClientSettings(
        model=scripted_model([
            api.ModelResponse(content='Installed remote answer',finish_reason='stop'),
        ]),
        storage=api.VolatileSettings(),policy=demo_policy(),
        client_limits=api.ClientLimits(max_page_size=100,max_call_ids=8,max_read_bytes=1048576,max_lookup_entries=1000,read_timeout_seconds=5,heartbeat_interval_seconds=1,shutdown_timeout_seconds=5,follower_queue_size=32,max_followers=8,max_active_turns=8,max_request_bytes=65536),
        model_limits=api.ModelExecutionLimits(max_response_bytes=8192,max_tool_argument_bytes=512,max_attempts=2,request_timeout_seconds=5,max_retry_delay_seconds=.01),
        context_config=context['context_config'],estimator=context['estimator'],
        specs=(api.AgentSpec(
            id='demo',version='1',title='Installed fixture',
            instructions='Fabricated data only',model='demo'),),
        default_spec='demo',resume='off',
    )
    async with await api.ClientHost.open(settings) as host:
        app=create_api(clients=host.client,authenticator=Identity(),settings=ApiSettings())
        async def witness(request,call_next):
            response=await call_next(request)
            response.headers['x-tether-instance']=sys.argv[3]
            return response
        app.middleware('http')(witness)
        config=Config();config.bind=['127.0.0.1:'+sys.argv[1]]
        config.accesslog=None;config.graceful_timeout=2
        await serve(app,config)

asyncio.run(main())
"""
REMOTE = """import asyncio,importlib.metadata as md,sys,httpx
from tether.client.remote import RemoteClient,RemoteClientSettings
class Credential:
    async def authorization(self):return 'Bearer '+sys.argv[2]
async def credential():return Credential()
async def main():
    for name in ('fastapi','novirhiza-tether-web-api','textual'):
        try:md.distribution(name)
        except md.PackageNotFoundError:pass
        else:raise RuntimeError('remote_install_leaked_server:'+name)
    async with httpx.AsyncClient(base_url=sys.argv[1],trust_env=False,timeout=30) as witness:
        response=await witness.get(
            '/api/v1/capabilities',headers={'Authorization':'Bearer '+sys.argv[2]})
        if response.status_code!=200 or response.headers.get('x-tether-instance')!=sys.argv[3]:
            raise RuntimeError('installed_remote_instance_mismatch')
    async with await RemoteClient.open(RemoteClientSettings(
        base_url=sys.argv[1],credential_provider=credential,
    )) as client:
        session=await client.create(spec='demo',title='Installed remote')
        if (await client.get(session.id)).summary.title!='Installed remote':
            raise RuntimeError('remote_installed_read_failed')
        handle=await client.send(session.id,'Fabricated installed request',spec='demo')
        result=await handle.result()
        if result.messages[-1].content!='Installed remote answer':
            raise RuntimeError('remote_installed_result_failed')
        if await client.turn_result(session.id,handle.turn_id)!=result:
            raise RuntimeError('remote_installed_result_lookup_failed')
        if not (await client.history(session.id)).items:
            raise RuntimeError('remote_installed_history_failed')
        if (await client.close(session.id)).status!='closed':
            raise RuntimeError('remote_installed_close_failed')
    print('Remote-only installed client executed against separate installed API')
asyncio.run(main())
"""
