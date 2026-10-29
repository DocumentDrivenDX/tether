"""Fabricated local effects and bounded web responses for reference tools."""

from __future__ import annotations

import asyncio
import json
from http.client import HTTPMessage
from io import BytesIO
from urllib.request import Request

import pytest

from tether.cli.common_tools import (
    BashArguments,
    BashTool,
    PageArguments,
    PageTool,
    ReadArguments,
    ReadFileTool,
    SearchArguments,
    SearchTool,
    WriteArguments,
    WriteFileTool,
    common_tools,
)
from tether.cli.web_tools import MAX_WEB_BYTES, Redirects, load_page, search_web, validate_url
from tether.types.execution import Actor, ToolContext


@pytest.fixture
def ctx():
    async def retrieve(*_):
        raise AssertionError("unused")

    return ToolContext(actor=Actor(id="fabricated"), invocation_id="invocation", retrieve=retrieve)


def test_file_tools_roundtrip_bounds_and_escape(tmp_path, ctx):
    writer, reader = WriteFileTool(tmp_path), ReadFileTool(tmp_path)
    result = json.loads(writer.run(ctx, WriteArguments(path="sample.txt", content="seedling 🌱")))
    assert result["bytes_written"] == len("seedling 🌱".encode())
    assert reader.run(ctx, ReadArguments(path="sample.txt")) == "seedling 🌱"
    writer.run(ctx, WriteArguments(path="sample.txt", content="replacement"))
    assert (tmp_path / "sample.txt").read_text() == "replacement"
    with pytest.raises(ValueError, match="path_outside_workspace"):
        writer.run(ctx, WriteArguments(path="../escape", content="refused"))
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="path_outside_workspace"):
        reader.run(ctx, ReadArguments(path="link/escape"))
    (tmp_path / "large").write_bytes(b"x" * 65537)
    with pytest.raises(ValueError, match="file_bytes_exceeded"):
        reader.run(ctx, ReadArguments(path="large"))
    with pytest.raises(ValueError, match="file_bytes_exceeded"):
        writer.run(ctx, WriteArguments(path="large", content="🌱" * 20000))
    assert (tmp_path / "large").stat().st_size == 65537


def test_tools_are_pinned_and_always_require_approval(tmp_path, ctx):
    tools = common_tools(tmp_path)
    assert [tool.name for tool in tools] == [
        "bash",
        "read_file",
        "write_file",
        "web_search",
        "web_page_load",
    ]
    for tool in tools:
        assert tool.always_ask
        assert tool.asks_first(ctx, ReadArguments(path="example"))
        assert tool.traits == {"workspace": str(tmp_path)}
    assert tools[0].replay == tools[2].replay == "unsafe"
    assert tools[1].effect == tools[3].effect == tools[4].effect == "read_only"


@pytest.mark.anyio
async def test_bash_cwd_errors_deadline_and_output_cap(tmp_path, ctx):
    tool = BashTool(tmp_path)
    result = json.loads(await tool.run(ctx, BashArguments(command="pwd; printf error >&2; exit 7")))
    assert result["status"] == "complete"
    assert result["exit_code"] == 7
    assert str(tmp_path) in result["output"]
    assert "error" in result["output"]
    result = json.loads(await tool.run(ctx, BashArguments(command="sleep 10", timeout_seconds=1)))
    assert result["status"] == "timeout"
    result = json.loads(await tool.run(ctx, BashArguments(command="yes seedling")))
    assert result["status"] == "output_limit"
    assert len(result["output"].encode()) == 65536


@pytest.mark.anyio
async def test_bash_cancellation_stops_descendant(tmp_path, ctx):
    tool = BashTool(tmp_path)
    marker = tmp_path / "should-not-exist"
    task = asyncio.create_task(
        tool.run(ctx, BashArguments(command="sleep 1; touch should-not-exist"))
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.1)
    assert not marker.exists()


def test_page_and_search_extractors(monkeypatch, tmp_path, ctx):
    from tether.cli import web_tools

    html = (
        "<html><style>hidden</style><h1>Seedling</h1><script>hidden</script><p>Text &amp; more</p>"
    )
    monkeypatch.setattr(web_tools, "fetch_text", lambda url: (url, "text/html", html, False))
    page = json.loads(PageTool(tmp_path).run(ctx, PageArguments(url="https://example.org")))
    assert page["text"] == "Seedling\nText & more"
    assert not page["truncated"]
    html = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org">'
        'A <b>seedling</b></a><a class="result__snippet">A snippet</a>'
    )
    result = json.loads(SearchTool(tmp_path).run(ctx, SearchArguments(query="seedling")))
    assert result["results"] == [
        {"title": "A seedling", "url": "https://example.org", "snippet": "A snippet"}
    ]
    html = (
        '<a class="result-link" href="https://example.org">Lite result</a>'
        '<td class="result-snippet">Lite <b>snippet</b></td>'
    )
    assert json.loads(search_web("query"))["results"] == [
        {"title": "Lite result", "url": "https://example.org", "snippet": "Lite snippet"}
    ]
    html = "blocked"
    assert json.loads(search_web("query"))["notice"] is not None
    html = "x" * 40000
    assert json.loads(load_page("https://example.org"))["truncated"]


@pytest.mark.parametrize(
    "url", ["file:///tmp/example", "ftp://example.org", "https://u:p@example.org", "http://"]
)
def test_invalid_urls_and_redirects_refuse(url):
    with pytest.raises(ValueError, match="http_url_required"):
        validate_url(url)
    with pytest.raises(ValueError, match="http_url_required"):
        Redirects().redirect_request(
            Request("https://example.org"), BytesIO(), 302, "", HTTPMessage(), url
        )


def test_fetch_bounds_and_media_refusal(monkeypatch):
    from email.message import Message

    from tether.cli import web_tools

    class Response:
        headers = Message()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            assert size == MAX_WEB_BYTES + 1
            return b"x" * size

        def geturl(self):
            return "https://example.org/final"

        def open(self, request, timeout):
            assert timeout == 10
            return self

    response = Response()
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    monkeypatch.setattr(web_tools, "build_opener", lambda *args: response)
    url, kind, text, truncated = web_tools.fetch_text("https://example.org")
    assert url.endswith("/final")
    assert kind == "text/plain"
    assert truncated
    assert len(text) == MAX_WEB_BYTES
    response.headers.replace_header("Content-Type", "application/octet-stream")
    with pytest.raises(ValueError, match="unsupported_page_content_type"):
        web_tools.fetch_text("https://example.org")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("approved", [True, False])
async def test_real_client_gates_common_write_with_scripted_model(tmp_path, monkeypatch, approved):
    from test_reference_model_profile import write_profile

    from tether.cli import demo
    from tether.cli.profile import load_profile
    from tether.context.testing import fabricated_context
    from tether.models.testing import ScriptedModel
    from tether.types import ModelResponse, ToolCall
    from tether.types.execution import AgentSpec, Answer

    class Model(ScriptedModel):
        async def aclose(self):
            pass

    async def configure(profile, limits, names):
        context = fabricated_context()
        model = Model(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id="fabricated-write",
                            name="write_file",
                            arguments={"path": "seedling.txt", "content": "fabricated"},
                        ),
                    ),
                    finish_reason="tool_calls",
                ),
                ModelResponse(content="Finished the fabricated turn.", finish_reason="stop"),
            ]
        )
        spec = AgentSpec(
            id="common-tools",
            version="1",
            title="Common tools",
            instructions="Fabricated fixture",
            model="demo",
            tools=names,
        )
        return model, context["context_config"], context["estimator"], Actor(id="demo"), spec

    monkeypatch.setattr(demo, "configured_model", configure)
    config = write_profile(tmp_path, 1)
    settings = json.loads(config.read_text())
    settings["workspace"] = "."
    config.write_text(json.dumps(settings))
    profile = await load_profile(config)
    assert profile.workspace == tmp_path
    async with demo.open_demo(tmp_path / "journal", profile=profile) as (app, actor, _):
        client = app.client(actor)
        session = await client.create(ref=profile.session_ref)
        result = await (await client.send(session.id, "Write fabricated data")).result()
        assert result.stop_reason == "confirmation_required"
        assert not (tmp_path / "seedling.txt").exists()
        pending = await client.pending(session.id)
        assert pending is not None
        assert pending.arguments == {"path": "seedling.txt", "content": "fabricated"}
        handle = await client.confirm(session.id, pending.invocation_id, Answer(approved=approved))
        assert handle is not None
        assert (await handle.result()).stop_reason == "final"
        assert (tmp_path / "seedling.txt").exists() == approved


@pytest.mark.anyio
async def test_workspace_selection_changes_identity_and_refuses_missing_directory(tmp_path):
    from test_reference_model_profile import write_profile

    from tether.cli.demo import open_demo
    from tether.cli.profile import load_profile

    config = write_profile(tmp_path, 1)
    offline_tools_profile = await load_profile(config)
    settings = json.loads(config.read_text())
    settings["workspace"] = "missing"
    config.write_text(json.dumps(settings))
    profile = await load_profile(config)
    assert profile.session_ref != offline_tools_profile.session_ref
    with pytest.raises(ValueError, match="workspace_directory_required"):
        async with open_demo(tmp_path / "journal", profile=profile):
            pytest.fail("Missing workspace must refuse before model setup")
