"""Run fabricated reference-UI scenarios against an explicit local model profile.
Usage: python capture/capture_local_qwen.py /operator/path/model.json
The profile, vocabulary, credentials and store are never copied into this Site.
"""
import asyncio
import hashlib
import importlib.metadata as metadata
import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from textual.widgets import Input
from tether.cli.profile import load_profile
from tether.tui import DemoApp

OUTPUT = Path(__file__).resolve().parents[1] / 'media'


async def capture(profile_path):
    assert metadata.version('novirhiza-tether') == '0.1.0'
    profile = await load_profile(profile_path)
    assert urlsplit(profile.base_url).hostname in {'127.0.0.1', 'localhost', '::1'}
    frames = []

    async def save(app, pilot, name, description):
        await pilot.pause()
        path = OUTPUT / f'{name}.svg'
        path.write_text(app.export_screenshot(title='Tether · local Qwen model'))
        frames.append({'file': path.name, 'description': description,
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    with tempfile.TemporaryDirectory(prefix='tether-local-fabricated-') as directory:
        app = DemoApp(Path(directory), model_config=profile_path)
        async with app.run_test(size=(110, 30)) as pilot:
            assert app.client and app.session_id
            app.query_one('#message', Input).value = 'Reply with exactly READY. Do not call any tools.'
            await pilot.click('#send')
            await pilot.pause()
            assert (await app.client.get(app.session_id)).summary.status == 'idle'
            page = await app.client.history(app.session_id)
            contents = [slot.value.record.message.content for slot in page.items
                        if slot.value.status == 'ready' and slot.value.record
                        and slot.value.record.message and slot.value.record.message.role == 'assistant']
            assert any(value and value.strip() == 'READY' for value in contents)
            assert await app.client.pending(app.session_id) is None
            await save(app, pilot, 'qwen-01-ready', 'The live local Qwen model responds READY.')
            app.query_one('#message', Input).value = 'Call record_demo with label LANTERN. This is fabricated demo data. Do not claim it was recorded until the tool runs.'
            await pilot.click('#send')
            await pilot.pause()
            pending = await app.client.pending(app.session_id)
            assert pending is not None and pending.tool == 'record_demo'
            await save(app, pilot, 'qwen-02-approval', 'A live local model proposal is held for approval.')
            await pilot.click('#approve')
            await pilot.pause()
            assert await app.client.pending(app.session_id) is None
            assert (await app.client.get(app.session_id)).summary.status == 'idle'
            await save(app, pilot, 'qwen-03-approved', 'Explicit approval completes the live local-model journey.')
    receipt = {'scenario': 'local-qwen-text-tool-approval', 'version': '0.1.0',
               'model': profile.model_name, 'server': 'Local DS4 OpenAI-compatible endpoint',
               'python': '3.11.17', 'platform': 'macOS arm64',
               'data': 'fabricated', 'network': 'explicit local endpoint',
               'capture': 'Real Textual headless UI exports; actual live inference',
               'limits': {'output_tokens': profile.output_tokens,
                          'max_response_bytes': profile.max_response_bytes,
                          'safety_margin_tokens': profile.safety_margin_tokens},
               'assertions': ['READY assistant response', 'no pending tool after text',
                              'record_demo pending before approval', 'no pending tool after approval',
                              'idle after approval'], 'frames': frames}
    (OUTPUT / 'qwen-capture-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'captured': len(frames), 'model': profile.model_name}))


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(capture(Path(sys.argv[1]).resolve()), timeout=120))
