"""Tests for durable Team tool execution markers and replay safety."""

import json
from types import SimpleNamespace

import pytest

from coworker.teams import (
    ResourceRef,
    ToolApprovalRequired,
    ToolExecutionReconciliationRequired,
    ToolGateway,
    ToolGatewayError,
)


class _Work:
    def __init__(self) -> None:
        self.events = {}

    def get_run_event(self, event_id):
        return self.events.get(event_id)


class _Coordinator:
    def __init__(self) -> None:
        self.work = _Work()
        self.recorded = []

    def record_tool_execution_event(self, **kwargs):
        self.recorded.append(kwargs)
        self.work.events[kwargs["event_id"]] = {
            "run_event_id": kwargs["event_id"],
            "event_type": kwargs["event_type"],
            "payload": dict(kwargs["payload"]),
        }
        return self.work.events[kwargs["event_id"]]


class _Registry:
    def __init__(self, *, fail=False) -> None:
        self.fail = fail
        self.calls = []

    def get(self, name):
        return name

    def names(self):
        return ["write_file"]

    def schemas(self):
        return [{"name": "write_file"}]

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.fail:
            raise RuntimeError("api_token=abcdefghijklmnopqrstuvwxyz1234")
        return {"ok": True}


class _ApprovalGovernance:
    def __init__(self) -> None:
        self.approvals = {}

    def get_approval(self, approval_id):
        return self.approvals.get(approval_id)

    def approval_is_valid(self, approval_id):
        return self.approvals[approval_id]["status"] == "approved"

    def create_approval(self, approval_id, workspace_id, requester, action, resource, reason, **kwargs):
        self.approvals[approval_id] = {
            "approval_id": approval_id,
            "workspace_id": workspace_id,
            "requested_by_actor_id": requester,
            "action_kind": action,
            "resource": resource,
            "reason": reason,
            "status": "pending",
            **kwargs,
        }
        return self.approvals[approval_id]


def _gateway(registry, coordinator, *, run_id="run_1"):
    return ToolGateway(
        registry,
        coordinator=coordinator,
        run_id=run_id,
        agent_id="agent_1",
        actor_id="actor_agent",
        requester_actor_id="actor_owner",
    )


def test_gateway_records_start_and_finish_and_blocks_duplicate_side_effect():
    coordinator = _Coordinator()
    registry = _Registry()
    gateway = _gateway(registry, coordinator)

    with pytest.raises(ToolGatewayError, match="stable tool call id"):
        gateway.execute("write_file", {"path": "a.txt"})

    assert gateway.execute_with_context(
        "call_1", "write_file", {"path": "a.txt", "api_key": "secret-value"}
    ) == {"ok": True}
    assert [item["event_type"] for item in coordinator.recorded] == [
        "tool.execution.started",
        "tool.execution.finished",
    ]
    assert registry.calls == [
        ("write_file", {"path": "a.txt", "api_key": "secret-value"})
    ]
    audit = json.dumps(coordinator.recorded, sort_keys=True)
    assert "secret-value" not in audit

    with pytest.raises(ToolExecutionReconciliationRequired, match="terminal"):
        gateway.execute_with_context(
            "call_1", "write_file", {"path": "a.txt", "api_key": "secret-value"}
        )
    assert len(registry.calls) == 1

    other_run = _gateway(registry, coordinator, run_id="run_2")
    assert other_run.execute_with_context(
        "call_1", "write_file", {"path": "a.txt", "api_key": "secret-value"}
    ) == {"ok": True}
    assert len(registry.calls) == 2


def test_gateway_redacts_error_and_records_failed_terminal_marker():
    coordinator = _Coordinator()
    gateway = _gateway(_Registry(fail=True), coordinator)

    with pytest.raises(RuntimeError, match="api_token"):
        gateway.execute_with_context("call_2", "write_file", {"path": "a.txt"})

    finished = coordinator.recorded[-1]
    assert finished["event_type"] == "tool.execution.finished"
    assert finished["payload"]["status"] == "failed"
    assert finished["payload"]["error"] == "[REDACTED]"


def test_gateway_creates_durable_approval_before_a_consequential_tool():
    coordinator = _Coordinator()
    governance = _ApprovalGovernance()
    registry = _Registry()
    registry.get = lambda _name: SimpleNamespace(
        metadata=SimpleNamespace(requires_approval=True)
    )
    gateway = ToolGateway(
        registry,
        coordinator=coordinator,
        run_id="run_approval",
        agent_id="agent_1",
        actor_id="actor_agent",
        requester_actor_id="actor_owner",
        governance=governance,
        workspace_id="workspace_1",
        request_id="request_1",
        approval_policy={"durable_approval": True},
    )

    with pytest.raises(ToolApprovalRequired) as raised:
        gateway.execute_with_context("call_approval", "write_file", {"path": "a.txt"})
    approval_id = raised.value.approval_id
    assert governance.approvals[approval_id]["status"] == "pending"
    assert registry.calls == []
    assert "a.txt" not in governance.approvals[approval_id]["reason"]

    governance.approvals[approval_id]["status"] = "approved"
    assert gateway.execute_with_context("call_approval", "write_file", {"path": "a.txt"}) == {
        "ok": True
    }
