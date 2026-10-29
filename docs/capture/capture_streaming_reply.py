"""Capture the real TUI between model fragments using the fabricated test provider.
Run from the checkout with its locked development environment, after UI tests pass.
"""

import asyncio
import hashlib
import json
import runpy
import tempfile
from importlib import metadata
from pathlib import Path

from textual.widgets import Input, Static

from tether.tui import DemoApp

ROOT = Path(__file__).resolve().parents[2]
fixtures = runpy.run_path(str(ROOT / "tests" / "test_tui_streaming.py"))
PausedReply, wait_reply = fixtures["PausedReply"], fixtures["wait_reply"]


async def capture():
    output = ROOT / "docs" / "media"
    frames = []
    with tempfile.TemporaryDirectory(prefix="tether-streaming-capture-") as directory:
        app, model = DemoApp(Path(directory), volatile=True), PausedReply()
        async with asyncio.timeout(20), app.run_test(size=(110, 30)) as pilot:
            if app.client is None:
                raise RuntimeError("Demo client is unavailable")
            app.client._service.engine.model = model
            app.query_one("#message", Input).value = "Fabricated streaming demonstration"
            sending = asyncio.create_task(app._send())
            reply = app.query_one("#reply", Static)
            try:
                await wait_reply(pilot, reply, "Hello  ▍")
                for name, expected in [
                    ("streaming-01-partial", "Hello  ▍"),
                    ("streaming-02-updated", "Hello world. ▍"),
                ]:
                    await wait_reply(pilot, reply, expected)
                    await pilot.pause()
                    path = output / f"{name}.svg"
                    image = app.export_screenshot(title="Tether · streaming reply preview")
                    path.write_text("\n".join(line.rstrip() for line in image.splitlines()) + "\n")
                    frames.append(
                        {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                    )
                    model.next_fragment.set()
            finally:
                model.next_fragment.set()
                model.finish.set()
                await sending
            if reply.display or reply.content != "":
                raise RuntimeError("Provisional reply remained after terminal acceptance")
    source = ROOT / "src/tether/tui/__init__.py"
    receipt = {
        "scenario": "in-place provisional reply",
        "package_version": metadata.version("novirhiza-tether"),
        "release_claim": "development source preview; not the 0.1.0 UI",
        "tui_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "model": "fabricated PausedReply test provider; no external inference",
        "capture": "Real Textual UI exported between accepted fragments",
        "assertions": ["one widget accumulates both fragments", "preview clears at terminal"],
        "frames": frames,
    }
    (output / "streaming-capture-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"captured": len(frames), "scenario": receipt["scenario"]}))


if __name__ == "__main__":
    asyncio.run(capture())
