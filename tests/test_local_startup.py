"""One-command startup uses bounded discovery and the normal native model/approval path."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_compatible_loopback import wire_response
from textual.widgets import Input

from tether.cli import local
from tether.cli.command import parser
from tether.cli.local import LocalModelOptions, ServedModel, local_profile, options
from tether.tui import DemoApp

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_options_enable_current_workspace_and_validate_combinations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = parser().parse_args(["--openai-api-baseurl=http://localhost:18001/v1"])
    chosen = options(args)
    assert chosen is not None
    assert chosen.workspace == tmp_path
    assert chosen.authorization_file is None
    assert chosen.output_tokens == 1024
    assert options(parser().parse_args([])) is None
    for argv in (
        ["--model", "explicit"],
        ["--output-tokens", "0"],
        ["--model-config", "profile.json", "--base-url", "http://localhost:1/v1"],
        ["--base-url", "http://localhost:1/v1", "--output-tokens", "0"],
    ):
        with pytest.raises(ValueError, match=r"require|Choose|greater"):
            options(parser().parse_args(argv))


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/model",
        "http://example.org/v1",
        "http://user@example.org/v1",
        "http://localhost/v1?q=x",
    ],
)
def test_anonymous_shortcut_requires_explicit_loopback_url(url, tmp_path):
    with pytest.raises(ValueError, match=r"HTTP|loopback"):
        LocalModelOptions(base_url=url, workspace=tmp_path)


async def test_discovery_profiles_and_explicit_overrides(tmp_path, monkeypatch):
    def metadata(*_):
        return [
            ServedModel(id="reasoner"),
            ServedModel(
                id="fixture-chat", context_length=64000, top_provider={"max_completion_tokens": 512}
            ),
        ]

    monkeypatch.setattr(local, "model_metadata", metadata)
    selected = LocalModelOptions(base_url="http://localhost:1/v1", workspace=tmp_path)
    profile = await local_profile(selected)
    assert profile.model_name == "fixture-chat"
    assert profile.context_window_tokens == 64000
    assert profile.context_source == "server"
    assert profile.output_tokens == 512
    assert profile.identity_mode == "anonymous"
    assert profile.authorization_file is None
    assert profile.tokenizer_profile is None
    other = await local_profile(selected.model_copy(update={"model_name": "reasoner"}))
    assert other.context_source == "starter"
    assert other.context_window_tokens == 16384
    override = await local_profile(selected.model_copy(update={"context_window_tokens": 32000}))
    assert override.context_source == "configured"
    assert override.context_window_tokens == 32000
    with pytest.raises(ValueError, match="Context budget"):
        await local_profile(selected.model_copy(update={"context_window_tokens": 1024}))


async def test_missing_discovery_needs_model_and_preserves_an_explicit_model(tmp_path, monkeypatch):
    def fail(*_):
        raise OSError("fabricated outage")

    monkeypatch.setattr(local, "model_metadata", fail)
    selected = LocalModelOptions(base_url="http://localhost:1/v1", workspace=tmp_path)
    with pytest.raises(ValueError, match="Cannot discover"):
        await local_profile(selected)
    profile = await local_profile(selected.model_copy(update={"model_name": "explicit"}))
    assert profile.model_name == "explicit"
    assert profile.context_source == "starter"


class LocalPeer:
    def __init__(self):
        self.seen = []

    async def handle(self, reader, writer):
        try:
            lines = (await reader.readuntil(b"\r\n\r\n")).decode("ascii").split("\r\n")
            headers = dict(line.lower().split(": ", 1) for line in lines[1:] if line)
            data = await reader.readexactly(int(headers.get("content-length", "0")))
            request = (lines[0], headers, json.loads(data) if data else {})
            self.seen.append(request)
            body = (
                json.dumps({"data": [{"id": "fabricated-chat", "context_length": 64000}]}).encode()
                if request[0].startswith("GET ")
                else wire_response()
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def test_url_only_tui_discovers_and_answers_without_credentials(tmp_path):
    peer = LocalPeer()
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        chosen = LocalModelOptions(
            base_url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1", workspace=tmp_path
        )
        app = DemoApp(tmp_path / "journal", local_options=chosen)
        async with asyncio.timeout(20), app.run_test() as pilot:
            assert app.client is not None
            assert app.session_id is not None
            assert app.sub_title == "fabricated-chat"
            app.query_one("#message", Input).value = "Fabricated local request"
            await pilot.click("#send")
            await pilot.pause()
            assert (await app.client.get(app.session_id)).summary.status == "idle"
            history = await app.client.history(app.session_id)
            assert any(
                slot.value.record
                and slot.value.record.message
                and slot.value.record.message.content == "Fabricated answer."
                for slot in history.items
            )
        assert len(peer.seen) == 2
        _, headers, body = peer.seen[1]
        assert "authorization" not in headers
        assert body["model"] == "fabricated-chat"
        assert body["max_tokens"] == 1024
        assert {tool["function"]["name"] for tool in body["tools"]} == {
            "record_demo",
            "bash",
            "read_file",
            "write_file",
            "web_search",
            "web_page_load",
        }
    finally:
        server.close()
        await server.wait_closed()
