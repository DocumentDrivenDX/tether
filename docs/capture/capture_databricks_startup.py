"""Live profile-only TUI startup with fabricated prompts and no published host/profile.

Run with a signed-in CLI profile: .venv/bin/python docs/capture/capture_databricks_startup.py PROFILE MODEL
Only public system.ai model names may be selected for this capture.
"""
import asyncio
import hashlib
import json
import platform
import sys
import tempfile
from pathlib import Path

from textual.widgets import Input, Select
from tether.cli.command import parser
from tether.cli.local import options
from tether.tui import DemoApp

OUTPUT = Path(__file__).resolve().parents[1] / "media"


async def capture(profile, model):
    assert model.startswith("system.ai.")
    with tempfile.TemporaryDirectory(prefix="tether-gateway-") as directory:
        workspace = Path(directory)
        args = parser().parse_args(["--databricks-profile", profile, "--workspace", str(workspace)])
        app = DemoApp(workspace / "journal", local_options=options(args))
        async with app.run_test(size=(110, 34)) as pilot:
            assert app.client is None
            choice = app.screen.query_one("#model-choice", Select)
            count = len(choice._options)
            choice.value = model
            app.save_screenshot("databricks-model-picker.svg", path=str(OUTPUT))
            await pilot.click("#start-model")
            await pilot.pause()
            assert app.client is not None and app.session_id
            assert app.sub_title == model
            app.query_one("#message", Input).value = "Reply with exactly READY. Do not call tools."
            original_finish = app._finish

            async def finish(handle):
                result = await handle.result()
                print("Live turn:", result.stop_reason, result.reason, flush=True)
                await original_finish(handle)

            app._finish = finish
            await pilot.click("#send")
            await pilot.pause()
            page = await app.client.history(app.session_id)
            answers = [slot.value.record.message.content for slot in page.items
                       if slot.value.record and slot.value.record.message
                       and slot.value.record.message.role == "assistant"]
            assert answers == ["READY"], "Live response mismatch: " + repr(answers) + "; " + str((await app.client.get(app.session_id)).summary.status)
            assert (await app.client.get(app.session_id)).summary.status == "idle"
            app.save_screenshot("databricks-startup-ready.svg", path=str(OUTPUT))
    frames = []
    for name in ("databricks-model-picker.svg", "databricks-startup-ready.svg"):
        data = (OUTPUT / name).read_bytes()
        assert profile.encode() not in data
        frames.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
    receipt = {
        "scenario": "Profile-only Databricks startup, model choice and fabricated READY response",
        "source": "unreleased working tree",
        "python": platform.python_version(), "platform": platform.system(),
        "discovered_chat_models": count, "model": model,
        "assertions": ["No URL supplied", "Profile host and auth derived by installed CLI",
                       "Model picker shown before session opened", "Selected model answered READY",
                       "Session idle after accepted response", "Profile and workspace host excluded from capture"],
        "limits": "system.ai metadata subset; no invocation guarantee for other listed models; starter context budget",
        "frames": frames,
    }
    (OUTPUT / "databricks-startup-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    asyncio.run(capture(*sys.argv[1:]))
