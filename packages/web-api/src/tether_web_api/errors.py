"""Content-free RFC 9457 error translation for public Tether failures."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tether.client import (
    ChangeGap,
    ExecutionDenied,
    LeaseLost,
    ReadLimit,
    RequestConflict,
    ResolutionConflict,
    RootBusy,
    SessionClosed,
    TurnInProgress,
    UnknownModelCall,
    UnknownSession,
    UnknownTurn,
    Unsupported,
)

from .wire import ProblemDetails


class ApiFailure(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        *,
        retryable: bool = False,
        locator: dict[str, str] | None = None,
    ) -> None:
        self.status, self.code, self.retryable = status, code, retryable
        self.locator = locator
        super().__init__(code)


def _classify(error: Exception, code: str) -> tuple[int, str, bool]:
    status, retryable = 500, False
    result_code = "internal_error"
    if isinstance(error, ApiFailure):
        status, result_code, retryable = error.status, error.code, error.retryable
    elif isinstance(error, (UnknownSession, UnknownTurn, UnknownModelCall)):
        status, result_code = 404, _unknown_code(error)
    elif isinstance(error, ExecutionDenied):
        status, result_code = 403, "denied"
    elif isinstance(error, (RequestConflict, ResolutionConflict, TurnInProgress, SessionClosed)):
        allowed = {"request_conflict", "resolution_conflict", "turn_in_progress", "session_closed"}
        status, result_code = 409, code if code in allowed else "conflict"
    elif isinstance(error, ReadLimit):
        status, result_code, retryable = _read_limit(code)
    elif isinstance(error, RootBusy):
        status, result_code, retryable = 429, "capacity_limited", True
    elif isinstance(error, LeaseLost):
        status, result_code, retryable = 503, "unavailable", True
    elif isinstance(error, Unsupported):
        status, result_code = 501, "unsupported"
    elif isinstance(error, ChangeGap):
        status, result_code, retryable = 409, "resync_required", True
    return status, result_code, retryable


def _unknown_code(error: Exception) -> str:
    if isinstance(error, UnknownTurn):
        return "unknown_turn"
    if isinstance(error, UnknownModelCall):
        return "unknown_model_call"
    return "unknown_session"


def _read_limit(code: str) -> tuple[int, str, bool]:
    if code in {"active_turn_limit", "follower_limit"}:
        return 429, "capacity_limited", True
    if code in {"read_bytes", "request_bytes", "mutation_receipt_bytes"}:
        return 413, "limit_exceeded", False
    return 400, "read_limit", False


def problem_for(error: Exception) -> ProblemDetails:
    reason = getattr(error, "reason", None)
    initial_code = str(reason or error.__class__.__name__).strip().lower()
    status, code, retryable = _classify(error, initial_code)
    titles = {
        400: "Invalid request",
        401: "Authentication required",
        403: "Forbidden",
        404: "Not found",
        408: "Request timed out",
        409: "Conflict",
        413: "Limit exceeded",
        429: "Capacity limited",
        501: "Unsupported",
        503: "Unavailable",
        500: "Internal error",
    }
    return ProblemDetails(
        type="about:blank",
        title=titles.get(status, "Request failed"),
        status=status,
        detail=titles.get(status, "Request failed"),
        code=code,
        retryable=retryable,
        locator=getattr(error, "locator", None),
    )


def install_error_handlers(app: FastAPI) -> None:
    async def known_client_failure(_request: Request, error: Exception) -> JSONResponse:
        value = problem_for(error)
        return JSONResponse(
            value.model_dump(mode="json"),
            status_code=value.status,
            media_type="application/problem+json",
        )

    for error_type in (
        UnknownSession,
        UnknownTurn,
        UnknownModelCall,
        ExecutionDenied,
        RequestConflict,
        ResolutionConflict,
        TurnInProgress,
        SessionClosed,
        ReadLimit,
        RootBusy,
        LeaseLost,
        Unsupported,
        ChangeGap,
    ):
        app.add_exception_handler(error_type, known_client_failure)

    @app.exception_handler(ApiFailure)
    async def api_failure(_request: Request, error: ApiFailure) -> JSONResponse:
        value = problem_for(error)
        return JSONResponse(
            value.model_dump(mode="json"),
            status_code=value.status,
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        value = ProblemDetails(
            title="Invalid request",
            status=400,
            detail="Invalid request",
            code="invalid_request",
            retryable=False,
        )
        return JSONResponse(
            value.model_dump(mode="json"), status_code=400, media_type="application/problem+json"
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_failure(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        status = (
            error.status_code
            if error.status_code in {401, 403, 404, 408, 409, 413, 429, 501, 503}
            else 500
        )
        codes = {
            401: "authentication_required",
            403: "denied",
            404: "not_found",
            408: "request_timeout",
            409: "conflict",
            413: "limit_exceeded",
            429: "capacity_limited",
            501: "unsupported",
            503: "unavailable",
        }
        value = ProblemDetails(
            title="Request failed",
            status=status,
            detail="Request failed",
            code=codes.get(status, "internal_error"),
            retryable=status in {408, 429, 503},
        )
        return JSONResponse(
            value.model_dump(mode="json"),
            status_code=value.status,
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def public_failure(_request: Request, error: Exception) -> JSONResponse:
        value = problem_for(error)
        return JSONResponse(
            value.model_dump(mode="json"),
            status_code=value.status,
            media_type="application/problem+json",
        )
