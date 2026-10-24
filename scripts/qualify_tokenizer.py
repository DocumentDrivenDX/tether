"""Offline native-JSON qualification with explicit vocabulary and profile file inputs."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx2 as httpx
import regex

from tether.adapters.openai_compat import OpenAICompatModel, TokenizerProfile
from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.types import (
    AttachmentRef,
    Message,
    ModelExecutionLimits,
    ModelRequest,
    RequestTags,
    ToolSpec,
)


def merge_count(piece: bytes, ranks: dict[bytes, int]) -> int:
    if piece and piece in ranks:
        return 1
    pieces = [bytes([value]) for value in piece]
    while len(pieces) > 1:
        candidates = [
            (ranks.get(pieces[index] + pieces[index + 1], float("inf")), index)
            for index in range(len(pieces) - 1)
        ]
        rank, index = min(candidates)
        if rank == float("inf"):
            break
        pieces[index : index + 2] = [pieces[index] + pieces[index + 1]]
    return len(pieces)


def independent_tokens(text: str, ranks: dict[bytes, int], pattern: str) -> int:
    count, position = 0, 0
    for match in regex.finditer(pattern, text):
        if match.start() != position:
            raise ValueError("oracle_pattern_coverage")
        position = match.end()
        count += merge_count(match.group().encode("utf-8"), ranks)
    if position != len(text):
        raise ValueError("oracle_pattern_coverage")
    return count


def fabricated_request(kind: str) -> ModelRequest:
    tags = RequestTags(
        session_id="fabricated",
        turn_id=kind,
        model_call_id=kind,
        principal="fabricated",
        spec_id="fixture",
        spec_version="1",
        purpose="summary" if kind == "summary" else "answer",
    )
    ref = AttachmentRef(
        id="fabricated",
        filename='é "sample".txt',
        media_type="text/plain",
        size_bytes=32,
        read=False,
        withheld=True,
    )
    message = Message(
        role="user",
        content="Fabricated goals, constraints, progress and unresolved items. é 😀 中文\n"
        if kind == "unicode"
        else "Fabricated request.",
        attachments=(ref, ref) if kind == "attachments" else (),
    )
    tools = (
        (
            ToolSpec(
                name="lookup",
                description="Fabricated lookup.",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            ),
        )
        if kind == "tools"
        else ()
    )
    return ModelRequest(
        model="demo",
        tags=tags,
        system="Fabricated instructions.",
        messages=(message,),
        tools=tools,
        max_output_tokens=64,
        identity_mode="anonymous",
        attempts=ListAttemptRecorder(tags),
    )


async def qualify_field(
    field: str, profile: TokenizerProfile, data: bytes, ranks: dict[bytes, int]
) -> list[dict]:
    seen, projected = [], []

    def handler(request):
        seen.append(bytes(request.content))
        return httpx.Response(
            200,
            json={
                "id": "fabricated",
                "object": "chat.completion",
                "created": 0,
                "model": "fabricated",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Fabricated answer"},
                    }
                ],
            },
        )

    limits = ModelExecutionLimits(
        max_response_bytes=8192,
        max_tool_argument_bytes=512,
        max_attempts=1,
        request_timeout_seconds=5,
        max_retry_delay_seconds=0.01,
    )
    model = OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "fabricated-native"},
        limits=limits,
        output_limit_field=field,
        transport=httpx.MockTransport(handler),
    )
    counted = model.estimator(profile=profile, vocabulary=data)
    original = counted._count

    def count(body):
        projected.append(body)
        return original(body)

    counted._count = count
    cases = []
    try:
        for kind in ("plain", "unicode", "tools", "attachments", "summary"):
            request = fabricated_request(kind)
            estimate = await counted.estimate(request)
            await complete(model, request)
            if projected[-1] != seen[-1]:
                raise ValueError("native_projection_changed")
            body = json.loads(seen[-1])
            expected = await asyncio.to_thread(
                independent_tokens, seen[-1].decode(), ranks, profile.pattern
            )
            expected += profile.request_framing_tokens + profile.message_framing_tokens * len(
                body["messages"]
            )
            expected += profile.tool_framing_tokens * len(body.get("tools", []))
            if estimate.tokens != expected or estimate.quality != "approximate":
                raise ValueError("independent_token_oracle")
            cases.append(
                dict(
                    output_limit_field=field,
                    kind=kind,
                    tokens=expected,
                    wire_bytes=len(seen[-1]),
                    revision=estimate.revision,
                )
            )
    finally:
        await model.aclose()
    return cases


async def qualify(data: bytes, profile: TokenizerProfile) -> dict:
    if hashlib.sha256(data).hexdigest() != profile.vocabulary_sha256:
        raise ValueError("vocabulary_hash")
    ranks = {base64.b64decode(line.split()[0]): int(line.split()[1]) for line in data.splitlines()}
    cases = []
    for field in ("max_tokens", "max_completion_tokens"):
        cases.extend(await qualify_field(field, profile, data, ranks))
    return dict(
        vocabulary_sha256=profile.vocabulary_sha256,
        vocabulary_bytes=len(data),
        mergeable_tokens=len(ranks),
        profile=profile.model_dump(mode="json"),
        independent_oracle=(
            "Python adjacent-pair BPE over full native UTF-8 JSON plus explicit framing"
        ),
        transport="private capture and mock native transport; no real model request",
        quality="approximate",
        cases=cases,
    )


def read_bounded(path: Path, maximum: int) -> bytes:
    if type(maximum) is not int or maximum < 0:
        raise ValueError("qualification_input_bytes")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("qualification_input_bytes")
    return data


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vocabulary", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    profile = TokenizerProfile.model_validate_json(
        await asyncio.to_thread(read_bounded, args.profile, 1024 * 1024)
    )
    data = await asyncio.to_thread(read_bounded, args.vocabulary, profile.max_vocabulary_bytes)
    result = await qualify(data, profile)
    await asyncio.to_thread(
        args.output.write_text, json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                qualified_cases=len(result["cases"]), vocabulary_sha256=result["vocabulary_sha256"]
            )
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
