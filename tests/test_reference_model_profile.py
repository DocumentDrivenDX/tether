"""Opt-in reference model configuration crosses the governed local wire boundary."""

from __future__ import annotations

import asyncio
import json

import pytest
from test_compatible_loopback import read_request, wire_response
from test_wire_estimator import profile as tokenizer_profile
from test_wire_estimator import vocabulary

from tether.cli.command import execute, parser
from tether.cli.demo import load_profile, open_demo
from tether.tui import DemoApp
from tether.types.canonical import digest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def write_profile(tmp_path, port: int, *, max_response_bytes: int | None = None):
    vocab = tmp_path / "vocabulary.txt"
    vocab.write_bytes(vocabulary())
    authorization = tmp_path / "authorization.txt"
    authorization.write_text(" ".join(("Bearer", "fabricated")), encoding="utf-8")
    config = tmp_path / "model.json"
    settings = dict(
        id="local-fixture",
        base_url=f"http://127.0.0.1:{port}/v1",
        model_name="fabricated-wire-model",
        authorization_file=authorization.name,
        output_limit_field="max_tokens",
        context_window_tokens=100000,
        output_tokens=256,
        safety_margin_tokens=128,
        tokenizer_profile=tokenizer_profile().model_dump(mode="json"),
        vocabulary_path=vocab.name,
    )
    if max_response_bytes is not None:
        settings["max_response_bytes"] = max_response_bytes
    config.write_text(json.dumps(settings), encoding="utf-8")
    return config


class Peer:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict[str, str], dict]] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.seen.append(await read_request(reader))
            payload = wire_response()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"X-Request-Id: fabricated-header\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


class ManyFramesPeer:
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await read_request(reader)

            def frame(delta: dict, finish: str | None = None) -> bytes:
                value = {
                    "id": "fabricated-many-frames",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fabricated-wire-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return b"data: " + json.dumps(value).encode() + b"\n\n"

            payload = (
                frame({"role": "assistant"})
                + frame({"content": "x"}) * 80
                + frame({}, "stop")
                + b"data: [DONE]\n\n"
            )
            assert len(payload) > 8192
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                b"Content-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


async def test_reference_profile_allows_bounded_many_frame_stream(tmp_path):
    peer = ManyFramesPeer()
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        config = write_profile(
            tmp_path, server.sockets[0].getsockname()[1], max_response_bytes=32768
        )
        loaded = await load_profile(config)
        legacy = loaded.model_dump(
            mode="json",
            exclude={"workspace", "identity_mode", "context_source", "databricks_profile"},
        )
        assert loaded.session_ref == "model:" + loaded.id + ":" + digest(legacy)[:16]
        async with open_demo(tmp_path / "journal", profile=loaded) as (app, actor, _tool):
            session = await app.client(actor).create(ref=loaded.session_ref)
            result = await (await app.client(actor).send(session.id, "Fabricated input")).result()
            assert result.stop_reason == "final"
            assert result.messages[-1].content == "x" * 80
    finally:
        server.close()
        await server.wait_closed()


async def test_reference_profile_default_response_cap_refuses_many_frames(tmp_path):
    peer = ManyFramesPeer()
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        config = write_profile(tmp_path, server.sockets[0].getsockname()[1])
        loaded = await load_profile(config)
        async with open_demo(tmp_path / "journal", profile=loaded) as (app, actor, _tool):
            session = await app.client(actor).create(ref=loaded.session_ref)
            result = await (await app.client(actor).send(session.id, "Fabricated input")).result()
            assert result.stop_reason == "model_unavailable"
    finally:
        server.close()
        await server.wait_closed()


async def test_reference_profile_rejects_unbounded_response_cap(tmp_path):
    config = write_profile(tmp_path, 1, max_response_bytes=1024 * 1024 + 1)
    with pytest.raises(ValueError, match="invalid_model_config"):
        await load_profile(config)


async def test_reference_config_selects_real_adapter_without_changing_offline_default(
    tmp_path, capsys
):
    peer = Peer()
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        config = write_profile(tmp_path, port)
        loaded = await load_profile(config)
        assert loaded.session_ref.startswith("model:local-fixture:")
        root = tmp_path / "journal"
        async with open_demo(root, profile=loaded) as (app, actor, _tool):
            session = await app.client(actor).create(ref=loaded.session_ref)
            result = await (await app.client(actor).send(session.id, "Fabricated input")).result()
            assert result.stop_reason == "final"
            assert result.messages[-1].content == "Fabricated answer."
        assert len(peer.seen) == 1
        line, headers, body = peer.seen[0]
        assert line.startswith("POST /v1/chat/completions ")
        assert headers["authorization"].startswith("Bearer ")
        assert body["model"] == "fabricated-wire-model"
        assert body["max_tokens"] == 256
        await execute(
            parser().parse_args(["--root", str(root), "--model-config", str(config), "list"])
        )
        assert session.id in capsys.readouterr().out
        assert len(peer.seen) == 1
        app = DemoApp(root, model_config=config)
        async with asyncio.timeout(20), app.run_test():
            assert app.session_id == session.id
            assert app.title == "Tether · local-fixture"
        assert len(peer.seen) == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_reference_config_refuses_missing_authorization_before_egress(tmp_path):
    peer = Peer()
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        config = write_profile(tmp_path, server.sockets[0].getsockname()[1])
        loaded = await load_profile(config)
        assert loaded.authorization_file is not None
        loaded.authorization_file.unlink()
        async with open_demo(tmp_path / "journal", profile=loaded) as (app, actor, _tool):
            session = await app.client(actor).create(ref=loaded.session_ref)
            result = await (await app.client(actor).send(session.id, "Fabricated input")).result()
            assert result.stop_reason != "final"
        assert not peer.seen
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("approved", [True, False])
async def test_common_write_tool_requires_exact_approval(tmp_path, monkeypatch, approved):
    import test_reference_model_profile as this_module

    from tether.types.execution import Answer

    peer = Peer()
    original = wire_response()
    proposal = json.loads(original)
    proposal["choices"][0].update(
        finish_reason="tool_calls",
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "fabricated-write",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "seedling.txt", "content": "fabricated seedling"}
                        ),
                    },
                }
            ],
        },
    )
    responses = iter([json.dumps(proposal).encode(), original])
    monkeypatch.setattr(this_module, "wire_response", lambda: next(responses))
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        config = write_profile(tmp_path, server.sockets[0].getsockname()[1])
        settings = json.loads(config.read_text())
        settings["workspace"] = "."
        config.write_text(json.dumps(settings))
        loaded = await load_profile(config)
        assert loaded.workspace == tmp_path
        async with open_demo(tmp_path / "journal", profile=loaded) as (app, actor, _):
            client = app.client(actor)
            session = await client.create(ref=loaded.session_ref)
            result = await (await client.send(session.id, "Write a fabricated file")).result()
            assert result.stop_reason == "confirmation_required"
            assert not (tmp_path / "seedling.txt").exists()
            names = {item["function"]["name"] for item in peer.seen[0][2]["tools"]}
            assert names == {
                "record_demo",
                "bash",
                "read_file",
                "write_file",
                "web_search",
                "web_page_load",
            }
            pending = await client.pending(session.id)
            assert pending is not None
            assert pending.tool == "write_file"
            assert pending.arguments == {"path": "seedling.txt", "content": "fabricated seedling"}
            handle = await client.confirm(
                session.id, pending.invocation_id, Answer(approved=approved)
            )
            assert handle is not None
            assert (await handle.result()).stop_reason == "final"
            assert (tmp_path / "seedling.txt").exists() == approved
            if approved:
                assert (tmp_path / "seedling.txt").read_text() == "fabricated seedling"
    finally:
        server.close()
        await server.wait_closed()
