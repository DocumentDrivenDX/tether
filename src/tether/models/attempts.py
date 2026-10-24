"""Common scope validation for reserved physical attempt identities."""

from tether.types import AttemptIdentity, ModelAttemptDenied, ModelRequest


def validate_identity(request: ModelRequest, identity: AttemptIdentity) -> None:
    tags = request.tags
    if (identity.session_id, identity.turn_id, identity.model_call_id, identity.purpose) != (
        tags.session_id,
        tags.turn_id,
        tags.model_call_id,
        tags.purpose,
    ):
        raise ModelAttemptDenied("attempt_identity_mismatch")
