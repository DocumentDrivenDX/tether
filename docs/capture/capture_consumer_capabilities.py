"""Capture the real development API demonstration; all data/model frames are fabricated."""

import asyncio
import hashlib
import json
import runpy
import tempfile
import platform
from importlib import metadata
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Header, RichLog, Static

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "consumer_capabilities.py"
exercise = runpy.run_path(str(EXAMPLE))["exercise"]


class ConsumerDemo(App):
    TITLE = "Tether · Consumer capabilities · Development preview"
    CSS = "Screen { background: #101c19; } RichLog { height: 1fr; padding: 1 2; } Static { height: auto; padding: 1 2; color: #a6c4aa; }"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Actual public Client operations · fabricated scripted model · no service egress")
        yield RichLog(wrap=True, markup=False)
        yield Static("Development API demonstration. This is not the released 0.1.0 reference TUI.")


async def main():
    output = ROOT / "docs" / "media"
    app = ConsumerDemo()
    frames = []
    async with asyncio.timeout(30), app.run_test(size=(110, 25)) as pilot:
        async def report(text):
            app.query_one(RichLog).write(text)
            await pilot.pause()
            filename = "consumer-" + str(len(frames) + 1).zfill(2) + ".svg"
            svg = "\n".join(line.rstrip() for line in app.export_screenshot().splitlines()) + "\n"
            (output / filename).write_text(svg)
            frames.append({"file": filename, "sha256": hashlib.sha256(svg.encode()).hexdigest(), "asserted_state": text})

        with tempfile.TemporaryDirectory(prefix="tether-consumer-capture-") as directory:
            result = await exercise(Path(directory), report)
    sources = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted((ROOT / "src" / "tether").rglob("*.py"))}
    receipt = {"python": platform.python_version(), "platform": platform.system() + "/" + platform.machine(),
               "package_version": metadata.version("novirhiza-tether"),
               "runtime_source_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
               "runtime_source_files": sources,"profile": "development source; fabricated API integration viewer",
               "capture_method": "real Textual headless application SVG export; executed public Client calls",
               "example_sha256": hashlib.sha256(EXAMPLE.read_bytes()).hexdigest(),
               "release_claim": "none; PyPI 0.1.0 is unchanged", "result": result, "frames": frames}
    (output / "consumer-capture-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
