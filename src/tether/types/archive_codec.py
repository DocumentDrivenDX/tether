"""Pure content-closure validation shared by live transfer and archive adapters."""

from collections.abc import Iterator

from tether.types import JsonValue
from tether.types.canonical import canonical, digest
from tether.types.execution import HistoryRef
from tether.types.history import StoredHistory
from tether.types.storage import ArchiveBundle, IntegrityError


def references(value: JsonValue) -> Iterator[HistoryRef]:
    if isinstance(value, dict):
        if {"provider", "key", "sha256", "codec", "owner", "session_id", "kind"} <= value.keys():
            yield HistoryRef.model_validate(value)
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def verify_bundle(bundle: ArchiveBundle) -> None:
    payload = bundle.model_dump(mode="json", exclude={"sha256"})
    if digest(payload) != bundle.sha256:
        raise IntegrityError("archive_integrity")
    records = {}
    for raw in bundle.records:
        stored = StoredHistory.model_validate(raw)
        if (
            stored.ref.owner != bundle.session.state.owner
            or stored.ref.session_id != bundle.session.state.id
            or stored.record.kind != stored.ref.kind
            or digest(stored.record.model_dump(mode="json")) != stored.ref.sha256
        ):
            raise IntegrityError("archive_reference_integrity")
        records[canonical(stored.ref.model_dump(mode="json"))] = stored
    for entry in bundle.session.entries:
        for ref in references(entry.model_dump(mode="json")):
            if canonical(ref.model_dump(mode="json")) not in records:
                raise IntegrityError("archive_reference_missing")


def dump_archive(bundle: ArchiveBundle) -> bytes:
    verify_bundle(bundle)
    return canonical(bundle.model_dump(mode="json"))


def load_archive(data: bytes) -> ArchiveBundle:
    try:
        bundle = ArchiveBundle.model_validate_json(data)
        verify_bundle(bundle)
        return bundle
    except ValueError:
        raise IntegrityError("archive_integrity") from None
