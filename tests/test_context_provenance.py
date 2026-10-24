"""Ordered provenance is checked against an independent exhaustive mapping oracle."""

from itertools import combinations, product

import pytest

from tether.context.provenance import align_sources
from tether.types import AttachmentRef, Message, ToolCall
from tether.types.context import ContextError, ContextSource


def sources(roles):
    return tuple(
        ContextSource(index=10 * i, message=Message(role=role, content=f"Fabricated {i}"))
        for i, role in enumerate(roles)
    )


def test_unique_fresh_mapping_and_identity_preserve_indices_without_text_guessing():
    original = sources(("user", "assistant", "user"))
    supplied = tuple(s.message.model_copy(deep=True) for s in original)
    redacted = Message(role="assistant", content="Redacted")
    projection = align_sources(original, supplied, (redacted, supplied[2]))
    assert tuple(s.index for s in projection.sources) == (10, 20)
    assert projection.filtered_indices == (0,)
    assert projection.sources[0].message.content == "Redacted"
    assert original[1].message.content == "Fabricated 1"


def test_equal_text_never_resolves_ambiguous_source_identity():
    original = sources(("user", "user"))
    supplied = tuple(s.message for s in original)
    with pytest.raises(ContextError, match="context_invalid"):
        align_sources(original, supplied, (Message(role="user", content="Fabricated 0"),))
    assert align_sources(original, supplied, (supplied[1],)).sources[0].index == 10


def test_all_small_fresh_mappings_match_exhaustive_ordered_oracle():
    for count in range(5):
        for roles in product(("user", "assistant"), repeat=count):
            original = sources(roles)
            supplied = tuple(s.message for s in original)
            for out_count in range(count + 1):
                for output in product(("user", "assistant"), repeat=out_count):
                    possible = [
                        p
                        for p in combinations(range(count), out_count)
                        if tuple(roles[i] for i in p) == output
                    ]
                    narrowed = tuple(Message(role=r, content="Redacted") for r in output)
                    if len(possible) == 1:
                        projection = align_sources(original, supplied, narrowed)
                        assert tuple(s.index for s in projection.sources) == tuple(
                            10 * i for i in possible[0]
                        )
                    else:
                        with pytest.raises(ContextError, match="context_invalid"):
                            align_sources(original, supplied, narrowed)


@pytest.mark.parametrize(
    "change",
    [
        {"role": "user"},
        {"tool_call_id": "forged"},
        {"is_error": True},
        {"tool_calls": (ToolCall(id="other", name="fixture", arguments={"value": 1}),)},
        {"tool_calls": (ToolCall(id="call", name="fixture", arguments={"value": True}),)},
        {
            "attachments": (
                AttachmentRef(
                    id="new", filename="new.txt", media_type="text/plain", size_bytes=1, read=True
                ),
            )
        },
    ],
)
def test_returned_identity_does_not_authorize_structural_mutation(change):
    original = (
        ContextSource(
            index=0,
            message=Message(
                role="assistant",
                content="Fabricated",
                tool_calls=(ToolCall(id="call", name="fixture", arguments={"value": 1}),),
            ),
        ),
    )
    supplied = (original[0].message.model_copy(deep=True),)
    for name, value in change.items():
        object.__setattr__(supplied[0], name, value)
    with pytest.raises(ContextError, match="context_invalid"):
        align_sources(original, supplied, supplied)


def test_attachment_narrowing_and_content_redaction_are_allowed_and_copied():
    attachment = AttachmentRef(
        id="fixture", filename="fixture.txt", media_type="text/plain", size_bytes=1, read=True
    )
    original = (
        ContextSource(
            index=7,
            message=Message(
                role="assistant",
                content="Fabricated",
                attachments=(attachment,),
                tool_calls=(ToolCall(id="call", name="fixture", arguments={"nested": [1]}),),
            ),
        ),
    )
    supplied = (original[0].message.model_copy(deep=True),)
    narrowed = supplied[0].model_copy(update={"content": "Redacted", "attachments": ()})
    projection = align_sources(original, supplied, (narrowed,))
    nested = projection.sources[0].message.tool_calls[0].arguments["nested"]
    assert isinstance(nested, list)
    nested.append(2)
    assert original[0].message.tool_calls[0].arguments == {"nested": [1]}
    assert supplied[0].tool_calls[0].arguments == {"nested": [1]}


@pytest.mark.parametrize("case", ["extra", "duplicate", "order", "shape", "sources"])
def test_malformed_provider_output_and_source_catalog_refuse(case):
    original = sources(("user", "assistant"))
    supplied = tuple(s.message.model_copy(deep=True) for s in original)
    output = supplied
    if case == "extra":
        object.__setattr__(supplied[0], "unexpected", "Fabricated")
    elif case == "duplicate":
        output = (supplied[0], supplied[0])
    elif case == "order":
        output = tuple(reversed(supplied))
    elif case == "shape":
        output = list(supplied)
    else:
        original = tuple(reversed(original))
    with pytest.raises(ContextError, match="context_invalid"):
        align_sources(original, supplied, output)


@pytest.mark.parametrize("reverse", [False, True])
def test_attachment_projection_cannot_duplicate_or_reorder_representation(reverse):
    first = AttachmentRef(
        id="first", filename="first.txt", media_type="text/plain", size_bytes=1, read=True
    )
    second = first.model_copy(update={"id": "second", "filename": "second.txt"})
    original = (
        ContextSource(
            index=0, message=Message(role="user", content="Fabricated", attachments=(first, second))
        ),
    )
    supplied = (original[0].message.model_copy(deep=True),)
    changed = supplied[0].model_copy(
        update={"attachments": (second, first) if reverse else (first, first)}
    )
    with pytest.raises(ContextError, match="context_invalid"):
        align_sources(original, supplied, (changed,))
