"""R10 crash/recovery/idempotency matrix for the canonical Team pipeline.

The tests in this file deliberately exercise the existing injectable seams at
the durable boundaries.  A fault is injected after a real SQLite-backed write
has committed, the database is reopened as a new worker would do, and the
recovery path is then driven to a terminal assertion.

There is no production-wide failpoint for Team execution today.  These tests
therefore use the already injectable coordinator, governance, and tool
registry collaborators rather than adding a test-only production hook.  A
crash is represented by an exception followed by a database restart; no timing
or sleep is involved.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from coworker.teams import (
    ActorKind,
    ActorRef,
    ApprovalStatus,
    ConversationRepository,
    DeliveryStatus,
    GovernanceRepository,
    ResourceRef,
    TeamDatabase,
    TeamPermissionService,
    ToolApprovalRequired,
    ToolExecutionReconciliationRequired,
    ToolGateway,
    WorkCoordinator,
    WorkRepository,
)
from test_team_orchestrator import _stack


NOW = "2026-08-29T12:00:00+00:00"
AFTER_LEASE = "2026-08-29T12:00:02+00:00"
AFTER_RETRY = "2026-08-29T12:00:03+00:00"
OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")


def _new_coordinator(database: TeamDatabase) -> WorkCoordinator:
    """Build the same low-level coordinator after a simulated worker restart."""

    return WorkCoordinator(
        WorkRepository(database),
        ConversationRepository(database),
        TeamPermissionService(),
    )


def _submit(coordinator: WorkCoordinator, context: dict[str, Any], key: str):
    return coordinator.submit_automation(
        request_id=f"request_{key}",
        workspace_id="workspace_1",
        automation_id=f"automation_{key}",
        requester=OWNER,
        target_agent_ids=("agent_b",),
        instruction=f"Exercise the {key} recovery path.",
        idempotency_key=f"idempotency_{key}",
        correlation_id=f"correlation_{key}",
        context_by_agent=context,
    )


def _start_run(
    tmp_path: Path,
    *,
    key: str,
    run_id: str,
    lease_seconds: int = 60,
):
    database, work, coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    submission = _submit(coordinator, context, key)
    lease = coordinator.claim_next(
        "agent_b",
        f"worker_{key}",
        now=NOW,
        lease_seconds=lease_seconds,
    )
    assert lease is not None
    coordinator.start_delivery(
        lease,
        run_id=run_id,
        agent_revision_id="revision_b",
        sandbox_id="sandbox_b",
        now=NOW,
    )
    return database, work, coordinator, submission, lease


class _SideEffectTool:
    """A deterministic tool whose external effect survives database restart."""

    def __init__(self, effect_log: Path, *, requires_approval: bool = False) -> None:
        self.effect_log = effect_log
        self.calls = 0
        self.metadata = SimpleNamespace(
            requires_approval=requires_approval,
            category="team",
            risk_level="high",
        )

    def get(self, _name: str):
        return self

    def names(self) -> list[str]:
        return ["write_once"]

    def schemas(self) -> list[dict[str, Any]]:
        return [{"name": "write_once"}]

    def execute(self, _name: str, _arguments: dict[str, Any]) -> dict[str, bool]:
        self.calls += 1
        with self.effect_log.open("a", encoding="utf-8") as stream:
            stream.write("effect\n")
        return {"ok": True}


def _gateway(
    registry: _SideEffectTool,
    coordinator: WorkCoordinator,
    *,
    run_id: str,
    request_id: str,
    governance: GovernanceRepository | None = None,
) -> ToolGateway:
    return ToolGateway(
        registry,
        coordinator=coordinator,
        run_id=run_id,
        agent_id="agent_b",
        actor_id="actor_b",
        requester_actor_id="actor_owner",
        governance=governance,
        workspace_id="workspace_1" if governance is not None else None,
        request_id=request_id if governance is not None else None,
        approval_policy={"durable_approval": governance is not None},
    )


def _effect_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def test_delivery_crash_after_claim_is_recovered_and_reclaimed_once(tmp_path):
    """A worker dying before dispatch leaves no phantom attempt or duplicate delivery."""

    database, work, coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    submission = _submit(coordinator, context, "delivery_claim_crash")
    delivery_id = submission.deliveries[0]["delivery_id"]
    lease = coordinator.claim_next(
        "agent_b", "worker_before_dispatch", now=NOW, lease_seconds=1
    )
    assert lease is not None
    assert work.get_delivery(delivery_id)["status"] == DeliveryStatus.LEASED.value
    assert work.get_delivery(delivery_id)["attempt_count"] == 0

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        recovered_work = WorkRepository(recovered_database)
        assert recovered_work.recover_expired_leases(now=AFTER_LEASE) == [delivery_id]
        recovered_delivery = recovered_work.get_delivery(delivery_id)
        assert recovered_delivery is not None
        assert recovered_delivery["status"] == DeliveryStatus.PENDING.value
        assert recovered_delivery["attempt_count"] == 0
        assert recovered_delivery["lease"] is None

        restarted = _new_coordinator(recovered_database)
        replay_lease = restarted.claim_next(
            "agent_b", "worker_after_restart", now=AFTER_LEASE
        )
        assert replay_lease is not None
        assert replay_lease["delivery_id"] == delivery_id
        assert recovered_work.list_runs_for_request(submission.request["request_id"]) == []
    finally:
        recovered_database.close()


def test_delivery_creation_crash_replays_request_and_delivery_idempotently(
    tmp_path, monkeypatch
):
    """A crash after delivery commit does not create a second logical delivery."""

    database, work, coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    original_create_deliveries = work.create_deliveries

    def commit_then_crash(request_id: str):
        created = original_create_deliveries(request_id)
        raise RuntimeError("injected crash after delivery commit")

    monkeypatch.setattr(work, "create_deliveries", commit_then_crash)
    with pytest.raises(RuntimeError, match="after delivery commit"):
        _submit(coordinator, context, "delivery_commit_crash")

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        restarted = _new_coordinator(recovered_database)
        replay = _submit(restarted, context, "delivery_commit_crash")
        request_id = replay.request["request_id"]
        assert request_id == "request_delivery_commit_crash"
        assert len(restarted.work.list_deliveries_for_request(request_id)) == 1
        assert len(restarted.work.list_requests("workspace_1")["requests"]) == 1
        assert replay.deliveries[0]["idempotency_key"] == (
            f"{request_id}:agent_b"
        )
    finally:
        recovered_database.close()


def test_run_dispatch_crash_is_recovered_to_one_retry_and_terminal_success(
    tmp_path, monkeypatch
):
    """A crash between dispatch and RUNNING is timed out, retried, and settled once."""

    database, work, coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    submission = _submit(coordinator, context, "run_dispatch_crash")
    delivery_id = submission.deliveries[0]["delivery_id"]
    lease = coordinator.claim_next(
        "agent_b", "worker_dispatch_crash", now=NOW, lease_seconds=1
    )
    assert lease is not None

    original_transition_run = work.transition_run

    def crash_before_running(*args, **kwargs):
        raise RuntimeError("injected crash before run transition")

    monkeypatch.setattr(work, "transition_run", crash_before_running)
    with pytest.raises(RuntimeError, match="before run transition"):
        coordinator.start_delivery(
            lease,
            run_id="run_dispatch_crash_1",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
            now=NOW,
        )
    monkeypatch.setattr(work, "transition_run", original_transition_run)
    assert work.get_run("run_dispatch_crash_1")["status"] == "queued"
    assert work.get_delivery(delivery_id)["status"] == DeliveryStatus.DISPATCHED.value

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        recovered_work = WorkRepository(recovered_database)
        assert recovered_work.recover_expired_leases(now=AFTER_LEASE) == [delivery_id]
        assert recovered_work.get_run("run_dispatch_crash_1")["status"] == "timed_out"
        assert recovered_work.get_delivery(delivery_id)["status"] == DeliveryStatus.RETRY_WAIT.value

        restarted = _new_coordinator(recovered_database)
        retry_lease = restarted.claim_next("agent_b", "worker_retry", now=AFTER_LEASE)
        assert retry_lease is not None
        restarted.start_delivery(
            retry_lease,
            run_id="run_dispatch_crash_2",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
            now=AFTER_LEASE,
        )
        settled = restarted.settle_run(
            "run_dispatch_crash_2",
            "succeeded",
            lease_token=retry_lease["lease_token"],
            now=AFTER_RETRY,
        )
        assert settled["delivery"]["status"] == DeliveryStatus.ACKNOWLEDGED.value
        assert settled["request"]["status"] == "completed"
        runs = restarted.work.list_runs_for_request(submission.request["request_id"])
        assert [(run["run_id"], run["status"]) for run in runs] == [
            ("run_dispatch_crash_1", "timed_out"),
            ("run_dispatch_crash_2", "succeeded"),
        ]
        attempts = recovered_work.get_delivery(delivery_id)["attempts"]
        assert [attempt["outcome"] for attempt in attempts] == ["timed_out", "accepted"]
    finally:
        recovered_database.close()


def test_tool_crash_after_start_marker_is_reconciled_without_side_effect(
    tmp_path, monkeypatch
):
    """A durable start marker makes an uncertain call non-replayable after restart."""

    database, work, coordinator, submission, _lease = _start_run(
        tmp_path, key="tool_start_crash", run_id="run_tool_start_crash"
    )
    effect_log = tmp_path / "tool-effects.log"
    registry = _SideEffectTool(effect_log)
    original_record = coordinator.record_tool_execution_event

    def persist_start_then_crash(**kwargs):
        persisted = original_record(**kwargs)
        if kwargs["event_type"] == "tool.execution.started":
            raise RuntimeError("injected crash after tool start marker")
        return persisted

    monkeypatch.setattr(coordinator, "record_tool_execution_event", persist_start_then_crash)
    gateway = _gateway(
        registry,
        coordinator,
        run_id="run_tool_start_crash",
        request_id=submission.request["request_id"],
    )
    with pytest.raises(RuntimeError, match="after tool start marker"):
        gateway.execute_with_context("tool-call-1", "write_once", {"path": "x"})
    assert _effect_count(effect_log) == 0

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        restarted = _new_coordinator(recovered_database)
        restarted_registry = _SideEffectTool(effect_log)
        retry_gateway = _gateway(
            restarted_registry,
            restarted,
            run_id="run_tool_start_crash",
            request_id=submission.request["request_id"],
        )
        with pytest.raises(ToolExecutionReconciliationRequired, match="may have started"):
            retry_gateway.execute_with_context("tool-call-1", "write_once", {"path": "x"})
        assert restarted_registry.calls == 0
        assert _effect_count(effect_log) == 0
        events = restarted.work.list_run_events("run_tool_start_crash")
        assert [event["event_type"] for event in events].count("tool.execution.started") == 1
        assert [event["event_type"] for event in events].count("tool.execution.finished") == 0
    finally:
        recovered_database.close()


def test_tool_crash_after_side_effect_before_terminal_marker_is_not_replayed(
    tmp_path, monkeypatch
):
    """An effect completed before a terminal marker remains exactly-once unknown."""

    database, work, coordinator, submission, _lease = _start_run(
        tmp_path, key="tool_finish_crash", run_id="run_tool_finish_crash"
    )
    effect_log = tmp_path / "tool-effects.log"
    registry = _SideEffectTool(effect_log)
    original_record = coordinator.record_tool_execution_event

    def crash_before_finished_marker(**kwargs):
        if kwargs["event_type"] == "tool.execution.finished":
            raise RuntimeError("injected crash before tool terminal marker")
        return original_record(**kwargs)

    monkeypatch.setattr(coordinator, "record_tool_execution_event", crash_before_finished_marker)
    gateway = _gateway(
        registry,
        coordinator,
        run_id="run_tool_finish_crash",
        request_id=submission.request["request_id"],
    )
    with pytest.raises(ToolExecutionReconciliationRequired, match="could not be durably recorded"):
        gateway.execute_with_context("tool-call-2", "write_once", {"path": "x"})
    assert registry.calls == 1
    assert _effect_count(effect_log) == 1

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        restarted = _new_coordinator(recovered_database)
        restarted_registry = _SideEffectTool(effect_log)
        retry_gateway = _gateway(
            restarted_registry,
            restarted,
            run_id="run_tool_finish_crash",
            request_id=submission.request["request_id"],
        )
        with pytest.raises(ToolExecutionReconciliationRequired, match="may have started"):
            retry_gateway.execute_with_context("tool-call-2", "write_once", {"path": "x"})
        assert restarted_registry.calls == 0
        assert _effect_count(effect_log) == 1
        events = restarted.work.list_run_events("run_tool_finish_crash")
        assert [event["event_type"] for event in events].count("tool.execution.started") == 1
        assert [event["event_type"] for event in events].count("tool.execution.finished") == 0
    finally:
        recovered_database.close()


def test_approval_creation_crash_reuses_one_pending_approval_after_restart(
    tmp_path, monkeypatch
):
    """A committed approval survives a worker crash and cannot run the tool while pending."""

    database, work, coordinator, submission, _lease = _start_run(
        tmp_path, key="approval_create_crash", run_id="run_approval_create_crash"
    )
    effect_log = tmp_path / "approval-effects.log"
    registry = _SideEffectTool(effect_log, requires_approval=True)
    governance = GovernanceRepository(database)
    original_create = governance.create_approval

    def commit_approval_then_crash(*args, **kwargs):
        created = original_create(*args, **kwargs)
        raise RuntimeError("injected crash after approval commit")

    monkeypatch.setattr(governance, "create_approval", commit_approval_then_crash)
    gateway = _gateway(
        registry,
        coordinator,
        run_id="run_approval_create_crash",
        request_id=submission.request["request_id"],
        governance=governance,
    )
    with pytest.raises(RuntimeError, match="after approval commit"):
        gateway.execute_with_context("approval-call-1", "write_once", {"path": "x"})
    approvals = governance.list_approvals("workspace_1", "actor_owner")
    assert len(approvals) == 1
    assert approvals[0]["status"] == ApprovalStatus.PENDING.value
    assert registry.calls == 0

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        restarted = _new_coordinator(recovered_database)
        restarted_governance = GovernanceRepository(recovered_database)
        restarted_registry = _SideEffectTool(effect_log, requires_approval=True)
        retry_gateway = _gateway(
            restarted_registry,
            restarted,
            run_id="run_approval_create_crash",
            request_id=submission.request["request_id"],
            governance=restarted_governance,
        )
        with pytest.raises(ToolApprovalRequired) as replay:
            retry_gateway.execute_with_context(
                "approval-call-1", "write_once", {"path": "x"}
            )
        assert replay.value.approval_id == approvals[0]["approval_id"]
        assert restarted_registry.calls == 0
        assert len(restarted_governance.list_approvals("workspace_1", "actor_owner")) == 1
    finally:
        recovered_database.close()


def test_approval_decision_crash_replays_approved_call_once_and_finishes_run(
    tmp_path, monkeypatch
):
    """A decision committed before a crash is consumed once by the restarted worker."""

    database, work, coordinator, submission, lease = _start_run(
        tmp_path, key="approval_decision_crash", run_id="run_approval_decision_crash"
    )
    effect_log = tmp_path / "approval-effects.log"
    registry = _SideEffectTool(effect_log, requires_approval=True)
    governance = GovernanceRepository(database)
    gateway = _gateway(
        registry,
        coordinator,
        run_id="run_approval_decision_crash",
        request_id=submission.request["request_id"],
        governance=governance,
    )
    with pytest.raises(ToolApprovalRequired) as pending:
        gateway.execute_with_context("approval-call-2", "write_once", {"path": "x"})
    approval_id = pending.value.approval_id

    original_decide = governance.decide_approval

    def commit_decision_then_crash(*args, **kwargs):
        decided = original_decide(*args, **kwargs)
        raise RuntimeError("injected crash after approval decision")

    monkeypatch.setattr(governance, "decide_approval", commit_decision_then_crash)
    with pytest.raises(RuntimeError, match="after approval decision"):
        governance.decide_approval(
            approval_id,
            ApprovalStatus.APPROVED,
            approver_actor_id="actor_owner",
        )
    assert governance.get_approval(approval_id)["status"] == ApprovalStatus.APPROVED.value
    assert registry.calls == 0

    database.close()
    recovered_database = TeamDatabase(tmp_path / "team.db")
    try:
        restarted = _new_coordinator(recovered_database)
        restarted_governance = GovernanceRepository(recovered_database)
        restarted_registry = _SideEffectTool(effect_log, requires_approval=True)
        retry_gateway = _gateway(
            restarted_registry,
            restarted,
            run_id="run_approval_decision_crash",
            request_id=submission.request["request_id"],
            governance=restarted_governance,
        )
        assert retry_gateway.execute_with_context(
            "approval-call-2", "write_once", {"path": "x"}
        ) == {"ok": True}
        assert restarted_registry.calls == 1
        assert _effect_count(effect_log) == 1
        assert len(restarted_governance.list_approvals("workspace_1", "actor_owner")) == 1

        settled = restarted.settle_run(
            "run_approval_decision_crash",
            "succeeded",
            lease_token=lease["lease_token"],
            now=AFTER_RETRY,
        )
        assert settled["delivery"]["status"] == DeliveryStatus.ACKNOWLEDGED.value
        assert settled["request"]["status"] == "completed"
    finally:
        recovered_database.close()


def test_unsupported_approval_park_crash_is_recorded_without_claiming_recovery():
    """The current hooks cannot inject between ToolApprovalRequired and parking.

    Approval creation/decision recovery is covered above.  There is no exposed
    hook for a crash in the worker's exception handler after the durable
    approval signal but before ``park_run_for_approval``.  Keep that gap
    explicit instead of changing production solely to manufacture a failpoint.
    """

    pytest.skip(
        "unsupported by current hooks: no injectable crash point between "
        "ToolApprovalRequired and park_run_for_approval"
    )
