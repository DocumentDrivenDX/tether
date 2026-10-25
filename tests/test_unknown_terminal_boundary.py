"""Unknown-terminal fallback never swallows physical observation or structural failures."""

import httpx2 as httpx
import pytest
from test_compatible_adapter import adapter, wire_response

from tether.models import complete
from tether.models.testing import ListAttemptRecorder
from tether.types import ModelUnavailable
from tether.types.execution import CommitUnknown

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("fault", ["observation", "bytes", "arguments", "projection"])
async def test_validated_unknown_fallback_requires_successful_boundary(
    limits, model_request, tool_spec, monkeypatch, fault
):
    from tether.adapters.pydantic_ai import compatible

    original = compatible.from_sdk_response

    def changed(response, usage=None):
        return original(response, usage).model_copy(update={"content": "Changed"})

    class Recorder(ListAttemptRecorder):
        async def observe(self, identity, receipt, **values):
            if fault == "observation" and values.get("wire_response") is not None:
                raise CommitUnknown("fabricated_observation_unknown")
            return await super().observe(identity, receipt, **values)

    recorder = Recorder(model_request.tags)
    request = model_request.model_copy(update={"attempts": recorder, "tools": (tool_spec,)})
    body = wire_response(finish=None if fault == "projection" else "future")
    if fault == "bytes":
        body["choices"][0]["message"]["content"] = "x" * limits.max_response_bytes
    if fault == "arguments":
        body["choices"][0]["message"]["tool_calls"] = [
            {"id": "call", "type": "function", "function": {"name": "lookup", "arguments": "{"}}
        ]
    monkeypatch.setattr(
        compatible, "from_sdk_response", changed if fault == "projection" else original
    )
    model = adapter(limits, lambda request: httpx.Response(200, json=body))
    try:
        with pytest.raises(CommitUnknown if fault == "observation" else ModelUnavailable):
            await complete(model, request)
        assert len(recorder.started) == 1
    finally:
        await model.aclose()
