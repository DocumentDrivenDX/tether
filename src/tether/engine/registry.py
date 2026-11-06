"""Complete immutable registry generations and bounded atomic publication."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from tether.types import JsonValue, ToolCall
from tether.types.canonical import canonical, digest
from tether.types.execution import (
    ENGINE_CONTRACT_VERSION,
    CallManifest,
    ExecutionDenied,
    Provider,
    Tool,
    TurnLog,
)
from tether.types.tooling import definition, tool_spec

from .effects import unresolved_effects


class SnapshotTool:
    _traits: dict[str, JsonValue]
    _source: Tool

    def __init__(self, tool: Tool) -> None:
        for name in (
            "name",
            "version",
            "description",
            "input_model",
            "requires",
            "model_visible",
            "always_ask",
            "effect",
            "replay",
            "timeout_seconds",
            "definition_hash",
        ):
            object.__setattr__(self, name, getattr(tool, name))
        object.__setattr__(self, "_traits", deepcopy(tool.traits))
        object.__setattr__(self, "_source", tool)

    @property
    def run(self):
        return self._source.run

    @property
    def asks_first(self):
        return self._source.asks_first

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("immutable_tool_generation")

    @property
    def traits(self) -> dict[str, JsonValue]:
        return deepcopy(self._traits)


@dataclass(frozen=True)
class PinnedTool:
    tool: Tool
    sha256: str
    original: Tool


class Registry:
    def __init__(self, tools: tuple[Tool, ...], providers: tuple[Provider, ...]) -> None:
        selected: dict[str, PinnedTool] = {}
        self.providers = providers
        ids = [p.id for p in providers]
        if len(set(ids)) != len(ids) or any(
            not 1 <= p.contract <= ENGINE_CONTRACT_VERSION for p in providers
        ):
            raise ValueError("provider_conflict")
        for tool in (*tools, *(t for p in providers for t in p.tools())):
            if tool.name in selected:
                raise ValueError("provider_conflict")
            tool_spec(tool)
            # Trusted executable implementations stay bound; declaration values are copied.
            from typing import cast

            selected[tool.name] = PinnedTool(
                cast(Tool, SnapshotTool(tool)), digest(definition(tool)), tool
            )
        self.tools: Mapping[str, PinnedTool] = MappingProxyType(selected)
        self.generation = digest(
            {
                name: [pinned.sha256, pinned.tool.model_visible, pinned.tool.timeout_seconds]
                for name, pinned in sorted(selected.items())
            }
        )

    def manifest(self, call: ToolCall, position: int) -> CallManifest:
        pinned = self.tools[call.name]
        return CallManifest(
            position=position,
            call_id=call.id,
            tool=call.name,
            args_digest=digest(call.arguments),
            tool_digest=pinned.sha256,
            effect=pinned.tool.effect,
            replay=pinned.tool.replay,
            registry_generation=self.generation,
        )


class RegistryPublisher:
    def __init__(
        self,
        initial: Registry,
        *,
        publish: Callable[[Registry], None],
        busy: Callable[[str], bool],
        check: Callable[[], None],
        max_generations: int = 8,
        max_tools: int = 1000,
        max_definition_bytes: int = 1048576,
    ) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (max_generations, max_tools, max_definition_bytes)
        ):
            raise ValueError("registry_limits")
        self.current = initial
        self.generations = {initial.generation: initial}
        self._publish, self._busy, self._check = publish, busy, check
        self._holds: dict[str, int] = {}
        self._effects: dict[str, str] = {}
        self.max_generations, self.max_tools, self.max_definition_bytes = (
            max_generations,
            max_tools,
            max_definition_bytes,
        )

    def publish(self, tools: tuple[Tool, ...]) -> str:
        self._check()
        generation = Registry(tools, self.current.providers)
        if (
            len(generation.tools) > self.max_tools
            or len(canonical({name: definition(p.tool) for name, p in generation.tools.items()}))
            > self.max_definition_bytes
        ):
            raise ExecutionDenied("registry_capacity")
        retained = {
            key: value
            for key, value in self.generations.items()
            if self._holds.get(key, 0) or self._busy(key) or key in self._effects.values()
        }
        retained[generation.generation] = generation
        if len(retained) > self.max_generations:
            raise ExecutionDenied("registry_capacity")
        self.generations = retained
        self.current = generation
        self._publish(generation)
        return generation.generation

    def admit(self, session_id: str, active: Sequence[str]) -> None:
        if len(set(self._effects) | set(active) | {session_id}) > self.max_tools:
            raise ExecutionDenied("registry_effect_capacity")

    def record_effects(self, session_id: str, generation: str, log: TurnLog) -> None:
        try:
            unresolved = unresolved_effects(log.entries)
        except BaseException:
            # Failed storage inspection cannot mask a primary unknown/cancelled operation.
            unresolved = True
        if unresolved:
            self._effects.setdefault(session_id, generation)
        else:
            self._effects.pop(session_id, None)

    def retain(self, generation: str) -> None:
        self._holds[generation] = self._holds.get(generation, 0) + 1

    def release_after(self, generation: str, futures: tuple[asyncio.Future[object], ...]) -> None:
        remaining = set(futures)

        def release(future: asyncio.Future[object] | None = None) -> None:
            if future is not None:
                remaining.discard(future)
            if not remaining:
                count = self._holds[generation] - 1
                if count:
                    self._holds[generation] = count
                else:
                    self._holds.pop(generation)

        if remaining:
            for future in remaining:
                future.add_done_callback(release)
        else:
            release()
