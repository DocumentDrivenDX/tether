"""Factory for the optional FastAPI boundary."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tether.client import ClientProvider

from .auth import AuthContext, Authenticator
from .boundary import RequestBoundary
from .errors import install_error_handlers
from .openapi import install_openapi
from .routes.events import build_router as build_events_router
from .routes.sessions import build_router as build_sessions_router
from .routes.turns import build_router as build_turns_router
from .settings import ApiSettings


def _install_cors(app: FastAPI, settings: ApiSettings) -> None:
    if not settings.allowed_origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
        expose_headers=["Content-Type"],
    )


def create_api(
    *, clients: ClientProvider, authenticator: Authenticator, settings: ApiSettings
) -> FastAPI:
    """Build an API app with all authority supplied by the embedding application."""
    auth = AuthContext(authenticator, clients, float(settings.request_timeout_seconds))
    app = FastAPI(
        title="Tether HTTP API",
        version="1.0.0",
        openapi_url=settings.api_prefix + "/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    install_error_handlers(app)
    _install_cors(app, settings)
    app.add_middleware(RequestBoundary, settings=settings)
    app.include_router(build_sessions_router(auth), prefix=settings.api_prefix)
    app.include_router(build_turns_router(auth, settings), prefix=settings.api_prefix)
    app.include_router(build_events_router(auth, settings), prefix=settings.api_prefix)
    install_openapi(app, settings)
    app.state.api_settings = settings
    return app
