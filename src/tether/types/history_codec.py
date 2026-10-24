"""Shared immutable exchange/source verification, independent of storage transport."""

from tether.types import Message, ModelResponse
from tether.types.canonical import digest
from tether.types.history import ExchangeBinding, HistoryRecord
from tether.types.storage import IntegrityError


def verify_exchange(binding: ExchangeBinding, record: HistoryRecord) -> None:
    if (
        record.kind != "model_exchange"
        or digest(record.model_dump(mode="json")) != binding.exchange_sha256
        or digest(record.request) != binding.request_digest
        or (digest(record.response) if record.response is not None else None)
        != binding.response_digest
    ):
        raise IntegrityError("exchange_integrity")


def exchange_source(binding: ExchangeBinding, record: HistoryRecord) -> HistoryRecord | None:
    if binding.response_kind == "none":
        if binding.source_sha256 is not None:
            raise IntegrityError("source_integrity")
        return None
    try:
        response = ModelResponse.model_validate(record.response)
    except ValueError:
        raise IntegrityError("source_integrity") from None
    if binding.response_kind == "summary":
        if response.tool_calls:
            raise IntegrityError("summary_has_tools")
        source = HistoryRecord(kind="summary", summary=response.content)
    else:
        source = HistoryRecord(
            kind="message",
            message=Message(
                role="assistant", content=response.content, tool_calls=response.tool_calls
            ),
        )
    if digest(source.model_dump(mode="json")) != binding.source_sha256:
        raise IntegrityError("source_integrity")
    return source
