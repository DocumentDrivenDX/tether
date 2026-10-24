"""Independent native body/byte-token oracles, explicit offline estimation and refusals."""

import base64
import hashlib
import json
from contextlib import asynccontextmanager

import httpx2 as httpx
import pytest
from test_attachment_metadata import attachment
from test_compatible_adapter import wire_response

from tether.adapters.openai_compat import OpenAICompatModel, TokenizerProfile
from tether.adapters.pydantic_ai.estimator import OpenAIWireEstimator
from tether.adapters.pydantic_ai.tokenizer import local_encoding
from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.testing.conformance import TokenEstimatorFixture, token_estimator_suite
from tether.types import Message
from tether.types.context import ContextError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def vocabulary():
    return b"".join(
        base64.b64encode(bytes([value])) + b" " + str(value).encode() + b"\n"
        for value in range(256)
    )


def profile(data=None, **updates):
    data = vocabulary() if data is None else data
    return TokenizerProfile.model_validate(
        dict(
            id="fabricated-byte-bpe",
            revision="1",
            encoding_name="fabricated-byte-bpe",
            pattern=r"(?s).",
            vocabulary_sha256=hashlib.sha256(data).hexdigest(),
            max_vocabulary_bytes=10000,
            max_request_bytes=200000,
            max_count_workers=1,
            max_count_operations=2,
            request_framing_tokens=3,
            message_framing_tokens=4,
            tool_framing_tokens=5,
        )
        | updates
    )


def estimator(settings=None, *, field="max_tokens"):
    return OpenAIWireEstimator(
        tiers={"demo": "fabricated-native", "fixture": "fabricated-native"},
        output_limit_field=field,
        profile=settings or profile(),
        vocabulary=vocabulary(),
    )


def expected_body(request, field="max_tokens"):
    messages = [{"role": "system", "content": request.system}] if request.system else []
    for original in request.messages:
        text = original.content
        if original.attachments:
            refs = json.dumps(
                [a.model_dump(mode="json") for a in original.attachments],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            text += (
                '\n\n<attachment-metadata codec="tether-attachment-metadata-v1">\n'
                + refs
                + "\n</attachment-metadata>"
            )
        message: dict[str, object] = {"role": original.role, "content": text}
        if original.tool_calls:
            message["tool_calls"] = [
                dict(
                    id=c.id,
                    type="function",
                    function=dict(
                        name=c.name,
                        arguments=json.dumps(
                            c.arguments, separators=(",", ":"), ensure_ascii=False
                        ),
                    ),
                )
                for c in original.tool_calls
            ]
        if original.tool_call_id:
            message["tool_call_id"] = original.tool_call_id
        messages.append(message)
    body: dict[str, object] = dict(messages=messages, model="fabricated-native", stream=False)
    if request.max_output_tokens is not None:
        body[field] = request.max_output_tokens
    if request.tools:
        body.update(
            parallel_tool_calls=False,
            tool_choice="auto",
            tools=[
                dict(
                    type="function",
                    function=dict(
                        name=t.name,
                        description=t.description,
                        parameters=t.input_schema,
                    ),
                )
                for t in request.tools
            ],
        )
    return body | request.extra


def oracle(request, field="max_tokens"):
    body = expected_body(request, field)
    count = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
    return count + 3 + 4 * len(body["messages"]) + 5 * len(body.get("tools", []))


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("variant", ["plain", "tools", "unicode", "attachment", "extra", "summary"])
async def test_estimator_matches_independent_native_body_and_byte_oracle(
    model_request, limits, tool_spec, field, variant
):
    request = model_request
    if variant == "tools":
        request = request.model_copy(update={"tools": (tool_spec,)})
    elif variant == "unicode":
        request = request.model_copy(
            update={"messages": (Message(role="user", content="é 😀 \n<|endoftext|>"),)}
        )
    elif variant == "attachment":
        request = request.model_copy(
            update={"messages": (Message(role="user", content="", attachments=(attachment(),)),)}
        )
    elif variant == "extra":
        request = request.model_copy(update={"extra": {"temperature": 0.2}})
    elif variant == "summary":
        request = request.model_copy(
            update={"tags": request.tags.model_copy(update={"purpose": "summary"})}
        )
    seen = []

    def handler(native):
        seen.append(json.loads(native.content))
        return httpx.Response(200, json=wire_response())

    model = OpenAICompatModel(
        base_url="https://fabricated.invalid/v1",
        tiers={"demo": "fabricated-native"},
        limits=limits,
        output_limit_field=field,
        transport=httpx.MockTransport(handler),
    )
    try:
        estimate = await model.estimator(profile=profile(), vocabulary=vocabulary()).estimate(
            request
        )
        await complete(
            model, request.model_copy(update={"attempts": ListAttemptRecorder(request.tags)})
        )
        assert seen == [expected_body(request, field)]
        assert estimate.tokens == oracle(request, field)
        assert estimate.quality == "approximate"
        assert estimate.method == "native-json-bpe-v1"
        assert len(estimate.revision) == 64
    finally:
        await model.aclose()


async def test_public_estimator_conformance_uses_independent_wire_oracle():
    @asynccontextmanager
    async def factory():
        yield TokenEstimatorFixture(
            estimator(), lambda request: (oracle(request), oracle(request)), evidence="wire_oracle"
        )

    report = await token_estimator_suite(factory)
    assert report.capabilities["evidence"] == "wire_oracle"
    assert "estimate_attachments_bounds" in report.checks


async def test_estimation_never_calls_identity_recorder_or_physical_transport(
    model_request, monkeypatch
):
    import socket

    import tiktoken

    class Bomb:
        async def authorization(self):
            raise AssertionError("credential_read")

        async def reserve(self):
            raise AssertionError("physical_attempt")

        async def start(self, *args, **kwargs):
            raise AssertionError("physical_attempt")

        async def observe(self, *args, **kwargs):
            raise AssertionError("physical_attempt")

    def refused(*args, **kwargs):
        raise AssertionError("implicit_lookup_or_network")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(tiktoken, "get_encoding", refused)
    request = model_request.model_copy(
        update={"identity_mode": "caller", "credential": Bomb(), "attempts": Bomb()}
    )
    before = request.model_dump(mode="json")
    counted = estimator()
    first = await counted.estimate(request)
    other = request.model_copy(
        update={
            "traceparent": "untrusted-not-counted",
            "tags": request.tags.model_copy(update={"principal": "other"}),
        }
    )
    assert await counted.estimate(other) == first
    assert request.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "kind",
    [
        "hash",
        "oversize",
        "duplicate_token",
        "duplicate_rank",
        "missing_byte",
        "malformed",
        "negative_rank",
        "oversize_rank",
        "empty_token",
        "empty_data",
        "bad_regex",
    ],
)
async def test_invalid_local_vocabulary_or_profile_refuses(kind):
    valid = vocabulary()
    data = {
        "duplicate_token": valid + b"AA== 256\n",
        "duplicate_rank": valid + b"YWI= 0\n",
        "missing_byte": valid.split(b"\n", 1)[1],
        "malformed": valid + b"not-base64 bad-rank\n",
        "negative_rank": valid + b"YWI= -1\n",
        "oversize_rank": valid + b"YWI= 4294967296\n",
        "empty_token": valid + b" 256\n",
        "empty_data": b"",
    }.get(kind, valid)
    updates = {
        "hash": {"vocabulary_sha256": "0" * 64},
        "oversize": {"max_vocabulary_bytes": 1},
        "bad_regex": {"pattern": "("},
    }.get(kind, {})
    with pytest.raises(ValueError, match="invalid_tokenizer"):
        local_encoding(data, profile(data, **updates))


@pytest.mark.parametrize(
    "change", ["tier", "effort", "reserved", "input_bytes", "regex_omits_bytes"]
)
async def test_unmeasurable_requests_refuse_without_egress(model_request, change):
    settings = profile()
    request = model_request
    if change == "tier":
        request = request.model_copy(update={"model": "unknown"})
    elif change == "effort":
        request = request.model_copy(update={"effort": "high"})
    elif change == "reserved":
        request = request.model_copy(update={"extra": {"messages": []}})
    elif change == "input_bytes":
        settings = profile(max_request_bytes=1)
    else:
        settings = profile(pattern=r"[a-z]+")
    with pytest.raises(ContextError, match="context_unmeasurable"):
        await estimator(settings).estimate(request)
