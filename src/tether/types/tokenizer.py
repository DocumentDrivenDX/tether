"""Provider-neutral configuration for an explicitly supplied tokenizer vocabulary."""

from typing import Annotated

from pydantic import Field, model_validator

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
    def capacity(self) -> "TokenizerProfile":
        if self.max_count_workers > self.max_count_operations:
            raise ValueError("invalid_tokenizer_capacity")
        return self
