"""Source-bound observed test outcomes; planned inventories never count as passes."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET
from public_test_ids import validate_public_test_id
from web_artifacts import inventory


def _validated_group_nodes(groups: object) -> list[str]:
    if not isinstance(groups, list) or not groups:
        raise ValueError("python_evidence_group_invalid")
    grouped: list[str] = []
    for index, group in enumerate(groups):
        if (
            not isinstance(group, dict)
            or set(group) != {"index", "status", "node_ids", "outcome_count", "returncode"}
            or group.get("status") != "passed"
            or type(group.get("returncode")) is not int
            or group.get("returncode") != 0
            or type(group.get("index")) is not int
            or group.get("index") != index
            or not isinstance(group.get("node_ids"), list)
            or not group.get("node_ids")
            or len(group["node_ids"]) > 32
            or any(not isinstance(node, str) for node in group["node_ids"])
            or sum(len(node.encode("utf-8")) + 1 for node in group["node_ids"]) > 64 * 1024
            or type(group.get("outcome_count")) is not int
            or group.get("outcome_count") != len(group["node_ids"])
        ):
            raise ValueError("python_evidence_group_invalid")
        group_nodes = group["node_ids"]
        if len(set(group_nodes)) != len(group_nodes):
            raise ValueError("python_evidence_group_invalid")
        for node in group_nodes:
            validate_public_test_id(node)
        native = [
            node.startswith(("tests/test_embedded_", "tests/test_postgres_"))
            for node in group_nodes
        ]
        expensive = any(
            node.startswith(
                ("tests/test_client_benchmark.py::", "tests/test_postgres_resource_shutdown.py::")
            )
            for node in group_nodes
        )
        if (
            len(set(native)) > 1
            or (native[0] and len(group_nodes) > 8)
            or (expensive and len(group_nodes) != 1)
        ):
            raise ValueError("python_evidence_group_policy_invalid")
        grouped.extend(group_nodes)
    return grouped


def junit_results(path: Path, required: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError("missing_observed_test_report")
    lookup = {}
    for node in required:
        file, *parts = node.split("::")
        key = (".".join([file.removesuffix(".py").replace("/", "."), *parts[:-1]]), parts[-1])
        if key in lookup:
            raise ValueError("ambiguous_observed_test_identity")
        lookup[key] = node
    return junit_named_results(path, lookup)


def _junit_case_key(case: Element) -> tuple[str, str]:
    classname = case.get("classname")
    name = case.get("name")
    if not isinstance(classname, str) or not isinstance(name, str):
        raise ValueError("unknown_or_duplicate_observed_test")
    return classname, name


def _junit_case_outcome(case: Element) -> str:
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


def junit_named_results(path: Path, lookup: dict[tuple[str, str], str]) -> list[dict[str, str]]:
    """Validate exact JUnit identities supplied by a producer-specific allowlist."""
    if not path.is_file():
        raise ValueError("missing_observed_test_report")
    if not lookup or len(set(lookup.values())) != len(lookup):
        raise ValueError("ambiguous_observed_test_identity")
    results: dict[str, str] = {}
    seen = set()
    try:
        document = ET.parse(path)
    except ET.ParseError:
        raise ValueError("invalid_observed_test_report") from None
    root = document.getroot()
    if root is None or root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("invalid_observed_test_report")
    for case in document.iter("testcase"):
        key = _junit_case_key(case)
        if key not in lookup or key in seen:
            raise ValueError("unknown_or_duplicate_observed_test")
        seen.add(key)
        results[lookup[key]] = _junit_case_outcome(case)
    if seen != set(lookup):
        raise ValueError("required_test_outcomes_missing")
    return [{"node_id": node, "outcome": results[node]} for node in lookup.values()]


@dataclass
class ObservedEvidence:
    source_sha256: dict[str, str]
    inventory: list[str] = field(default_factory=list)
    results: list[dict[str, str]] = field(default_factory=list)
    groups: list[dict[str, object]] = field(default_factory=list)
    inventory_complete: bool = False
    failure_reason: str | None = None
    failure_phase: str | None = None
    coverage_returncode: int | None = None
    qualified: bool = False

    def record_group(
        self,
        node_ids: list[str],
        status: str,
        outcomes: list[dict[str, str]],
        returncode: int | None,
        reason: str | None = None,
    ) -> None:
        group: dict[str, object] = {
            "index": len(self.groups),
            "status": status,
            "node_ids": node_ids,
            "outcome_count": len(outcomes),
        }
        if returncode is not None:
            group["returncode"] = returncode
        if reason is not None:
            group["reason"] = reason
        self.groups.append(group)
        self.results.extend(outcomes)

    def write(self, root: Path, code: int | None, destination: Path | None = None) -> Path:
        try:
            stable = self.source_sha256 == inventory(root)
        except Exception:
            stable = False
        all_results = [entry.get("node_id") for entry in self.results]
        try:
            grouped_nodes = _validated_group_nodes(self.groups)
        except ValueError:
            grouped_nodes = []
        all_passed = (
            self.inventory_complete
            and bool(self.inventory)
            and all_results == self.inventory
            and all(entry.get("outcome") == "passed" for entry in self.results)
            and grouped_nodes == self.inventory
        )
        self.qualified = bool(
            type(code) is int
            and code == 0
            and type(self.coverage_returncode) is int
            and self.coverage_returncode == 0
            and stable
            and all_passed
            and self.failure_reason is None
        )
        if self.qualified:
            status = "passed"
        elif self.failure_reason or not stable or not self.inventory_complete or not all_results:
            status = "incomplete"
        else:
            status = "failed"
        if destination is None:
            with tempfile.NamedTemporaryFile(
                prefix="tether-python-evidence-", suffix=".json", delete=False
            ) as file:
                destination = Path(file.name)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        pending = (
            [node for node in self.inventory if node not in set(all_results)]
            if self.inventory_complete
            else None
        )
        receipt = {
            "schema_version": 2,
            "status": status,
            "command": "make check / bounded Python aggregate",
            "recorded_at": datetime.now(UTC).isoformat(),
            "python": sys.version,
            "profile": "local-deterministic",
            "source_sha256": self.source_sha256,
            "source_stable": stable,
            "inventory_complete": self.inventory_complete,
            "inventory": self.inventory,
            "pending_nodes": pending,
            "groups": self.groups,
            "test_results": self.results,
            "coverage_returncode": self.coverage_returncode,
            "limits": ["Python aggregate only; no browser, installed or live-platform inference"],
        }
        if code is not None and status != "incomplete":
            receipt["exit_code"] = code
        if self.failure_reason is not None:
            receipt["incomplete_reason"] = self.failure_reason
            receipt["incomplete_phase"] = self.failure_phase
        destination.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        os.chmod(destination, 0o600)
        return destination


def validate_python_receipt(value: object, source: dict[str, str]) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
    ):
        raise ValueError("python_evidence_schema_invalid")
    expected_fields = {
        "schema_version",
        "status",
        "command",
        "recorded_at",
        "python",
        "profile",
        "source_sha256",
        "source_stable",
        "inventory_complete",
        "inventory",
        "pending_nodes",
        "groups",
        "test_results",
        "coverage_returncode",
        "limits",
        "exit_code",
    }
    if set(value) != expected_fields:
        raise ValueError("python_evidence_schema_invalid")
    nodes = value.get("inventory")
    groups = value.get("groups")
    outcomes = value.get("test_results")
    if (
        value.get("status") != "passed"
        or value.get("command") != "make check / bounded Python aggregate"
        or not isinstance(value.get("recorded_at"), str)
        or not value.get("recorded_at")
        or not isinstance(value.get("python"), str)
        or not value.get("python")
        or value.get("profile") != "local-deterministic"
        or value.get("limits")
        != ["Python aggregate only; no browser, installed or live-platform inference"]
        or value.get("source_stable") is not True
        or value.get("source_sha256") != source
        or value.get("inventory_complete") is not True
        or not isinstance(nodes, list)
        or not nodes
        or any(not isinstance(node, str) or not node for node in nodes)
        or len(set(nodes)) != len(nodes)
        or not isinstance(groups, list)
        or not groups
        or not isinstance(outcomes, list)
    ):
        raise ValueError("python_evidence_not_complete")
    for node in nodes:
        validate_public_test_id(node)
    grouped = _validated_group_nodes(groups)
    observed: list[str] = []
    for item in outcomes:
        if (
            not isinstance(item, dict)
            or set(item) != {"node_id", "outcome"}
            or not isinstance(item.get("node_id"), str)
            or item.get("outcome") != "passed"
        ):
            raise ValueError("python_evidence_outcome_invalid")
        observed.append(item["node_id"])
    if (
        grouped != nodes
        or observed != nodes
        or value.get("pending_nodes") != []
        or type(value.get("coverage_returncode")) is not int
        or value.get("coverage_returncode") != 0
        or type(value.get("exit_code")) is not int
        or value.get("exit_code") != 0
    ):
        raise ValueError("python_evidence_outcomes_incomplete")
    return value
