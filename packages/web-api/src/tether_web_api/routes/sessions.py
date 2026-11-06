"""Composition of session collection, read, and metadata routes."""

from fastapi import APIRouter

from ..auth import AuthContext
from .session_collection import build_router as build_collection_router
from .session_reads import build_router as build_reads_router
from .session_writes import build_router as build_writes_router


def build_router(auth: AuthContext) -> APIRouter:
    router = APIRouter()
    router.include_router(build_collection_router(auth))
    router.include_router(build_reads_router(auth))
    router.include_router(build_writes_router(auth))
    return router
