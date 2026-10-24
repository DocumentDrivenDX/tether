"""Fabricated conformance fixtures test port behavior, never wire-token qualification."""

from contextlib import asynccontextmanager

import pytest

from tether.context import FullContext, TailContext
from tether.context.materialization import merged_parts, validated_merged
from tether.context.testing import FixedEstimator, ScriptedContext
from tether.testing.conformance import (
    ConformanceFailure,
    TokenEstimatorFixture,
    context_strategy_suite,
    token_estimator_suite,
)
from tether.testing.conformance.context import estimator_cases, input_case
from tether.types.context import ContextError, ContextPart, ContextPlan, SourceRange

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("kind", ["full", "tail", "scripted"])
async def test_context_port_suite_checks_both_independent_group_shapes(kind):
    @asynccontextmanager
    async def factory():
        if kind == "full":
            yield FullContext()
        elif kind == "tail":
            yield TailContext()
        else:
            plans = [await FullContext().plan(input_case(grouped)) for grouped in (False, True)]
            yield ScriptedContext(plans, strategy_id="full")

    report = await context_strategy_suite(factory)
    assert "context_plan_valid" in report.checks
    assert "full_request_fit" in report.required_external
    assert report.capabilities == {"contract": 1}


async def test_estimator_fixture_reports_fabricated_evidence_and_external_obligations():
    @asynccontextmanager
    async def factory():
        yield TokenEstimatorFixture(FixedEstimator(17), lambda request: (17, 17))

    report = await token_estimator_suite(factory)
    assert report.capabilities["evidence"] == "fabricated"
    assert "estimate_tools_bounds" in report.checks
    assert "real_serialized_payload_framing" in report.required_external


async def test_estimator_suite_catches_underestimate_against_separate_oracle():
    @asynccontextmanager
    async def factory():
        yield TokenEstimatorFixture(FixedEstimator(1), lambda request: (2, 3))

    with pytest.raises(ConformanceFailure, match="estimate_framing_bounds"):
        await token_estimator_suite(factory)


async def test_estimator_suite_accepts_explicit_attachment_refusal():
    class Refusing(FixedEstimator):
        async def estimate(self, request):
            if any(m.attachments for m in request.messages):
                raise ContextError("context_unmeasurable")
            return await super().estimate(request)

    @asynccontextmanager
    async def factory():
        yield TokenEstimatorFixture(Refusing(3), lambda request: (3, 3), attachments="refuses")

    report = await token_estimator_suite(factory)
    assert "estimate_attachment_refusal" in report.checks


@pytest.mark.parametrize("kind", ["plan", "estimator", "refusal"])
async def test_port_suites_reject_input_mutation(kind):
    class MutatingStrategy:
        id, version = "full", "1"

        async def plan(self, context):
            answer = await FullContext().plan(context)
            object.__setattr__(context.sources[0].message, "content", "Mutated")
            return answer

    class MutatingEstimator(FixedEstimator):
        async def estimate(self, request):
            if kind != "refusal" or any(m.attachments for m in request.messages):
                object.__setattr__(request, "system", "Mutated")
            if kind == "refusal" and any(m.attachments for m in request.messages):
                raise ContextError("context_unmeasurable")
            return await super().estimate(request)

    @asynccontextmanager
    async def strategy_factory():
        yield MutatingStrategy()

    @asynccontextmanager
    async def estimator_factory():
        yield TokenEstimatorFixture(
            MutatingEstimator(3),
            lambda request: (3, 3),
            attachments="refuses" if kind == "refusal" else "estimates",
        )

    operation = (
        context_strategy_suite(strategy_factory)
        if kind == "plan"
        else token_estimator_suite(estimator_factory)
    )
    with pytest.raises(ConformanceFailure, match="input_immutable"):
        await operation


async def test_scripted_dummy_copies_plans_inputs_and_failures_and_bounds_capture():
    original = input_case(False)
    selected = await FullContext().plan(original)
    dummy = ScriptedContext(
        [selected, ContextError("context_denied")], strategy_id="full", capacity=2
    )
    answer = await dummy.plan(original)
    assert answer == selected
    assert answer is not selected
    object.__setattr__(original.sources[0].message, "content", "Changed later")
    assert dummy.requests[0].sources[0].message.content != "Changed later"
    with pytest.raises(ContextError, match="context_denied"):
        await dummy.plan(original)
    with pytest.raises(ContextError, match="context_exhausted"):
        await dummy.plan(original)
    with pytest.raises(ContextError, match="context_failed"):
        await ScriptedContext([]).plan(original)
    with pytest.raises(ContextError, match="context_exhausted"):
        ScriptedContext([selected], max_bytes=1)


async def test_estimator_capture_is_bounded_deep_and_strips_live_capabilities():
    request = dict(estimator_cases())["tools"]

    class Credential:
        async def authorization(self):
            raise AssertionError("credential must not be read")

    request = request.model_copy(update={"credential": Credential(), "attempts": object()})
    dummy = FixedEstimator(5, capacity=1)
    assert (await dummy.estimate(request)).tokens == 5
    assert dummy.requests[0].credential is dummy.requests[0].attempts is None
    request.tools[0].input_schema["title"] = "Changed later"
    assert "title" not in dummy.requests[0].tools[0].input_schema
    with pytest.raises(ContextError, match="context_exhausted"):
        await dummy.estimate(request)
    with pytest.raises(ContextError, match="context_exhausted"):
        await FixedEstimator(5, max_bytes=1).estimate(request)


@pytest.mark.parametrize("value", [True, 0, -1, "2"])
async def test_doubles_refuse_invalid_capacity(value):
    with pytest.raises(ValueError, match="context_fixture_capacity"):
        FixedEstimator(0, capacity=value)


async def test_final_merge_rejects_missing_forged_and_reordered_provider_markers():
    original = input_case(False).model_copy(update={"filtered_indices": (1, 3)})
    selected = await FullContext().plan(original)
    final = merged_parts(original, selected)
    assert validated_merged(original, selected, final) == final
    for bad in (final[:-1], tuple(reversed(final)), (*final, final[0])):
        with pytest.raises(ContextError, match="context_invalid"):
            validated_merged(original, selected, bad)
    forged = tuple(
        p.model_copy(update={"reason": "budget"}) if p.reason == "provider_filter" else p
        for p in final
    )
    with pytest.raises(ContextError):
        validated_merged(original, selected, forged)


async def test_multi_range_part_cannot_jump_over_filtered_entry():
    original = input_case(False).model_copy(update={"filtered_indices": (1,)})
    selected = ContextPlan(
        strategy_id="fixture",
        strategy_version="1",
        parts=(
            ContextPart(
                kind="source", ranges=(SourceRange(start=0, end=0), SourceRange(start=2, end=4))
            ),
        ),
    )
    with pytest.raises(ContextError, match="context_invalid"):
        merged_parts(original, selected)


async def test_scripted_answer_inventory_also_obeys_aggregate_byte_bound():
    selected = await FullContext().plan(input_case(False))
    huge = selected.model_copy(update={"strategy_version": "v" * 2000})
    with pytest.raises(ContextError, match="context_exhausted"):
        ScriptedContext([huge], max_bytes=1000)
    with pytest.raises(ValueError, match="context_fixture_capacity"):
        ScriptedContext([selected, selected], capacity=1)


@pytest.mark.parametrize("seconds", [True, 0, -1, float("inf"), float("nan")])
async def test_conformance_suite_timeout_requires_finite_positive_number(seconds):
    @asynccontextmanager
    async def factory():
        raise AssertionError("invalid timeout must refuse before opening fixture")
        yield FullContext()

    with pytest.raises(ValueError, match="context_suite_timeout"):
        await context_strategy_suite(factory, timeout_seconds=seconds)
