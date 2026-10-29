"""Profile-only startup derives its connection and selects a discovered chat model."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from textual.widgets import Select

from tether.adapters.databricks import CliProfile, model_names
from tether.cli.command import parser
from tether.cli.local import local_profile, options
from tether.tui import DemoApp
from tether.types import JsonValue

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def host():
    return "https://" + "workspace.example.org"


class FakeCli(CliProfile):
    async def _command(self, *arguments: str) -> dict[str, JsonValue]:
        if arguments == ("auth", "describe"):
            return {"details": {"host": host()}}
        if arguments[:2] == ("auth", "token"):
            return {"access_token": "-".join(("fabricated", "credential"))}
        query = parse_qs(urlsplit(arguments[2]).query)
        assert query["parent"] == ["schemas/system.ai"]
        assert query["view"] == ["BASIC"]
        if "page_token" not in query:
            return {"model_services": [service("system.ai.alpha")], "next_page_token": "next"}
        return {"model_services": [service("system.ai.beta"), service("system.ai.image", "images")]}


def service(name, api="mlflow/v1/chat/completions"):
    return {"name": "model-services/" + name, "supported_api_types": [api]}


def test_profile_flag_needs_no_url_and_conflicts_are_clear(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selected = options(parser().parse_args(["--databricks-profile=work"]))
    assert selected is not None
    assert selected.databricks_profile == "work"
    assert selected.base_url == ""
    assert selected.workspace == tmp_path
    for extra in (
        ["--gateway", host()],
        ["--authorization-file", "authorization"],
        ["--model-config", "model.json"],
    ):
        with pytest.raises(ValueError, match=r"supplies|Choose"):
            options(parser().parse_args(["--databricks-profile=work", *extra]))


async def test_derive_discover_paginate_pin_and_authenticate(tmp_path, monkeypatch):
    monkeypatch.setattr("tether.adapters.databricks.CliProfile", FakeCli)
    selected = options(
        parser().parse_args(["--databricks-profile=work", "--workspace", str(tmp_path)])
    )
    assert selected is not None
    profile = await local_profile(selected)
    assert profile.base_url == host() + "/ai-gateway/mlflow/v1"
    assert profile.available_models == ("system.ai.alpha", "system.ai.beta")
    assert profile.identity_mode == "caller"
    assert profile.context_source == "starter"
    assert profile.databricks_profile == "work"
    assert "available_models" not in profile.model_dump()
    source = FakeCli("work", expected_host=host())
    value = await source.authorization()
    assert value == " ".join(("Bearer", "-".join(("fabricated", "credential"))))
    assert value is not None
    assert value not in profile.model_dump_json()
    with pytest.raises(RuntimeError, match="host changed"):
        await FakeCli("work", expected_host="https://" + "other.example.org").authorization()
    pinned = await local_profile(selected.model_copy(update={"model_name": "system.ai.beta"}))
    assert pinned.model_name == "system.ai.beta"
    assert pinned.available_models == ()
    with pytest.raises(ValueError, match="not discovered"):
        await local_profile(selected.model_copy(update={"model_name": "not-found"}))


async def test_model_picker_starts_selected_session_and_cancel_opens_no_store(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tether.adapters.databricks.CliProfile", FakeCli)
    selected = options(
        parser().parse_args(["--databricks-profile=work", "--workspace", str(tmp_path)])
    )
    app = DemoApp(tmp_path / "journal", local_options=selected)
    async with app.run_test(size=(110, 34)) as pilot:
        assert app.client is None
        app.screen.query_one("#model-choice", Select).value = "system.ai.beta"
        await pilot.click("#start-model")
        await pilot.pause()
        assert app.client is not None
        assert app.session_id is not None
        assert app.sub_title == "system.ai.beta"
    cancelled = DemoApp(tmp_path / "unused", local_options=selected)
    async with cancelled.run_test(size=(110, 34)) as pilot:
        await pilot.click("#cancel-model")
    assert cancelled.client is None
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("name", ["", "--profile", "a/b", "a\nb"])
def test_invalid_profile_name_refused(name):
    with pytest.raises(ValueError, match="profile name"):
        CliProfile(name)


def test_discovery_only_advertised_chat_services():
    assert model_names([service("system.ai.alpha"), service("system.ai.image", "images")]) == {
        "system.ai.alpha"
    }
    assert model_names([{"name": "model-services/system.ai.unknown"}]) == set()
    with pytest.raises(ValueError, match="name"):
        model_names([service("system.two")])


async def test_bad_host_and_invalid_pagination_refused(monkeypatch):
    async def invalid(self, *arguments):
        return {"details": {"host": host() + "/unexpected"}}

    monkeypatch.setattr(CliProfile, "_command", invalid)
    with pytest.raises(ValueError, match="origin"):
        await CliProfile("work").host()

    async def repeated(self, *arguments):
        if arguments == ("auth", "describe"):
            return {"details": {"host": host()}}
        return {"model_services": [], "next_page_token": "same"}

    monkeypatch.setattr(CliProfile, "_command", repeated)
    with pytest.raises(ValueError, match="pagination"):
        await CliProfile("work").models()


async def test_cli_runner_scrubs_failed_output_and_parses_success(tmp_path, monkeypatch):
    executable = tmp_path / "databricks"
    # A fabricated CLI stand-in exercises the actual bounded subprocess path.
    executable.write_text(
        "#!/bin/sh\nprintf '%s' '" + json.dumps({"details": {"host": host()}}) + "'\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert await CliProfile("work").host() == host()
    executable.write_text("#!/bin/sh\nprintf '%s' 'private failure' >&2\nexit 1\n")
    with pytest.raises(RuntimeError, match="CLI request failed") as error:
        await CliProfile("work").host()
    assert "private failure" not in str(error.value)
    executable.write_text("#!/bin/sh\nprintf '%s' 'private invalid json'\n")
    with pytest.raises(RuntimeError, match="Invalid Databricks CLI JSON") as error:
        await CliProfile("work").host()
    assert "private invalid" not in str(error.value)


async def test_gateway_wire_capability_omits_optional_field_and_preserves_tags(
    model_request, limits, tool_spec
):
    import httpx2 as httpx
    from test_compatible_adapter import wire_response

    from tether.adapters.openai_compat import OpenAICompatModel
    from tether.models import complete

    seen = []

    async def handle(request):
        body = json.loads(request.content)
        assert "parallel_tool_calls" not in body
        assert "tools" in body
        tags = json.loads(request.headers["Databricks-Ai-Gateway-Request-Tags"])
        assert tags["session_id"] == model_request.tags.session_id
        assert tags["attempt_id"]
        assert all(isinstance(value, str) for value in tags.values())
        assert "X-Tether-Request-Tags" not in request.headers
        seen.append(body)
        return httpx.Response(200, json=wire_response())

    adapter = OpenAICompatModel(
        base_url=host() + "/ai-gateway/mlflow/v1",
        tiers={"demo": "system.ai.alpha"},
        limits=limits,
        output_limit_field="max_tokens",
        supports_parallel_tool_calls=False,
        request_tags_header="Databricks-Ai-Gateway-Request-Tags",
        transport=httpx.MockTransport(handle),
    )
    try:
        result = await complete(adapter, model_request.model_copy(update={"tools": (tool_spec,)}))
        assert result.content == "Fabricated answer."
        assert len(seen) == 1
    finally:
        await adapter.aclose()


@pytest.mark.parametrize("partial", [None, 0, 1111, -1, True, "unknown"])
def test_partial_stream_usage_requires_valid_counts_and_complete_terminal(partial, limits):
    from tether.adapters.pydantic_ai.wire import StreamingWireValidator
    from tether.types import ModelUnavailable, TokenUsage

    def frame(content, finish, usage):
        return (
            b"data: "
            + json.dumps(
                {
                    "id": "fabricated-stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "system.ai.alpha",
                    "usage": usage,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": content},
                            "finish_reason": finish,
                        }
                    ],
                }
            ).encode()
            + b"\n\n"
        )

    first = frame("READY", None, {"prompt_tokens": partial, "completion_tokens": None})
    validator = StreamingWireValidator((), limits)
    if partial is not None and (type(partial) is not int or partial < 0):
        with pytest.raises(ModelUnavailable, match="invalid_response"):
            validator.feed(first)
        return
    validator.feed(first)
    assert validator.result is None
    validator.feed(frame("", "stop", {"prompt_tokens": 1111, "completion_tokens": 5}))
    validator.feed(b"data: [DONE]\n\n")
    assert validator.finish().usage == TokenUsage(input_tokens=1111, output_tokens=5)
    incomplete = StreamingWireValidator((), limits)
    incomplete.feed(first)
    incomplete.feed(frame("", "stop", {"prompt_tokens": 1111, "completion_tokens": None}))
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        incomplete.feed(b"data: [DONE]\n\n")


async def test_cli_cancellation_reaps_actual_process(tmp_path, monkeypatch):
    import asyncio
    import os

    executable, marker = tmp_path / "databricks", tmp_path / "child.pid"
    executable.write_text(
        '#!/bin/sh\nprintf "%s" "$$" > "' + str(marker) + '"\nexec /bin/sleep 20\n'
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    task = asyncio.create_task(CliProfile("work").host())
    try:
        for _ in range(300):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists()
        pid = int(marker.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_gateway_estimate_matches_omitted_parameter_and_changes_revision(
    model_request, limits, tool_spec
):
    from test_wire_estimator import expected_body, profile, vocabulary

    from tether.adapters.openai_compat import OpenAICompatModel

    adapter = OpenAICompatModel(
        base_url=host(),
        tiers={"demo": "fabricated-native"},
        limits=limits,
        output_limit_field="max_tokens",
        supports_parallel_tool_calls=False,
    )
    default = OpenAICompatModel(
        base_url=host(),
        tiers={"demo": "fabricated-native"},
        limits=limits,
        output_limit_field="max_tokens",
    )
    request = model_request.model_copy(update={"tools": (tool_spec,)})
    try:
        estimator = adapter.estimator(profile=profile(), vocabulary=vocabulary())
        estimate = await estimator.estimate(request)
        body = expected_body(request)
        body.pop("parallel_tool_calls")
        expected = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
        expected += 3 + 4 * len(body["messages"]) + 5 * len(body["tools"])
        assert estimate.tokens == expected
        other = await default.estimator(profile=profile(), vocabulary=vocabulary()).estimate(
            request
        )
        assert estimate.revision != other.revision
    finally:
        await adapter.aclose()
        await default.aclose()
