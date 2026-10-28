"""Opt-in compatible model composition for reference clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tether.types import ModelExecutionLimits
from tether.types.context import ContextConfig, ModelLimits, TokenEstimator
from tether.types.execution import Actor, AgentSpec

from .profile import FileAuthorization, ReferenceModelProfile, read_bounded

if TYPE_CHECKING:
    from tether.adapters.openai_compat import OpenAICompatModel


async def configured_model(
    profile: ReferenceModelProfile,
    limits: ModelExecutionLimits,
    tool_name: str,
) -> tuple[OpenAICompatModel, ContextConfig, TokenEstimator, Actor, AgentSpec]:
    """Construct the selected optional adapter without importing its SDK in base mode."""

    try:
        from tether.adapters.openai_compat import OpenAICompatModel, TokenizerProfile
    except ImportError:
        raise RuntimeError("openai_extra_required") from None

    tokenizer = TokenizerProfile.model_validate(profile.tokenizer_profile)
    try:
        vocabulary = await read_bounded(
            profile.vocabulary_path, min(tokenizer.max_vocabulary_bytes, 10 * 1024 * 1024)
        )
    except ValueError:
        raise ValueError("model_vocabulary_bytes") from None
    if profile.context_window_tokens <= profile.output_tokens + profile.safety_margin_tokens:
        raise ValueError("model_context_limit")
    adapter = OpenAICompatModel(
        base_url=profile.base_url,
        tiers={"demo": profile.model_name},
        limits=limits,
        output_limit_field=profile.output_limit_field,
    )
    context = ContextConfig(
        limits={
            "demo": ModelLimits(
                profile_id=tokenizer.id,
                revision=tokenizer.revision,
                context_window_tokens=profile.context_window_tokens,
                output_tokens=profile.output_tokens,
                safety_margin_tokens=profile.safety_margin_tokens,
            )
        },
        max_source_bytes=4 * 1024 * 1024,
        timeout_seconds=30,
    )
    try:
        estimator = adapter.estimator(profile=tokenizer, vocabulary=vocabulary)
    except Exception:
        await adapter.aclose()
        raise
    actor = Actor(id="demo", credential=FileAuthorization(profile.authorization_file))
    spec = AgentSpec(
        id="model-" + profile.id,
        version=profile.session_ref.rsplit(":", 1)[-1],
        title="Tether model · " + profile.id,
        instructions="Answer the user. Use record_demo only when asked to record a label.",
        model="demo",
        tools=(tool_name,),
    )
    return adapter, context, estimator, actor, spec
