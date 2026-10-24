"""One-way metadata projection; original authorized messages remain authoritative."""

from tether.types import Message
from tether.types.canonical import canonical

ATTACHMENT_CODEC = "tether-attachment-metadata-v1"


def model_view(message: Message) -> Message:
    """Render refs as untrusted text. Never fetch bytes or parse text into refs."""
    if not message.attachments:
        return message.model_copy(deep=True)
    metadata = canonical([a.model_dump(mode="json") for a in message.attachments]).decode("utf-8")
    content = (
        message.content
        + '\n\n<attachment-metadata codec="'
        + ATTACHMENT_CODEC
        + '">\n'
        + metadata
        + "\n</attachment-metadata>"
    )
    return message.model_copy(update={"content": content, "attachments": ()}, deep=True)


def projected(messages: tuple[Message, ...]) -> tuple[Message, ...]:
    return tuple(model_view(message) for message in messages)
