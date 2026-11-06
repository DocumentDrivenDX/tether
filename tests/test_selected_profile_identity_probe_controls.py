"""Controls for bounded workspace identity convergence with the captured credential."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import selected_profiles_identity_probe as probe


def _install_responses(monkeypatch, responses: list[httpx.Response]) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses.pop(0)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        probe,
        "_client",
        lambda host: httpx.AsyncClient(
            base_url=host,
            timeout=httpx.Timeout(10),
            trust_env=False,
            verify=True,
            transport=transport,
        ),
    )
    return requests


def _response(status: int, body: object | None = None) -> httpx.Response:
    return httpx.Response(status, json=body) if body is not None else httpx.Response(status)


class _ManualDeadline:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, error_type: object, *_args: object) -> bool:
        if error_type is asyncio.CancelledError:
            raise TimeoutError
        return False


def _run(expected_active: bool) -> str:
    return asyncio.run(
        probe.wait_for_workspace_identity(
            "https://workspace.fabricated.example",
            " ".join(("Bearer", "fabricated-current-token")),
            "fabricated-principal",
            expected_active=expected_active,
        )
    )


def test_workspace_me_denial_is_inactive_and_uses_exact_captured_bearer(monkeypatch) -> None:
    requests = _install_responses(monkeypatch, [_response(403)])
    assert _run(False) == "refused"
    assert len(requests) == 1
    assert requests[0].url.path == probe._ME_PATH
    assert requests[0].headers["authorization"] == " ".join(("Bearer", "fabricated-current-token"))


def test_workspace_me_requires_exact_principal_and_false_active(monkeypatch) -> None:
    requests = _install_responses(
        monkeypatch,
        [_response(200, {"id": "fabricated-principal", "active": False})],
    )
    assert _run(False) == "inactive"
    assert len(requests) == 1


def test_workspace_me_rejects_different_principal(monkeypatch) -> None:
    _install_responses(
        monkeypatch,
        [_response(200, {"id": "fabricated-other-principal", "active": False})],
    )
    with pytest.raises(RuntimeError, match="identity_workspace_principal_mismatch"):
        _run(False)


def test_workspace_me_retries_until_expected_transition(monkeypatch) -> None:
    responses = [
        _response(200, {"id": "fabricated-principal", "active": True}),
        _response(200, {"id": "fabricated-principal", "active": False}),
    ]
    requests = _install_responses(monkeypatch, responses)
    monkeypatch.setattr(probe.asyncio, "sleep", _no_wait)
    assert _run(False) == "inactive"
    assert len(requests) == 2


async def _no_wait(_duration: float) -> None:
    return None


def test_workspace_me_active_restore_requires_bound_active_response(monkeypatch) -> None:
    _install_responses(
        monkeypatch,
        [_response(200, {"id": "fabricated-principal", "active": True})],
    )
    assert _run(True) == "active"


def test_workspace_access_retries_transient_protected_unauthorized(monkeypatch) -> None:
    requests = _install_responses(
        monkeypatch,
        [
            _response(200, {"id": "fabricated-principal", "active": True}),
            _response(200, {"id": "fabricated-principal", "active": True}),
        ],
    )
    monkeypatch.setattr(probe.asyncio, "sleep", _no_wait)
    statuses = iter((401, 200))
    calls = 0

    async def protected_status() -> int:
        nonlocal calls
        calls += 1
        return next(statuses)

    async def run() -> str:
        return await probe.wait_for_workspace_access(
            "https://workspace.fabricated.example",
            " ".join(("Bearer", "fabricated-fresh-token")),
            "fabricated-principal",
            protected_status,
        )

    assert asyncio.run(run()) == "active"
    assert calls == 2
    assert len(requests) == 2


def test_workspace_access_rejects_wrong_identity_before_protected_request(monkeypatch) -> None:
    requests = _install_responses(
        monkeypatch,
        [_response(200, {"id": "fabricated-other-principal", "active": True})],
    )
    protected_calls = 0

    async def protected_status() -> int:
        nonlocal protected_calls
        protected_calls += 1
        return 200

    async def run() -> None:
        with pytest.raises(RuntimeError, match="identity_workspace_principal_mismatch"):
            await probe.wait_for_workspace_access(
                "https://workspace.fabricated.example",
                " ".join(("Bearer", "fabricated-fresh-token")),
                "fabricated-principal",
                protected_status,
            )

    asyncio.run(run())
    assert protected_calls == 0
    assert len(requests) == 1


def test_workspace_me_refusal_does_not_prove_restored_identity(monkeypatch) -> None:
    _install_responses(monkeypatch, [_response(401)])
    monkeypatch.setattr(probe, "_DEADLINE_SECONDS", 0.0)
    with pytest.raises(RuntimeError, match="identity_workspace_transition_timeout"):
        _run(True)


def test_workspace_me_absolute_deadline_cancels_last_inflight_request(monkeypatch) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return _response(200, {"id": "fabricated-principal", "active": False})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        probe,
        "_client",
        lambda host: httpx.AsyncClient(
            base_url=host,
            timeout=httpx.Timeout(10),
            trust_env=False,
            verify=True,
            transport=transport,
        ),
    )
    monkeypatch.setattr(probe.asyncio, "timeout", lambda *_args, **_kwargs: _ManualDeadline())

    async def run() -> None:
        task = asyncio.create_task(
            probe.wait_for_workspace_identity(
                "https://workspace.fabricated.example",
                " ".join(("Bearer", "fabricated-current-token")),
                "fabricated-principal",
                expected_active=False,
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(RuntimeError, match="identity_workspace_transition_timeout"):
            await task
        assert cancelled.is_set()

    asyncio.run(run())


@pytest.mark.parametrize(
    "host",
    [
        "http://workspace.fabricated.example",
        "https://" + "user" + "@workspace.fabricated.example",
        "https://workspace.fabricated.example/path",
        "https://workspace.fabricated.example?query=1",
    ],
    ids=["cleartext", "userinfo", "non-origin-path", "query"],
)
def test_workspace_me_rejects_non_origin_host(monkeypatch, host: str) -> None:
    called = False

    def unused_client(_host: str) -> httpx.AsyncClient:
        nonlocal called
        called = True
        raise AssertionError("client should not be created")

    monkeypatch.setattr(probe, "_client", unused_client)
    with pytest.raises(RuntimeError, match="identity_workspace_host_invalid"):
        asyncio.run(
            probe.wait_for_workspace_identity(
                host,
                " ".join(("Bearer", "fabricated-current-token")),
                "fabricated-principal",
                expected_active=False,
            )
        )
    assert not called
