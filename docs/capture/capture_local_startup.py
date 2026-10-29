"""Test URL-only startup with real local inference and all five reference tools.

Run: .venv/bin/python docs/capture/capture_local_startup.py http://localhost:18001/v1
Uses fabricated prompts and a temporary workspace. It does not restart the server.
"""
import asyncio
import hashlib
import json
import platform
import sys
import tempfile
from pathlib import Path

from textual.widgets import Button, Input
from tether.cli.command import parser
from tether.cli.local import options
from tether.tui import DemoApp

OUTPUT = Path(__file__).resolve().parents[1] / "media"
CALLS = [
    ("write_file", {"path": "seedling.txt", "content": "Fabricated seedling"}),
    ("read_file", {"path": "seedling.txt"}),
    ("bash", {"command": "printf 'Fabricated shell output'", "timeout_seconds": 10}),
    ("web_search", {"query": "Python asyncio documentation"}),
    ("web_page_load", {"url": "https://example.com"}),
]


async def contents(app, role=None):
    messages, start = [], 0
    while True:
        page = await app.client.history(app.session_id, start=start)
        messages.extend(slot.value.record.message.content for slot in page.items
                        if slot.value.record and slot.value.record.message
                        and (role is None or slot.value.record.message.role == role))
        if page.next_start is None:
            return "\n".join(messages)
        start = page.next_start


async def capture(base_url):
    frames, assertions = [], []
    with tempfile.TemporaryDirectory(prefix="tether-local-tools-") as directory:
        workspace = Path(directory).resolve()
        args = parser().parse_args(["--openai-api-baseurl", base_url, "--workspace", str(workspace)])
        app = DemoApp(workspace / "journal", local_options=options(args))
        async with app.run_test(size=(110, 34)) as pilot:
            assert app.client and app.session_id
            model = app.sub_title
            app.query_one("#message", Input).value = "Reply with exactly READY. Do not call any tools."
            await pilot.click("#send")
            await pilot.pause()
            assert (await contents(app, "assistant")).strip() == "READY"
            assert await app.client.pending(app.session_id) is None
            assertions.append("URL-only discovery and anonymous live model answer")
            for name, arguments in CALLS:
                await pilot.press("ctrl+n")
                await pilot.pause()
                app.query_one("#message", Input).value = (
                    "This is fabricated test data. Call only the tool " + name
                    + " with exactly these arguments: " + json.dumps(arguments)
                    + ". After its result, reply DONE without any further tool calls."
                )
                await pilot.click("#send")
                await pilot.pause()
                pending = await app.client.pending(app.session_id)
                assert pending and pending.tool == name, (name, await contents(app))
                assert pending.arguments == arguments, (name, pending.arguments)
                if name == "write_file":
                    assert not (workspace / "seedling.txt").exists()
                path = OUTPUT / f"local-startup-{name}-pending.svg"
                path.write_text("\n".join(line.rstrip() for line in app.export_screenshot(title="Tether · local tool approval").splitlines()) + "\n")
                frames.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                await pilot.click("#approve")
                await pilot.pause()
                assert await app.client.pending(app.session_id) is None
                assert (await app.client.get(app.session_id)).summary.status == "idle"
                text = await contents(app, "tool")
                expected = {"write_file": "bytes_written", "read_file": "Fabricated seedling",
                            "bash": "Fabricated shell output", "web_search": '"results": [{',
                            "web_page_load": "Example Domain"}[name]
                assert expected in text, (name, text)
                assert app.query_one("#approve", Button).disabled
                assertions.append(name + ": exact pending arguments, approval, real result, idle")
                print("PASS", name, flush=True)
            assert (workspace / "seedling.txt").read_text() == "Fabricated seedling"
    receipt = {
        "scenario": "URL-only local model and common tools", "source": "unreleased working tree",
        "model": model, "endpoint": "explicit loopback HTTP endpoint",
        "python": platform.python_version(), "platform": platform.system() + " " + platform.machine(),
        "data": "fabricated prompts and temporary file; public web query/page",
        "capture": "Actual Textual headless SVG exports with real local inference",
        "assertions": assertions, "frames": frames,
        "limits": "One local server/model; approximate byte estimator; no broad provider or sandbox qualification",
    }
    (OUTPUT / "local-startup-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("PASS local startup and all five tools", flush=True)


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(capture(sys.argv[1]), timeout=240))
