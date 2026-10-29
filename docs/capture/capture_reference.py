"""Capture the installed offline Tether UI with fabricated inputs and denied network.
Run with Python from an environment containing novirhiza-tether[tui]==0.1.0.
"""
import asyncio
import hashlib
import importlib.metadata as metadata
import json
import socket
import tempfile
from pathlib import Path

from textual.widgets import Button, Input
from tether.tui import DemoApp

OUTPUT = Path(__file__).resolve().parents[1] / 'media'


def deny_network(*args, **kwargs):
    raise AssertionError('Offline capture attempted network access')


async def capture():
    assert metadata.version('novirhiza-tether') == '0.1.0'
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frames = []

    async def save(app, pilot, name, description):
        await pilot.pause()
        image = app.export_screenshot(title='Tether · offline reference client')
        path = OUTPUT / f'{name}.svg'
        path.write_text(image)
        frames.append({'file': path.name, 'description': description,
                       'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    with tempfile.TemporaryDirectory(prefix='tether-microsite-fabricated-') as directory:
        root = Path(directory)
        app = DemoApp(root)
        async with app.run_test(size=(110, 30)) as pilot:
            assert app.client and app.session_id
            session_id = app.session_id
            await save(app, pilot, '01-ready', 'A fresh durable offline demo session.')
            app.query_one('#message', Input).value = 'Record the fabricated release checklist.'
            await pilot.click('#send')
            await pilot.pause()
            assert await app.client.pending(session_id) is not None
            assert not app.query_one('#approve', Button).disabled
            await save(app, pilot, '02-approval', 'The tool call waits for explicit approval.')
            await pilot.click('#approve')
            await pilot.pause()
            assert await app.client.pending(session_id) is None
            assert (await app.client.get(session_id)).summary.status == 'idle'
            await save(app, pilot, '03-approved', 'Explicit approval completes the fabricated tool journey.')
        reopened = DemoApp(root)
        async with reopened.run_test(size=(110, 30)) as pilot:
            assert reopened.client and reopened.session_id == session_id
            assert await reopened.client.pending(session_id) is None
            assert (await reopened.client.get(session_id)).summary.status == 'idle'
            page = await reopened.client.history(session_id)
            assert len(page.items) >= 3
            await save(reopened, pilot, '04-reopened', 'Reopening the same store restores the completed conversation.')
    receipt = {'scenario': 'offline-approval-reopen', 'package': 'novirhiza-tether',
               'version': '0.1.0', 'python': '3.11.17', 'platform': 'macOS arm64',
               'capture': 'Real Textual headless UI export, 110 × 30 cells',
               'data': 'fabricated', 'network': 'denied',
               'assertions': ['pending approval before confirmation', 'same session after reopen',
                              'completed history restored', 'explicit approve settles pending state',
                              'idle after approval'], 'frames': frames}
    (OUTPUT / 'capture-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'captured': len(frames), 'scenario': receipt['scenario']}))


if __name__ == '__main__':
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    asyncio.run(capture())
