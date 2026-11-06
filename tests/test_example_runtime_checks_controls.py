"""Check-sequencing controls for the selected example schema-refusal proof (fabricated)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import selected_profiles_example_runtime as runtime
from databricks.sdk import WorkspaceClient

from tether.client import Actor

_ACTOR = Actor(id="caller")
_CONSTRUCTOR = runtime._ConstructorInputs("https://workspace.example.test", "profile-1", 1)
_VALUES = {
    "TETHER_PUBLIC_ORIGIN": "https://app.example.test",
    "TETHER_PG_HOST": "database.example.test",
    "TETHER_PG_DATABASE": "db",
    "TETHER_PG_SCHEMA": "schema1",
    "TETHER_PG_ENDPOINT": "endpoint1",
    "TETHER_PG_INFRA_ROLE": "infra",
}


class _Report:
    def __init__(self, complete: bool) -> None:
        self.complete = complete


class _App:
    def __init__(self, complete: bool = True) -> None:
        self.shutdown_report: _Report | None = _Report(complete)


def _patch_lifespan(monkeypatch: pytest.MonkeyPatch, stops: list[str]) -> None:
    async def lifespan(app: object) -> Any:
        async def stop() -> None:
            stops.append("stopped")

        return stop

    monkeypatch.setattr(runtime, "_lifespan", lifespan)


def _patch_build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    api: int = 1,
    ui: int = 1,
    close: int = 1,
    complete: bool = True,
    error: Exception | None = None,
) -> None:
    async def build(*_args: object, **kwargs: Any) -> tuple[object, object, object]:
        factories = kwargs["factories"]
        if error is not None:
            raise error
        factories.api_calls, factories.ui_calls = api, ui
        return _App(complete), SimpleNamespace(close_calls=close), factories

    monkeypatch.setattr(runtime, "_build", build)


def test_build_wires_bindings_and_publishes_through_reference_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def build_app(bindings: Any, *, api_factory: Any, ui_factory: Any) -> str:
        captured.update(bindings=bindings, api=api_factory, ui=ui_factory)
        return "app"

    async def provider(settings: Any, source: Any) -> Any:
        return SimpleNamespace(settings=settings, source=source)

    monkeypatch.setattr(runtime, "build_reference_app", build_app)
    monkeypatch.setattr(runtime, "_resource_provider", provider)
    factories = runtime._Factories()
    app, opened, same = asyncio.run(
        runtime._build(
            _VALUES,
            schema="schema1",
            actor=_ACTOR,
            source=cast(Any, "source"),
            factories=factories,
            constructor=_CONSTRUCTOR,
        )
    )
    assert (app, same) == ("app", factories)
    assert cast(Any, opened).source == "source"
    bindings = captured["bindings"]
    assert bindings.infrastructure_actor is _ACTOR
    assert bindings.settings.database_profile.migration_owner == "caller"
    assert bindings.settings.database_profile.schema_name == "schema1"
    assert captured["api"] == factories.api
    assert captured["ui"] == factories.ui


def test_good_startup_requires_published_factories_and_complete_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stops: list[str] = []
    _patch_lifespan(monkeypatch, stops)
    _patch_build(monkeypatch)
    asyncio.run(
        runtime._check_good_example_startup(_VALUES, "s", _ACTOR, cast(Any, "src"), _CONSTRUCTOR)
    )
    assert stops == ["stopped"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"api": 0},
        {"ui": 2},
        {"close": 0},
        {"complete": False},
    ],
)
def test_good_startup_refuses_incomplete_evidence(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    _patch_lifespan(monkeypatch, [])
    _patch_build(monkeypatch, **overrides)
    with pytest.raises(RuntimeError) as info:
        asyncio.run(
            runtime._check_good_example_startup(
                _VALUES, "s", _ACTOR, cast(Any, "src"), _CONSTRUCTOR
            )
        )
    expected = (
        "selected_example_factories_not_published"
        if overrides.keys() & {"api", "ui"}
        else "selected_example_startup_or_close_incomplete"
    )
    assert str(info.value) == expected


def test_good_startup_refuses_missing_shutdown_report(monkeypatch: pytest.MonkeyPatch) -> None:
    async def build(*_args: object, **kwargs: Any) -> tuple[object, object, object]:
        factories = kwargs["factories"]
        factories.api_calls = factories.ui_calls = 1
        app = _App()
        app.shutdown_report = None
        return app, SimpleNamespace(close_calls=1), factories

    _patch_lifespan(monkeypatch, [])
    monkeypatch.setattr(runtime, "_build", build)
    with pytest.raises(RuntimeError, match="selected_example_startup_or_close_incomplete"):
        asyncio.run(
            runtime._check_good_example_startup(
                _VALUES, "s", _ACTOR, cast(Any, "src"), _CONSTRUCTOR
            )
        )


class _Provider:
    def __init__(self, close_calls: int) -> None:
        self.close_calls = close_calls


def _patch_missing(
    monkeypatch: pytest.MonkeyPatch, *, close_calls: int = 1, classified: str | None = "code"
) -> None:
    async def provider(settings: Any, source: Any) -> _Provider:
        return _Provider(close_calls)

    monkeypatch.setattr(runtime, "_resource_provider", provider)
    monkeypatch.setattr(runtime, "_missing_schema_refusal_code", lambda error: classified)


def test_missing_schema_refusal_accepts_classified_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_missing(monkeypatch)
    _patch_build(monkeypatch, error=RuntimeError("database_schema_missing"))
    asyncio.run(
        runtime._check_missing_schema_refusal(
            _VALUES, "missing", _ACTOR, cast(Any, "src"), _CONSTRUCTOR
        )
    )


def test_missing_schema_refusal_rejects_unclassified_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_missing(monkeypatch, classified=None)
    _patch_build(monkeypatch, error=RuntimeError("something_else"))
    with pytest.raises(RuntimeError, match="missing_schema_refusal_unclassified"):
        asyncio.run(
            runtime._check_missing_schema_refusal(
                _VALUES, "missing", _ACTOR, cast(Any, "src"), _CONSTRUCTOR
            )
        )


def test_missing_schema_refusal_rejects_unexpected_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stops: list[str] = []
    _patch_missing(monkeypatch)
    _patch_lifespan(monkeypatch, stops)
    _patch_build(monkeypatch)
    with pytest.raises(RuntimeError, match="missing_schema_open_unexpectedly_succeeded"):
        asyncio.run(
            runtime._check_missing_schema_refusal(
                _VALUES, "missing", _ACTOR, cast(Any, "src"), _CONSTRUCTOR
            )
        )
    assert stops == ["stopped"]


def test_missing_schema_refusal_detects_leaked_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_missing(monkeypatch, close_calls=0)
    _patch_build(monkeypatch, error=RuntimeError("database_schema_missing"))
    with pytest.raises(RuntimeError, match="missing_schema_published_factories_or_leaked_lease"):
        asyncio.run(
            runtime._check_missing_schema_refusal(
                _VALUES, "missing", _ACTOR, cast(Any, "src"), _CONSTRUCTOR
            )
        )


def _patch_orchestration(
    monkeypatch: pytest.MonkeyPatch, *, snapshots: list[object], isolated: bool = True
) -> list[str]:
    order: list[str] = []
    queue = list(snapshots)

    async def source(workspace: object, values: object) -> tuple[Actor, str]:
        order.append("source")
        return _ACTOR, "src"

    async def existing(*_args: object) -> object:
        order.append("existing")
        return queue.pop(0)

    async def good(*_args: object) -> None:
        order.append("good")

    async def snapshot(_source: object, _actor: object, schema: str) -> object:
        order.append(
            "snapshot:" + ("missing" if schema.startswith("fabricated_missing_") else "sel")
        )
        return queue.pop(0)

    async def missing(*_args: object) -> None:
        order.append("refusal")

    async def run_thread(operation: Any, *args: object, **kwargs: object) -> object:
        order.append("constructor")
        return _CONSTRUCTOR

    monkeypatch.setattr(runtime, "_selected_nonadmin_source", source)
    monkeypatch.setattr(runtime, "_check_existing_schema", existing)
    monkeypatch.setattr(runtime, "_check_good_example_startup", good)
    monkeypatch.setattr(runtime, "_snapshot", snapshot)
    monkeypatch.setattr(runtime, "_check_missing_schema_refusal", missing)
    monkeypatch.setattr(runtime, "_run_thread", run_thread)
    return order


def _run_refusal() -> None:
    workspace = cast(WorkspaceClient, SimpleNamespace(config=SimpleNamespace(host="https://w")))
    asyncio.run(
        runtime.selected_example_schema_refusal(
            workspace=workspace, values={}, selected_schema="sel", bundle=Path("bundle")
        )
    )


def test_schema_refusal_runs_checks_in_order_and_accepts_unchanged_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # existing-before, missing-before(empty schema rows), selected-after, missing-after
    snaps: list[object] = [("b",), ((), "m"), ("b",), ((), "m")]
    order = _patch_orchestration(monkeypatch, snapshots=snaps)
    _run_refusal()
    assert order == [
        "source",
        "constructor",
        "existing",
        "good",
        "snapshot:missing",
        "refusal",
        "snapshot:sel",
        "snapshot:missing",
    ]


def test_schema_refusal_rejects_preexisting_probe_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_orchestration(monkeypatch, snapshots=[("b",), (("present",), "m")])
    with pytest.raises(RuntimeError, match="missing_schema_probe_not_isolated"):
        _run_refusal()


@pytest.mark.parametrize(
    "snaps",
    [
        [("b",), ((), "m"), ("changed",), ((), "m")],
        [("b",), ((), "m"), ("b",), ((), "changed")],
    ],
    ids=["selected-changed", "missing-changed"],
)
def test_schema_refusal_rejects_metadata_drift(
    monkeypatch: pytest.MonkeyPatch, snaps: list[object]
) -> None:
    _patch_orchestration(monkeypatch, snapshots=list(snaps))
    with pytest.raises(RuntimeError, match="missing_schema_changed_role_or_schema_metadata"):
        _run_refusal()
