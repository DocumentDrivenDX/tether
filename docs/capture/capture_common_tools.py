"""Reproduce a fabricated common-tool UI journey against the current source checkout.

Run: .venv/bin/python docs/capture/capture_common_tools.py
This uses a scripted model and the real file tool, engine, Client and Textual UI.
It does not exercise a live model or search service.
"""
import asyncio
import hashlib
import json
import platform
import tempfile
from pathlib import Path
from unittest.mock import patch

from textual.widgets import Button, Input, RichLog
from tether.cli import demo
from tether.cli.common_tools import WriteFileTool
from tether.models.testing import ScriptedModel
from tether.tui import DemoApp
from tether.types import ModelResponse, ToolCall

OUTPUT = Path(__file__).resolve().parents[1] / "media"


async def capture():
    frames = []
    with tempfile.TemporaryDirectory(prefix="tether-fabricated-tools-") as directory:
        workspace = Path(directory).resolve()

        class FileTool(WriteFileTool):
            def __init__(self):
                super().__init__(workspace)

        def model(**kwargs):
            return ScriptedModel([
                ModelResponse(tool_calls=(ToolCall(
                    id="fabricated-write", name="write_file",
                    arguments={"path": "seedling.txt", "content": "Fabricated seedling"},
                ),), finish_reason="tool_calls"),
                ModelResponse(content="The fabricated file operation is complete.", finish_reason="stop"),
            ])

        spec = demo.DEMO_SPEC.model_copy(update={"tools": ("write_file",)})
        with patch.object(demo, "DemoTool", FileTool), patch.object(demo, "demo_model", model), patch.object(demo, "DEMO_SPEC", spec):
            app = DemoApp(workspace / "journal")
            async with app.run_test(size=(110, 30)) as pilot:
                app.title = "Tether · fabricated common tool fixture"
                app.query_one("#message", Input).value = "Write the fabricated seedling file."
                await pilot.click("#send")
                await pilot.pause()
                assert not (workspace / "seedling.txt").exists()
                assert not app.query_one("#approve", Button).disabled
                assert any("seedling.txt" in str(line) for line in app.query_one("#transcript", RichLog).lines)
                original = app.session_id
                for decision in ("pending", "approved", "new-session"):
                    if decision == "approved":
                        await pilot.click("#approve")
                        await pilot.pause()
                        assert (workspace / "seedling.txt").read_text() == "Fabricated seedling"
                        assert app.query_one("#approve", Button).disabled
                    if decision == "new-session":
                        await pilot.click("#new-session")
                        await pilot.pause()
                        assert app.session_id is not None and app.session_id != original
                        assert len(app.sessions) == 2
                        assert (await app.client.history(app.session_id)).items == ()
                        assert app.focused is app.query_one("#message", Input)
                    path = OUTPUT / f"common-tools-{decision}.svg"
                    path.write_text(app.export_screenshot(title="Tether · common tools"))
                    frames.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    receipt = {
        "scenario": "common-write-approval", "source": "working tree; unreleased",
        "python": platform.python_version(), "platform": platform.system() + " " + platform.machine(),
        "capture": "Actual Textual headless SVG export, 110 x 30 cells",
        "model": "fabricated scripted fixture", "network": "not used",
        "assertions": ["arguments visible before approval", "no file before approval", "approved file content exact", "approval settled", "new session is distinct and empty", "composer focused"],
        "limits": "File tool only; no live provider/search evidence or sandbox claim",
        "frames": frames,
    }
    (OUTPUT / "common-tools-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print("Captured and verified common-tool approval screenshots")


if __name__ == "__main__":
    asyncio.run(capture())
