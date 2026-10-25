"""Claimed identities and text consistency do not become disclosure/start authority."""

import asyncio

import pytest

from tether.models import checked_stream, complete
from tether.types import AttemptIdentity, ModelResponse, ModelUnavailable, TextDelta

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Port:
    provider = "fabricated"

    def __init__(self, values):
        self.values, self.closed = values, False

    async def stream(self, request):
        try:
            for value in self.values:
                if isinstance(value, BaseException):
                    raise value
                yield value
        finally:
            self.closed = True


def identity(request, **changes):
    tags = request.tags
    return AttemptIdentity(
        session_id=tags.session_id,
        turn_id=tags.turn_id,
        model_call_id=tags.model_call_id,
        purpose=tags.purpose,
        attempt_id="fabricated-claim",
    ).model_copy(update=changes)


@pytest.mark.parametrize(
    ("pieces", "text"),
    [
        ([], "unstreamed"),
        ([""], "unstreamed"),
        (["é", "😀", "中文"], "é😀中文"),
        (["a", "", "bc"], "abc"),
        (["a\n", '"b'], 'a\n"b'),
        ([""], ""),
    ],
)
async def test_exact_effective_text_or_no_effective_text_is_accepted(model_request, pieces, text):
    port = Port(
        [*(TextDelta(text=p) for p in pieces), ModelResponse(content=text, finish_reason="stop")]
    )
    assert (await complete(port, model_request)).content == text
    assert port.closed


@pytest.mark.parametrize("text", ["short", "toolong", "same-size-x"])
async def test_mismatched_terminal_is_never_forwarded(model_request, text):
    port = Port([TextDelta(text="same-size-y"), ModelResponse(content=text, finish_reason="stop")])
    seen = []
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await append_frames(port, model_request, seen)
    assert len(seen) == 1
    assert isinstance(seen[0], TextDelta)
    assert port.closed


@pytest.mark.parametrize(
    "field", ["session_id", "turn_id", "model_call_id", "purpose", "attempt_id"]
)
async def test_foreign_or_empty_claimed_identity_is_refused(model_request, field):
    changed = "summary" if field == "purpose" else "" if field == "attempt_id" else "foreign"
    claim = identity(model_request, **{field: changed})
    port = Port(
        [TextDelta(text="a", identity=claim), ModelResponse(content="a", finish_reason="stop")]
    )
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(port, model_request)
    assert port.closed


@pytest.mark.parametrize("change", ["attempt", "bind", "unbind"])
async def test_nonempty_frames_cannot_silently_switch_attempt_state(model_request, change):
    first = None if change == "bind" else identity(model_request)
    second = None if change == "unbind" else identity(model_request, attempt_id="other")
    port = Port(
        [
            TextDelta(text="a", identity=first),
            TextDelta(text="b", identity=second),
            ModelResponse(content="ab", finish_reason="stop"),
        ]
    )
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(port, model_request)
    assert port.closed


async def test_matching_claim_is_complete_only_without_proof_of_start(model_request):
    claim = identity(model_request)
    port = Port(
        [
            TextDelta(text="a", identity=claim),
            TextDelta(text="", identity=None),
            TextDelta(text="b", identity=claim),
            ModelResponse(content="ab", finish_reason="stop"),
        ]
    )
    assert (await complete(port, model_request)).content == "ab"
    assert not model_request.attempts.started


@pytest.mark.parametrize("poison", ["integer", "bytes", "bad_identity", "bad_terminal", "object"])
async def test_typed_instances_are_freshly_validated_without_diagnostic_content(
    model_request, poison
):
    marker = "Fabricated private diagnostic"
    values = {
        "integer": TextDelta(text="a").model_copy(update={"text": 3}),
        "bytes": TextDelta(text="a").model_copy(update={"text": b"a"}),
        "bad_identity": TextDelta(text="a").model_copy(update={"identity": {"attempt_id": marker}}),
        "bad_terminal": ModelResponse(finish_reason="stop").model_copy(
            update={"finish_reason": marker}
        ),
        "object": object(),
    }
    port = Port([values[poison]])
    with pytest.raises(ModelUnavailable, match="invalid_response") as caught:
        await complete(port, model_request)
    assert marker not in str(caught.value)
    assert port.closed


async def test_response_copy_cannot_be_rewritten_after_forwarding(model_request):
    from tether.types import ToolCall

    original = ModelResponse(
        content="a",
        finish_reason="tool_calls",
        tool_calls=(ToolCall(id="call", name="lookup", arguments={"n": 1}),),
    )

    class Mutating:
        provider = "fabricated"

        async def stream(self, request):
            yield TextDelta(text="a")
            yield original
            original.tool_calls[0].arguments["n"] = 999

    observed = await complete(Mutating(), model_request)
    assert observed.tool_calls[0].arguments == {"n": 1}
    assert original.tool_calls[0].arguments == {"n": 999}


@pytest.mark.parametrize("terminal", [False, True])
async def test_explicit_byte_cap_refuses_before_forwarding_oversized_text(model_request, terminal):
    value = (
        ModelResponse(content="é" * 100000, finish_reason="stop")
        if terminal
        else TextDelta(text="é" * 100000)
    )
    port = Port([value])
    seen = []
    with pytest.raises(ModelUnavailable, match="response_too_large"):
        await append_frames(port, model_request, seen, maximum=16)
    assert not seen
    assert port.closed


async def test_large_text_hashing_is_cancellation_responsive(model_request):
    port = Port(
        [TextDelta(text="a" * 1000000), ModelResponse(content="a" * 1000000, finish_reason="stop")]
    )
    entered = asyncio.Event()

    async def consume():
        entered.set()
        await complete(port, model_request)

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert port.closed


@pytest.mark.parametrize("component", ["delta", "identity", "response", "usage", "receipt", "call"])
async def test_undeclared_typed_state_is_not_silently_dropped(model_request, component):
    from tether.types import ResponseReceipt, TokenUsage, ToolCall

    extra = {"unexpected": "Fabricated marker"}
    values = {
        "delta": TextDelta(text="x").model_copy(update=extra),
        "identity": TextDelta(text="x", identity=identity(model_request).model_copy(update=extra)),
        "response": ModelResponse(finish_reason="stop").model_copy(update=extra),
        "usage": ModelResponse(
            finish_reason="stop",
            usage=TokenUsage(input_tokens=1, output_tokens=1).model_copy(update=extra),
        ),
        "receipt": ModelResponse(
            finish_reason="stop",
            receipt=ResponseReceipt(
                provider="fabricated",
                endpoint="offline",
                schema_profile="fixture",
                request_id_source="none",
            ).model_copy(update=extra),
        ),
        "call": ModelResponse(
            finish_reason="tool_calls",
            tool_calls=(ToolCall(id="c", name="lookup", arguments={}).model_copy(update=extra),),
        ),
    }
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(Port([values[component]]), model_request)


async def test_port_mutation_cannot_replace_original_scope_target(model_request):
    original_session = model_request.tags.session_id
    claim = identity(model_request, session_id="foreign")

    class Mutating:
        provider = "fabricated"

        async def stream(self, request):
            request.tags.__dict__["session_id"] = "foreign"
            yield TextDelta(text="x", identity=claim)
            yield ModelResponse(content="x", finish_reason="stop")

    try:
        with pytest.raises(ModelUnavailable, match="invalid_response"):
            await complete(Mutating(), model_request)
    finally:
        model_request.tags.__dict__["session_id"] = original_session


async def test_consumer_mutation_cannot_replace_earlier_identity_state(model_request):
    claim = identity(model_request)
    port = Port(
        [
            TextDelta(text="a", identity=claim),
            TextDelta(text="b", identity=identity(model_request, attempt_id="other")),
            ModelResponse(content="ab", finish_reason="stop"),
        ]
    )

    async def mutate():
        async for item in checked_stream(port, model_request):
            if isinstance(item, TextDelta):
                assert item.identity is not None
                object.__setattr__(item.identity, "attempt_id", "other")

    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await mutate()


async def append_frames(port, request, seen, maximum=None):
    async for item in checked_stream(port, request, max_text_bytes=maximum):
        seen.append(item)


async def test_cumulative_text_cap_refuses_second_frame_before_forwarding(model_request):
    port = Port(
        [
            TextDelta(text="12345678"),
            TextDelta(text="abcdefgh"),
            ModelResponse(content="12345678abcdefgh", finish_reason="stop"),
        ]
    )
    seen = []
    with pytest.raises(ModelUnavailable, match="response_too_large"):
        await append_frames(port, model_request, seen, maximum=12)
    assert [item.text for item in seen] == ["12345678"]
    assert port.closed


@pytest.mark.parametrize("maximum", [0, True, "16"])
async def test_invalid_text_limit_refuses_before_invoking_port(model_request, maximum):
    class Never:
        provider = "fabricated"

        def stream(self, request):
            raise AssertionError("port_invoked")

    with pytest.raises(ValueError, match="invalid_stream_limit"):
        await append_frames(Never(), model_request, [], maximum=maximum)


async def test_typed_json_model_is_not_repaired_into_tool_argument_dict(model_request):
    from tether.types import ToolCall

    call = ToolCall(id="call", name="lookup", arguments={}).model_copy(
        update={"arguments": {"nested": TextDelta(text="private")}}
    )
    response = ModelResponse(finish_reason="tool_calls", tool_calls=(call,))
    with pytest.raises(ModelUnavailable, match="invalid_response"):
        await complete(Port([response]), model_request)
