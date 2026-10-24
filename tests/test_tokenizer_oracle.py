"""The independent oracle obeys byte merges and refuses regex coverage holes."""

import pytest
from qualify_tokenizer import independent_tokens, merge_count


@pytest.mark.parametrize(
    ("text", "merges", "expected"),
    [
        ("abc", {b"abc": 256}, 1),
        ("aaa", {b"aa": 256}, 2),
        ("aaaa", {b"aa": 256}, 2),
        ("abc", {b"ab": 257, b"bc": 256}, 2),
        ("abc", {b"ab": 256, b"abc": 257}, 1),
    ],
)
def test_independent_oracle_uses_rank_priority(text, merges, expected):
    ranks = {bytes([value]): value for value in range(256)} | merges
    assert merge_count(text.encode(), ranks) == expected
    assert independent_tokens(text, ranks, r"(?s).+") == expected


@pytest.mark.parametrize("pattern", [r"[a-z]+", r"[0-9]+"])
def test_independent_oracle_refuses_uncovered_text(pattern):
    with pytest.raises(ValueError, match="oracle_pattern_coverage"):
        independent_tokens("abc 12", {}, pattern)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_qualification_script_is_offline_and_checks_exact_native_bytes(monkeypatch):
    import socket

    import tiktoken
    from qualify_tokenizer import qualify
    from test_wire_estimator import profile, vocabulary

    def refused(*args, **kwargs):
        raise AssertionError("implicit_network_or_registry")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(tiktoken, "get_encoding", refused)
    result = await qualify(vocabulary(), profile())
    assert len(result["cases"]) == 10
    assert result["quality"] == "approximate"


def test_qualification_input_reads_have_a_finite_byte_bound(tmp_path):
    from qualify_tokenizer import read_bounded

    path = tmp_path / "fabricated"
    path.write_bytes(b"abc")
    assert read_bounded(path, 3) == b"abc"
    with pytest.raises(ValueError, match="qualification_input_bytes"):
        read_bounded(path, 2)
