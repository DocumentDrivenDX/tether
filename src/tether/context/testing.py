"""Bounded deterministic dummies; estimates and source content are fabricated test evidence."""

from collections import deque
from collections.abc import Sequence

from tether.types import ModelRequest
from tether.types.canonical import canonical
from tether.types.context import ContextCode, ContextError, ContextInput, ContextPlan, TokenEstimate

from .validation import copied, validated_input


class _Capacity:
    def __init__(self, count: int, size: int) -> None:
        if type(count) is not int or type(size) is not int or min(count, size) <= 0:
            raise ValueError("context_fixture_capacity")
        self.count, self.size, self.used = count, size, 0

    def admit(self, value: bytes, count: int) -> None:
        if count >= self.count or self.used + len(value) > self.size:
            raise ContextError("context_exhausted")
        self.used += len(value)


class ScriptedContext:
    def __init__(
        self,
        plans: Sequence[ContextPlan | ContextError],
        *,
        strategy_id: str = "scripted",
        strategy_version: str = "1",
        capacity: int = 32,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._capacity = _Capacity(capacity, max_bytes)
        identity = ContextPlan(strategy_id=strategy_id, strategy_version=strategy_version, parts=())
        self.id, self.version = identity.strategy_id, identity.strategy_version
        if len(plans) > capacity:
            raise ValueError("context_fixture_capacity")
        self._plans: deque[ContextPlan | ContextCode] = deque()
        self._capacity.admit(canonical(identity.model_dump(mode="json")), 0)
        for item in plans:
            answer = item.code if isinstance(item, ContextError) else copied(ContextPlan, item)
            self._capacity.admit(
                canonical(answer if isinstance(answer, str) else answer.model_dump(mode="json")), 0
            )
            self._plans.append(answer)
        self.requests: list[ContextInput] = []

    async def plan(self, context: ContextInput) -> ContextPlan:
        snapshot = validated_input(context)
        self._capacity.admit(canonical(snapshot.model_dump(mode="json")), len(self.requests))
        self.requests.append(snapshot)
        if not self._plans:
            raise ContextError("context_failed")
        answer = self._plans.popleft()
        if isinstance(answer, str):
            raise ContextError(answer)
        return copied(ContextPlan, answer)


class FixedEstimator:
    def __init__(
        self,
        tokens: int,
        *,
        capacity: int = 32,
        max_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._estimate = TokenEstimate(
            tokens=tokens, method="fixed-fixture", revision="1", quality="approximate"
        )
        self._capacity = _Capacity(capacity, max_bytes)
        self.requests: list[ModelRequest] = []

    async def estimate(self, request: ModelRequest) -> TokenEstimate:
        data = request.model_dump(mode="python")
        snapshot = ModelRequest.model_validate(data).model_copy(deep=True)
        self._capacity.admit(canonical(snapshot.model_dump(mode="json")), len(self.requests))
        self.requests.append(snapshot)
        return self._estimate.model_copy(deep=True)
