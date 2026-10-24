"""Explicit offline vocabulary; no registry, cache, environment or download lookup."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Annotated

from pydantic import Field, model_validator
from tiktoken import Encoding

from tether.types import Count, Payload, PositiveInt


class TokenizerProfile(Payload):
    id: Annotated[str, Field(strict=True, min_length=1)]
    revision: Annotated[str, Field(strict=True, min_length=1)]
    encoding_name: Annotated[str, Field(strict=True, min_length=1)]
    pattern: Annotated[str, Field(strict=True, min_length=1, max_length=65536)]
    vocabulary_sha256: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
    max_vocabulary_bytes: PositiveInt
    max_request_bytes: PositiveInt
    max_count_workers: PositiveInt
    max_count_operations: PositiveInt
    request_framing_tokens: Count
    message_framing_tokens: Count
    tool_framing_tokens: Count

    @model_validator(mode="after")
    def capacity(self) -> TokenizerProfile:
        if self.max_count_workers > self.max_count_operations:
            raise ValueError("invalid_tokenizer_capacity")
        return self


def vocabulary_ranks(data: bytes, profile: TokenizerProfile) -> dict[bytes, int]:
    if (
        type(data) is not bytes
        or not data
        or len(data) > profile.max_vocabulary_bytes
        or hashlib.sha256(data).hexdigest() != profile.vocabulary_sha256
    ):
        raise ValueError("invalid_tokenizer_vocabulary")
    ranks: dict[bytes, int] = {}
    used: set[int] = set()
    try:
        for line in data.splitlines():
            token, raw = line.split()
            rank = int(raw)
            value = base64.b64decode(token, validate=True)
            if not value or value in ranks or not 0 <= rank <= 2**32 - 1 or rank in used:
                raise ValueError("invalid_tokenizer_vocabulary")
            ranks[value] = rank
            used.add(rank)
        if not all(bytes([value]) in ranks for value in range(256)):
            raise ValueError("invalid_tokenizer_vocabulary")
    except (ValueError, binascii.Error):
        raise ValueError("invalid_tokenizer_vocabulary") from None
    return ranks


def local_encoding(data: bytes, profile: TokenizerProfile) -> Encoding:
    ranks = vocabulary_ranks(data, profile)
    try:
        return Encoding(
            profile.encoding_name,
            pat_str=profile.pattern,
            mergeable_ranks=ranks,
            special_tokens={},
        )
    except Exception:
        raise ValueError("invalid_tokenizer_profile") from None
