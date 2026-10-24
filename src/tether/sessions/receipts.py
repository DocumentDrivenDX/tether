"""Validate one content-bearing store result before metadata-only observation."""

from typing import TypeVar

from tether.types.canonical import canonical
from tether.types.client import ReadLimit
from tether.types.mutations import CreateReceipt, UpdateReceipt
from tether.types.storage import StoreError

R = TypeVar("R", CreateReceipt, UpdateReceipt)


def validated(
    raw: R,
    model: type[R],
    owner: str,
    max_bytes: int,
    *,
    session_id: str | None = None,
    ref: str | None = None,
) -> R:
    try:
        data = raw.model_dump(mode="python", warnings=False)
        size = len(canonical(raw.model_dump(mode="json", warnings=False)))
    except Exception:
        raise StoreError("mutation_receipt_invalid") from None
    if size > max_bytes:
        raise ReadLimit("mutation_receipt_bytes")
    try:
        receipt = model.model_validate(data).model_copy(deep=True)
        summary = receipt.summary
        if summary.owner != owner or (session_id is not None and summary.id != session_id):
            raise ValueError("scope")
        if isinstance(receipt, CreateReceipt) and (
            summary.ref != ref or (ref is None and receipt.disposition == "existing")
        ):
            raise ValueError("reference")
        return receipt
    except Exception:
        raise StoreError("mutation_receipt_invalid") from None
