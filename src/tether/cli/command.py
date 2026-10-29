"""Local, fabricated reference command line over the public Client API."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from tether.client import Client
from tether.models.testing import ReplayModel
from tether.types.client import ReplyReset, ReplyText
from tether.types.execution import Answer, TurnResult

from .demo import open_demo
from .local import options, resolve_profile


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="tether")
    command.add_argument("--root", type=Path, default=Path.home() / ".local" / "share" / "tether")
    command.add_argument(
        "--model-config", type=Path, help="Opt into an explicit compatible model profile"
    )
    command.add_argument(
        "--volatile", action="store_true", help="Use a temporary in-memory UI demo"
    )
    local_arguments(command)
    actions = command.add_subparsers(dest="action")
    actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("session_id")
    send = actions.add_parser("send")
    send.add_argument("session_id")
    send.add_argument("text")
    follow = actions.add_parser("follow")
    follow.add_argument("session_id")
    follow.add_argument("--since", type=int)
    confirm = actions.add_parser("confirm")
    confirm.add_argument("session_id")
    confirm.add_argument("invocation_id")
    confirm.add_argument("answer", choices=("yes", "no"))
    resume = actions.add_parser("resume")
    resume.add_argument("session_id")
    replay = actions.add_parser("replay")
    replay.add_argument("cassette", type=Path)
    replay.add_argument("session_id")
    replay.add_argument("text")
    return command


def local_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--openai-api-baseurl", "--base-url", "--gateway", help="OpenAI-compatible API base URL"
    )
    command.add_argument(
        "--databricks-profile", help="Use a Databricks CLI profile; derive its gateway URL"
    )
    command.add_argument(
        "--model", help="Model id (otherwise discover /models, preferring a -chat id)"
    )
    command.add_argument(
        "--workspace", type=Path, help="Tool workspace (default: current directory)"
    )
    command.add_argument("--context-window", type=int, help="Override the local context budget")
    command.add_argument("--output-tokens", type=int, help="Output budget (default: 1024)")
    command.add_argument(
        "--authorization-file", type=Path, help="Optional file with full Authorization value"
    )


def display_result(result: TurnResult) -> None:
    for message in result.messages:
        if message.role == "assistant" and message.content:
            print(message.content)
    print(f"stop: {result.stop_reason}")
    for pending in result.pending:
        print(f"approval: {pending.tool} {pending.invocation_id}")


async def list_sessions(client: Client) -> None:
    cursor = None
    while True:
        page = await client.list(cursor=cursor)
        for item in page.items:
            print(f"{item.id}\t{item.status}\t{item.title or ''}")
        if page.next is None:
            return
        cursor = page.next


async def show_session(client: Client, session_id: str) -> None:
    view = await client.get(session_id)
    print(view.model_dump_json(indent=2))
    start = 0
    while True:
        page = await client.history(session_id, start=start)
        for slot in page.items:
            print(slot.model_dump_json())
        if page.next_start is None:
            return
        start = page.next_start


async def follow_session(client: Client, session_id: str, since: int | None) -> None:
    async for change in client.follow(session_id=session_id, since=since):
        if isinstance(change, ReplyText):
            print(change.text, end="", flush=True)
        elif isinstance(change, ReplyReset):
            print("\n[reply reset]")
        else:
            print(change.model_dump_json())


async def execute(args: argparse.Namespace) -> None:
    if args.volatile:
        raise ValueError("volatile_ui_only")
    profile = await resolve_profile(args.model_config, options(args))
    async with open_demo(args.root, continuation=args.action == "confirm", profile=profile) as (
        app,
        actor,
        _tool,
    ):
        client = app.client(actor)
        if args.action == "list":
            await list_sessions(client)
        elif args.action == "show":
            await show_session(client, args.session_id)
        elif args.action == "follow":
            await follow_session(client, args.session_id, args.since)
        elif args.action == "send":
            display_result(await (await client.send(args.session_id, args.text)).result())
        elif args.action == "confirm":
            handle = await client.confirm(
                args.session_id, args.invocation_id, Answer(approved=args.answer == "yes")
            )
            if handle is None:
                print("approval already settled")
            else:
                display_result(await handle.result())
        elif args.action == "resume":
            display_result(await (await client.resume(args.session_id)).result())
        elif args.action == "replay":
            cassette = await asyncio.to_thread(args.cassette.read_text, encoding="utf-8")
            app.engine.model = ReplayModel(cassette)
            display_result(await (await client.send(args.session_id, args.text)).result())


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action is None:
            try:
                from tether.tui import run
            except ImportError:
                raise RuntimeError(
                    "Install novirhiza-tether[tui,openai] to run the local-model TUI"
                ) from None
            run(
                args.root,
                volatile=args.volatile,
                model_config=args.model_config,
                local_options=options(args),
            )
        else:
            asyncio.run(execute(args))
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": type(error).__name__, "reason": str(error)}))
        return 1
    return 0
