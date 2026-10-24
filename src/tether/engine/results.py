"""Immutable content addresses stay distinct from the single journal result key."""

from tether.types import Message
from tether.types.canonical import digest


def result_content_key(invocation_id: str, message: Message) -> str:
    return "result:" + invocation_id + ":" + digest(message.model_dump(mode="json"))
