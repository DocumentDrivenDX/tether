"""Explicit offline vocabulary; no registry, cache, environment or download lookup."""

from __future__ import annotations

import base64
import binascii
import hashlib

from tiktoken import Encoding

from tether.types.tokenizer import TokenizerProfile as TokenizerProfile


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
