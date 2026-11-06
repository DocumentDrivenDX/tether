"""Reference command dispatch, paging and exit-code behavior over a fabricated client."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from tether.cli import command
from tether.cli.command import dispatch, main, parser
from tether.client import ReplyReset, ReplyText


class _Dump(BaseModel):
    value: str


def _result(text: str = "answer", pending: tuple[Any, ...] = ()) -> SimpleNamespace:
    messages = [
        SimpleNamespace(role="user", content="question"),
        SimpleNamespace(role="assistant", content=text),
        SimpleNamespace(role="assistant", content=""),
    ]
    return SimpleNamespace(messages=messages, stop_reason="final", pending=list(pending))


class _Handle:
    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result

    async def result(self) -> SimpleNamespace:
        return self._result


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.settled = False

    async def list(self, *, cursor: str | None = None) -> SimpleNamespace:
        self.calls.append(("list", (cursor,)))
        if cursor is None:
            item = SimpleNamespace(id="s1", status="idle", title="First")
            return SimpleNamespace(items=[item], next="c2")
        item = SimpleNamespace(id="s2", status="running", title=None)
        return SimpleNamespace(items=[item], next=None)

    async def get(self, session_id: str) -> _Dump:
        return _Dump(value=session_id)

    async def history(self, session_id: str, *, start: int = 0) -> SimpleNamespace:
        self.calls.append(("history", (session_id, start)))
        if start == 0:
            return SimpleNamespace(items=[_Dump(value="slot0")], next_start=1)
        return SimpleNamespace(items=[_Dump(value="slot1")], next_start=None)

    async def follow(self, *, session_id: str, since: int | None) -> Any:
        self.calls.append(("follow", (session_id, since)))
        for event in (
            ReplyText(session_id=session_id, turn_id="t", attempt_id="a", offset=0, text="par"),
            ReplyReset(session_id=session_id, turn_id="t", attempt_id="a", reason="outage"),
            _Dump(value="other"),
        ):
            yield event

    async def send(self, session_id: str, text: str) -> _Handle:
        self.calls.append(("send", (session_id, text)))
        pending = SimpleNamespace(tool="seedling", invocation_id="inv-1")
        return _Handle(_result(pending=(pending,)))

    async def confirm(self, session_id: str, invocation_id: str, answer: Any) -> _Handle | None:
        self.calls.append(("confirm", (session_id, invocation_id, answer.approved)))
        return None if self.settled else _Handle(_result("confirmed"))

    async def resume(self, session_id: str) -> _Handle:
        self.calls.append(("resume", (session_id,)))
        return _Handle(_result("resumed"))


def _run(client: _FakeClient, *argv: str) -> None:
    args = parser().parse_args(["--root", "unused", *argv])
    asyncio.run(dispatch(cast(Any, client), args))


def test_list_pages_through_cursors(capsys: pytest.CaptureFixture[str]) -> None:
    client = _FakeClient()
    _run(client, "list")
    assert capsys.readouterr().out.splitlines() == ["s1\tidle\tFirst", "s2\trunning\t"]
    assert [call[1] for call in client.calls] == [(None,), ("c2",)]


def test_show_prints_view_then_every_history_page(capsys: pytest.CaptureFixture[str]) -> None:
    client = _FakeClient()
    _run(client, "show", "s1")
    out = capsys.readouterr().out
    assert '"value": "s1"' in out
    assert out.index("slot0") < out.index("slot1")
    assert [call[1] for call in client.calls] == [("s1", 0), ("s1", 1)]


def test_follow_renders_text_reset_and_other_events(capsys: pytest.CaptureFixture[str]) -> None:
    client = _FakeClient()
    _run(client, "follow", "s1", "--since", "7")
    out = capsys.readouterr().out
    assert out.startswith("par\n[reply reset]\n")
    assert '"value":"other"' in out
    assert client.calls == [("follow", ("s1", 7))]


def test_send_and_resume_display_replies_stop_and_pending(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _FakeClient()
    _run(client, "send", "s1", "hello")
    sent = capsys.readouterr().out
    assert "answer\n" in sent
    assert "question" not in sent
    assert "stop: final" in sent
    assert "approval: seedling inv-1" in sent
    _run(client, "resume", "s1")
    assert "resumed" in capsys.readouterr().out
    assert [call[0] for call in client.calls] == ["send", "resume"]


def test_replay_uses_send(capsys: pytest.CaptureFixture[str]) -> None:
    client = _FakeClient()
    _run(client, "replay", "cassette.json", "s1", "hello")
    assert client.calls == [("send", ("s1", "hello"))]
    assert "stop: final" in capsys.readouterr().out


@pytest.mark.parametrize(("answer", "approved"), [("yes", True), ("no", False)])
def test_confirm_passes_decision_and_reports_settled(
    capsys: pytest.CaptureFixture[str], answer: str, approved: bool
) -> None:
    client = _FakeClient()
    _run(client, "confirm", "s1", "inv-1", answer)
    assert client.calls == [("confirm", ("s1", "inv-1", approved))]
    assert "confirmed" in capsys.readouterr().out
    client.settled = True
    _run(client, "confirm", "s1", "inv-1", answer)
    assert capsys.readouterr().out == "approval already settled\n"


def test_execute_refuses_volatile_mode() -> None:
    args = parser().parse_args(["--volatile", "list"])
    with pytest.raises(ValueError, match="volatile_ui_only"):
        asyncio.run(command.execute(args))


def _error(capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    return json.loads(capsys.readouterr().out)


def test_main_reports_model_options_without_connection_as_json_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--model", "m", "list"]) == 1
    error = _error(capsys)
    assert error["error"] == "ValueError"
    assert "Model options require" in error["reason"]


def test_main_routes_remote_options_and_refuses_local_model_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Any] = []

    async def fake_remote(args: Any, handler: Any) -> None:
        seen.append((args.endpoint, args.action, handler))

    monkeypatch.setattr("tether.cli.remote.run_remote", fake_remote)
    assert main(["--endpoint", "https://remote.invalid", "list"]) == 0
    assert seen == [("https://remote.invalid", "list", dispatch)]
    assert main(["--remote-service-caller", "list"]) == 0
    assert main(["--remote-profile", "fabricated", "list"]) == 0
    assert len(seen) == 3
    code = main(["--endpoint", "https://remote.invalid", "--databricks-profile", "p", "list"])
    assert code == 1
    assert _error(capsys)["reason"] == "remote_mode_refuses_local_model_options"


def test_main_without_action_starts_tui_with_parsed_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(root: Path, **kwargs: Any) -> None:
        seen.update(root=root, **kwargs)

    monkeypatch.setattr("tether.tui.run", fake_run)
    assert main(["--root", str(tmp_path), "--volatile"]) == 0
    assert seen == {
        "root": tmp_path,
        "volatile": True,
        "model_config": None,
        "local_options": None,
    }


def test_main_runs_action_and_maps_operational_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[str] = []

    async def fake_execute(args: Any) -> None:
        ran.append(args.action)

    monkeypatch.setattr(command, "execute", fake_execute)
    assert main(["list"]) == 0
    assert ran == ["list"]

    async def failing(_args: Any) -> None:
        raise OSError("fabricated disk failure")

    monkeypatch.setattr(command, "execute", failing)
    assert main(["list"]) == 1
    assert _error(capsys) == {"error": "OSError", "reason": "fabricated disk failure"}
