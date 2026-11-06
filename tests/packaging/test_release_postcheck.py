"""An unchanged aggregate can be reused only with every required observed stage."""

import asyncio
import hashlib
import json

import pytest
import web_gate_receipt as aggregate_receipts
from process import Result
from test_evidence import ObservedEvidence
from web_artifacts import inventory
from web_gate_receipt import LIMITS, STAGES, verify_aggregate


def aggregate(root):
    (root / "package.json").write_text(json.dumps({"packageManager": "npm@11.20.0"}))
    sources = inventory(root)
    node = "tests/test_fake.py::test_complete"
    python_path = root / "python-evidence.json"
    observed = ObservedEvidence(sources)
    observed.inventory = [node]
    observed.inventory_complete = True
    observed.record_group([node], "passed", [{"node_id": node, "outcome": "passed"}], 0)
    observed.coverage_returncode = 0
    observed.write(root, 0, python_path)
    raw_python = python_path.read_bytes()
    python_receipt = json.loads(raw_python)
    value = {
        "status": "passed",
        "schema_version": 1,
        "source_sha256": sources,
        "stages": list(STAGES),
        "recorded_at": "fabricated-unit-fixture",
        "command": "make web-check / complete deterministic core and web aggregate",
        "versions": {"node": "v22.22.2", "npm": "11.20.0", "python": "3.11.17"},
        "python_quality": {
            "receipt_sha256": hashlib.sha256(raw_python).hexdigest(),
            "receipt_json": raw_python.decode("utf-8"),
            "status": "passed",
            "source_sha256": sources,
            "inventory_sha256": hashlib.sha256(
                json.dumps(python_receipt["inventory"], separators=(",", ":")).encode()
            ).hexdigest(),
            "outcomes_sha256": hashlib.sha256(
                json.dumps(python_receipt["test_results"], separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "test_results": [
            {"node_id": "scripts/web_quality.py::" + stage, "outcome": "passed"} for stage in STAGES
        ],
        "limits": list(LIMITS),
    }
    return value


def write(root, value):
    path = root / "observed.json"
    path.write_text(json.dumps(value))
    return path


def test_current_complete_aggregate_verifies_without_running_children(tmp_path):
    value = aggregate(tmp_path)
    assert verify_aggregate(tmp_path, write(tmp_path, value))["status"] == "passed"
    (tmp_path / "README.md").write_text("changed source input")
    with pytest.raises(ValueError, match="failed_or_stale"):
        verify_aggregate(tmp_path, tmp_path / "observed.json")


@pytest.mark.parametrize(
    "case", ["missing-stage", "failed-case", "unqualified-node", "old-node22", "wrong-npm"]
)
def test_incomplete_or_different_runtime_receipt_cannot_replace_aggregate(tmp_path, case):
    value = aggregate(tmp_path)
    if case == "missing-stage":
        value["stages"].pop()
    elif case == "failed-case":
        value["test_results"][0]["outcome"] = "failed"
    elif case == "unqualified-node":
        value["versions"]["node"] = "v26.12.1"
    elif case == "old-node22":
        value["versions"]["node"] = "v22.20.0"
    else:
        value["versions"]["npm"] = "10.0.0"
    with pytest.raises(ValueError, match="aggregate_"):
        verify_aggregate(tmp_path, write(tmp_path, value))


def test_python_receipt_bytes_must_match_aggregate_binding(tmp_path):
    value = aggregate(tmp_path)
    value["python_quality"]["receipt_json"] = "{}"
    with pytest.raises(ValueError, match="aggregate_python_evidence_hash_mismatch"):
        verify_aggregate(tmp_path, write(tmp_path, value))


def test_aggregate_producer_embeds_exact_python_receipt_bytes(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text(json.dumps({"packageManager": "npm@11.20.0"}))
    source = inventory(tmp_path)
    node = "tests/test_fake.py::test_complete"
    python_path = tmp_path / "python-evidence.json"
    observed = ObservedEvidence(source)
    observed.inventory = [node]
    observed.inventory_complete = True
    observed.record_group([node], "passed", [{"node_id": node, "outcome": "passed"}], 0)
    observed.coverage_returncode = 0
    observed.write(tmp_path, 0, python_path)
    raw = python_path.read_bytes()
    aggregate_path = tmp_path / "aggregate.json"
    monkeypatch.setattr(aggregate_receipts, "AGGREGATE", aggregate_path)
    monkeypatch.setattr(
        aggregate_receipts.shutil,
        "which",
        lambda name: "/usr/bin/" + name,
    )

    async def version(arguments, cwd):
        return Result(0, "v22.22.2\n" if arguments[0].endswith("node") else "11.20.0\n", "")

    monkeypatch.setattr(aggregate_receipts, "run_command", version)
    asyncio.run(aggregate_receipts.record_aggregate(tmp_path, source, python_path))
    value = verify_aggregate(tmp_path, aggregate_path)
    assert value["python_quality"]["receipt_json"].encode() == raw
