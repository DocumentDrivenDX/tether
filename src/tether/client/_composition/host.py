"""Concrete host construction, isolated from the public client protocol."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from tether.client.protocol import ClientProtocol
from tether.engine import Engine
from tether.engine.governance import utc_now
from tether.history.jsonl import JsonlHistory
from tether.history.messages import MessageHistoryAdapter
from tether.ports import (
    AttemptRecorder,
    AuditSink,
    HistoryProvider,
    ModelPort,
    Provider,
    ResultGuard,
    SessionStore,
    Tracer,
)
from tether.sessions.service import SessionService
from tether.stores.jsonl import JsonlRoot, JsonlSessionStore
from tether.stores.memory import MemoryFiles
from tether.types import ModelExecutionLimits, RequestTags
from tether.types.archive import ArchiveSink as ArchiveSinkType
from tether.types.archive import ArchiveSource
from tether.types.client import ClientLimits
from tether.types.context import ContextConfig, ContextStrategy, TokenEstimator
from tether.types.execution import Actor, AgentSpec, Clock, ExecutionLimits, Policy, Tool
from tether.types.history import HistoryProvider as HistoryProviderType
from tether.types.storage import Unsupported


@dataclass(frozen=True)
class LocalJsonlSettings:
    root: Path


@dataclass(frozen=True)
class VolatileSettings:
    """Explicit process-lifetime storage for fabricated demos; never restart-durable."""


@dataclass(frozen=True)
class ProvidedStorage:
    store: SessionStore
    history: HistoryProviderType


@dataclass(frozen=True)
class ClientSettings:
    model: ModelPort
    storage: LocalJsonlSettings | VolatileSettings | ProvidedStorage
    policy: Policy
    client_limits: ClientLimits
    model_limits: ModelExecutionLimits
    context_config: ContextConfig
    estimator: TokenEstimator
    specs: tuple[AgentSpec, ...]
    tools: tuple[Tool, ...] = ()
    identity_mode: Literal["caller", "application", "anonymous"] = "caller"
    resume: Literal["off", "report", "auto"] = "report"
    default_spec: str | None = None
    archive: ArchiveSource | None = None
    archive_sink: ArchiveSinkType | None = None


@dataclass(frozen=True)
class ClientPorts:
    providers: tuple[Provider, ...] = ()
    guards: tuple[ResultGuard, ...] = ()
    attempt_factory: Callable[[RequestTags], AttemptRecorder] | None = None
    audit: AuditSink | None = None
    tracer: Tracer | None = None
    context: ContextStrategy | None = None
    execution_limits: ExecutionLimits | None = None
    clock: Clock | None = None


class ClientHost:
    """Owns a composed client service and only the resources it opens."""

    def __init__(self, service: SessionService) -> None:
        self._service = service
        self._closed = False

    @classmethod
    async def open(cls, settings: ClientSettings) -> Self:
        return await cls._open(settings, ClientPorts())

    @classmethod
    async def from_ports(cls, settings: ClientSettings, *, ports: ClientPorts) -> Self:
        return await cls._open(settings, ports)

    @classmethod
    async def _open(cls, settings: ClientSettings, ports: ClientPorts) -> Self:
        root: JsonlRoot | None = None
        store: SessionStore | None = None
        history: HistoryProvider | None = None
        engine: Engine | None = None
        owns_storage = isinstance(settings.storage, (LocalJsonlSettings, VolatileSettings))
        service: SessionService | None = None
        try:
            if isinstance(settings.storage, (LocalJsonlSettings, VolatileSettings)):
                if isinstance(settings.storage, LocalJsonlSettings):
                    root = await JsonlRoot.open(settings.storage.root)
                    files = root
                else:
                    if settings.resume != "off":
                        raise Unsupported("volatile_storage_requires_resume_off")
                    files = MemoryFiles()
                store = await JsonlSessionStore.open(files)
                history = await JsonlHistory.open(files)
            else:
                if settings.resume != "off":
                    raise Unsupported("borrowed_storage_requires_resume_off")
                store, history = settings.storage.store, settings.storage.history

            adapter = MessageHistoryAdapter(history)
            engine = Engine(
                model=settings.model,
                history=adapter,
                history_provider=history,
                model_limits=settings.model_limits,
                identity_mode=settings.identity_mode,
                attempt_factory=ports.attempt_factory,
                tools=settings.tools,
                providers=ports.providers,
                guards=ports.guards,
                policy=settings.policy,
                limits=ports.execution_limits,
                clock=ports.clock or utc_now,
                context_config=settings.context_config,
                estimator=settings.estimator,
                context=ports.context,
                specs=settings.specs,
                audit=ports.audit,
                tracer=ports.tracer,
            )
            service = SessionService(
                engine=engine,
                store=store,
                history=history,
                limits=settings.client_limits,
                resume=settings.resume,
                default_spec=settings.default_spec,
                archive=settings.archive,
                archive_sink=settings.archive_sink,
                close_store=owns_storage,
                _storage_drain=root.wait_closed if root is not None else None,
            )
            await service.__aenter__()
            return cls(service)
        except BaseException:
            if service is not None:
                await service.aclose()
            else:
                if engine is not None:
                    await engine.aclose()
                if owns_storage and store is not None:
                    await store.aclose()
                elif owns_storage and root is not None:
                    await root.aclose()
            raise

    def client(self, actor: Actor) -> ClientProtocol:
        if self._closed:
            raise RuntimeError("tether_not_active")
        from .client import Client

        return Client(self._service, actor)

    @property
    def store(self) -> SessionStore:
        return self._service.store

    @property
    def resume_mode(self) -> str:
        return self._service.resume_mode

    @property
    def drained(self) -> bool:
        return self._service.drained

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("tether_not_active")
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def wait_closed(self) -> bool:
        """Wait for an initiated shutdown; cancelling this wait cannot cancel cleanup."""
        return await self._service.wait_closed()

    async def aclose(self) -> bool:
        if self._closed:
            return self._service.drained
        self._closed = True
        return await self._service.aclose()
