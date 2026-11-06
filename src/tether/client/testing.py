"""Explicit offline model/context factories for fabricated reference and conformance cases."""

from collections.abc import Sequence
from typing import Literal, TypedDict

from tether.context.testing import fabricated_context
from tether.models.testing import ReplayModel, ScriptedModel
from tether.ports import ModelPort, Policy
from tether.types import StreamItem
from tether.types.context import ContextConfig, TokenEstimator

from ._composition.fixtures import fabricated_storage as fabricated_storage


class FabricatedContext(TypedDict):
    context_config: ContextConfig
    estimator: TokenEstimator


def demo_policy() -> Policy:
    from tether.engine.governance import AllowAll

    return AllowAll()


def scripted_model(script: Sequence[StreamItem | Exception]) -> ModelPort:
    return ScriptedModel(script)


def replay_model(cassette: str, *, mode: Literal["sequence", "keyed"] = "sequence") -> ModelPort:
    return ReplayModel(cassette, mode=mode)


def fabricated_model_context() -> FabricatedContext:
    """Fixture budgets only; this never qualifies a live model's wire capacity."""
    context = fabricated_context()
    return {"context_config": context["context_config"], "estimator": context["estimator"]}


__all__ = [
    "demo_policy",
    "fabricated_model_context",
    "fabricated_storage",
    "replay_model",
    "scripted_model",
]
