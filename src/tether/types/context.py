"""Version1 source-addressed context values and asynchronous planning/estimation ports."""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Annotated, Literal, Protocol, Self, cast

from pydantic import ConfigDict, Field, field_validator, model_validator

from tether.types import AttachmentRef, Count, Message, ModelRequest, Payload, PositiveInt, ToolCall
from tether.types.execution import HistoryRef

CONTEXT_CONTRACT_VERSION = 1
Text = Annotated[str, Field(strict=True, min_length=1)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
Omission = Literal["budget", "provider_filter", "attachment_unavailable"]
ContextCode = Literal[
    "context_unconfigured",
    "context_unmeasurable",
    "context_invalid",
    "context_exhausted",
    "history_pending",
    "history_unavailable",
    "context_unavailable",
    "context_incompatible",
    "context_denied",
    "context_failed",
]
CODES = frozenset(
    {
        "context_unconfigured",
        "context_unmeasurable",
        "context_invalid",
        "context_exhausted",
        "history_pending",
        "history_unavailable",
        "context_unavailable",
        "context_incompatible",
        "context_denied",
        "context_failed",
    }
)


class ContextError(Exception):
    def __init__(self, code: ContextCode) -> None:
        if type(code) is not str or code not in CODES:
            raise ValueError("context_error_code")
        self.code = self.reason = code
        super().__init__(code)


class ContextValue(Payload):
    model_config = ConfigDict(revalidate_instances="always")


def ordered(values: tuple[int, ...]) -> None:
    if any(left >= right for left, right in pairwise(values)):
        raise ValueError("context_positions")


class ModelLimits(ContextValue):
    profile_id: Text
    revision: Text
    context_window_tokens: PositiveInt
    output_tokens: PositiveInt
    safety_margin_tokens: Count

    @model_validator(mode="after")
    def allowance(self) -> Self:
        if self.output_tokens + self.safety_margin_tokens >= self.context_window_tokens:
            raise ValueError("context_allowance")
        return self

    @property
    def input_tokens(self) -> int:
        return self.context_window_tokens - self.output_tokens - self.safety_margin_tokens


class TokenEstimate(ContextValue):
    tokens: Count
    method: Text
    revision: Text
    quality: Literal["exact", "conservative", "approximate"]


class ContextConfig(ContextValue):
    limits: dict[Text, ModelLimits]
    summary_model: Text | None = None
    summary_identity_mode: Literal["inherit", "caller", "application", "anonymous"] = "inherit"
    max_summary_calls_per_step: Count = 1
    max_source_bytes: PositiveInt
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


class ContextSource(ContextValue):
    index: Count
    message: Message

    @field_validator("message", mode="before")
    @classmethod
    def copy_message(cls, value: object) -> object:
        if isinstance(value, Message):
            if set(value.__dict__) != set(Message.model_fields):
                raise ValueError("context_message")
            for raw, model in ((value.attachments, AttachmentRef), (value.tool_calls, ToolCall)):
                items: object = cast(object, raw)
                if not isinstance(items, tuple) or any(
                    not isinstance(item, model) or set(item.__dict__) != set(model.model_fields)
                    for item in cast(tuple[object, ...], items)
                ):
                    raise ValueError("context_message")
            return value.model_dump(mode="python")
        return value


class SourceRange(ContextValue):
    start: Count
    end: Count

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.end < self.start:
            raise ValueError("context_range")
        return self


def ordered_ranges(ranges: tuple[SourceRange, ...]) -> None:
    if any(left.end >= right.start for left, right in pairwise(ranges)):
        raise ValueError("context_ranges")


class ContextPart(ContextValue):
    kind: Literal["source", "summary", "summarize", "omit"]
    ranges: Annotated[tuple[SourceRange, ...], Field(min_length=1)]
    summary_index: Count | None = None
    reason: Omission | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        ordered_ranges(self.ranges)
        if (self.kind == "omit") != (self.reason is not None):
            raise ValueError("context_reason")
        if self.kind in {"source", "omit"} and self.summary_index is not None:
            raise ValueError("context_summary_index")
        if self.kind == "summary" and self.summary_index is None:
            raise ValueError("context_summary_index")
        return self


class ContextGroup(ContextValue):
    indices: Annotated[tuple[Count, ...], Field(min_length=1)]
    estimate: TokenEstimate

    @model_validator(mode="after")
    def positions(self) -> Self:
        ordered(self.indices)
        return self


class ContextPlan(ContextValue):
    strategy_id: Text
    strategy_version: Text
    parts: tuple[ContextPart, ...]

    @model_validator(mode="after")
    def chronology(self) -> Self:
        ordered_ranges(tuple(r for p in self.parts for r in p.ranges))
        return self


class CompactionRecord(ContextValue):
    model_call_id: Text
    preparation_ordinal: Count
    part_position: Count
    config_digest: Digest
    summary_profile_id: Text
    summary_profile_revision: Text
    summary_profile_digest: Digest
    strategy_id: Text
    strategy_version: Text
    through_index: Count
    source_ranges: Annotated[tuple[SourceRange, ...], Field(min_length=1)]
    source_view_digest: Digest
    scope_digest: Digest
    spec_digest: Digest
    profile_id: Text
    profile_revision: Text
    summary_ref: HistoryRef
    predecessor: Count | None = None
    lossy: Literal[True] = True

    @field_validator("lossy", mode="before")
    @classmethod
    def strict_lossy(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("context_lossy")
        return value

    @model_validator(mode="after")
    def provenance(self) -> Self:
        ordered_ranges(self.source_ranges)
        if (
            not self.lossy
            or self.source_ranges[-1].end > self.through_index
            or (self.predecessor is not None and self.predecessor > self.through_index)
        ):
            raise ValueError("context_compaction")
        ref = self.summary_ref
        if (
            ref.kind != "summary"
            or ref.component not in {"record", "summary"}
            or re.fullmatch(r"[a-f0-9]{64}", ref.sha256) is None
            or not all((ref.owner, ref.session_id, ref.provider, ref.key))
        ):
            raise ValueError("context_summary_ref")
        return self


class ContextSummary(ContextValue):
    index: Count
    record: CompactionRecord

    @model_validator(mode="after")
    def committed(self) -> Self:
        if self.index <= self.record.through_index or (
            self.record.predecessor is not None and self.record.predecessor >= self.index
        ):
            raise ValueError("context_summary_position")
        return self


class ContextPreparation(ContextValue):
    model_call_id: Text
    ordinal: Count
    through_index: Count
    source_view_digest: Digest
    scope_digest: Digest
    spec_digest: Digest
    config_digest: Digest
    main_profile_digest: Digest
    summary_profile_digest: Digest | None = None
    strategy_id: Text
    strategy_version: Text


class ContextUse(ContextValue):
    model_call_id: Text
    preparation_ordinal: Count
    config_digest: Digest
    through_index: Count
    parts: tuple[ContextPart, ...]
    source_view_digest: Digest
    request_digest: Digest
    profile_id: Text
    profile_revision: Text
    estimate: TokenEstimate
    output_tokens: PositiveInt
    safety_margin_tokens: Count

    @model_validator(mode="after")
    def materialized(self) -> Self:
        ordered_ranges(tuple(r for p in self.parts for r in p.ranges))
        if any(p.kind == "summarize" or p.ranges[-1].end > self.through_index for p in self.parts):
            raise ValueError("context_use_parts")
        return self


class ContextInput(ContextValue):
    session_id: Text
    through_index: Count
    sources: tuple[ContextSource, ...]
    summaries: tuple[ContextSummary, ...]
    protected: frozenset[Count]
    limits: ModelLimits
    source_view_digest: Digest
    fixed_estimate: TokenEstimate
    groups: tuple[ContextGroup, ...]
    filtered_indices: tuple[Count, ...] = ()

    @model_validator(mode="after")
    def snapshot(self) -> Self:
        indices = tuple(s.index for s in self.sources)
        ordered(indices)
        ordered(self.filtered_indices)
        if set(indices) & set(self.filtered_indices) or not self.protected.issubset(indices):
            raise ValueError("context_source_scope")
        if any(i > self.through_index for i in (*indices, *self.filtered_indices)):
            raise ValueError("context_source_scope")
        if tuple(i for group in self.groups for i in group.indices) != indices:
            raise ValueError("context_group_partition")
        if any(
            g.indices[0] < f < g.indices[-1] for g in self.groups for f in self.filtered_indices
        ):
            raise ValueError("context_group_filtered")
        summary_indices = tuple(s.index for s in self.summaries)
        ordered(summary_indices)
        if set(summary_indices) & (set(indices) | set(self.filtered_indices)):
            raise ValueError("context_summary_scope")
        if any(
            s.index > self.through_index or s.record.summary_ref.session_id != self.session_id
            for s in self.summaries
        ):
            raise ValueError("context_summary_scope")
        return self


class TokenEstimator(Protocol):
    async def estimate(self, request: ModelRequest) -> TokenEstimate: ...


class ContextStrategy(Protocol):
    id: str
    version: str

    async def plan(self, context: ContextInput) -> ContextPlan: ...
