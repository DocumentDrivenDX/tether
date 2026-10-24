"""Versioned derivative SDK codecs; expected bindings come from the caller's journal."""

from importlib.metadata import version

from pydantic import Field, JsonValue, ValidationError
from pydantic_ai import Conversation, ConversationTypeAdapter

from tether.models.canonical import canonical, digest
from tether.types import ModelUnavailable, Payload

from .messages import CODEC, from_sdk_messages

SDK_VERSION = version("pydantic-ai-slim")
PYDANTIC_VERSION = version("pydantic")


class Checkpoint(Payload):
    codec: str
    sdk_version: str
    pydantic_version: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    conversation: JsonValue


def encode_conversation(conversation: Conversation, *, source_sha256: str) -> bytes:
    from_sdk_messages(list(conversation.messages))
    value = ConversationTypeAdapter.dump_python(conversation, mode="json")
    envelope = Checkpoint(
        codec=CODEC,
        sdk_version=SDK_VERSION,
        pydantic_version=PYDANTIC_VERSION,
        source_sha256=source_sha256,
        conversation=value,
    )
    return canonical(envelope.model_dump(mode="json"))


def decode_conversation(data: bytes, *, expected_sha256: str, source_sha256: str) -> Conversation:
    try:
        envelope = Checkpoint.model_validate_json(data)
        if digest(envelope.model_dump(mode="json")) != expected_sha256:
            raise ModelUnavailable("checkpoint_integrity_error")
        if (
            envelope.codec != CODEC
            or envelope.sdk_version != SDK_VERSION
            or envelope.pydantic_version != PYDANTIC_VERSION
        ):
            raise ModelUnavailable("unsupported_checkpoint_codec")
        if envelope.source_sha256 != source_sha256:
            raise ModelUnavailable("checkpoint_source_changed")
        conversation = ConversationTypeAdapter.validate_python(envelope.conversation)
        from_sdk_messages(list(conversation.messages))
        return conversation
    except (ValueError, ValidationError):
        raise ModelUnavailable("invalid_checkpoint") from None
