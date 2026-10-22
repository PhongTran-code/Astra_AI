"""Durable approval parking and same-run resume coverage."""

from test_team_orchestrator import _handoff, _stack

from coworker.teams import ApprovalStatus, ComputerRepository, GovernanceRepository, ResourceRef


def test_approved_tool_wait_resumes_the_same_run_and_delivery(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    governance = GovernanceRepository(database)
    try:
        orchestrator.submit_handoff(_handoff(), context)
        lease = coordinator.claim_next("agent_b", "worker_b")
        assert lease is not None
        coordinator.start_delivery(
            lease,
            run_id="run_approval",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
        )
        approval = governance.create_approval(
            "approval_tool",
            "workspace_1",
            "actor_owner",
            "tool.execute",
            ResourceRef("tool_execution", "tool:call"),
            "A consequential Team tool needs review",
            request_id=lease["request_id"],
            run_id="run_approval",
        )
        parked = coordinator.park_run_for_approval(
            "run_approval",
            approval["approval_id"],
            lease_token=lease["lease_token"],
        )
        assert parked["run"]["status"] == "waiting_approval"
        assert work.list_ready_deliveries("agent_b") == []

        governance.decide_approval(
            approval["approval_id"], ApprovalStatus.APPROVED, approver_actor_id="actor_owner"
        )
        resumed_lease = coordinator.claim_next("agent_b", "worker_b_resume")
        assert resumed_lease is not None
        assert resumed_lease["resume_run_id"] == "run_approval"
        started = coordinator.start_delivery(
            resumed_lease,
            run_id="run_approval",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
        )
        assert started["run"]["run_id"] == "run_approval"
        assert started["run"]["status"] == "running"
        assert len(work.list_runs_for_request(lease["request_id"])) == 1
    finally:
        database.close()


def test_approved_human_takeover_resumes_the_same_run_and_delivery(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    computers = ComputerRepository(database)
    try:
        orchestrator.submit_handoff(_handoff("handoff_takeover"), context)
        lease = coordinator.claim_next("agent_b", "worker_b")
        assert lease is not None
        coordinator.start_delivery(
            lease,
            run_id="run_takeover",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
        )
        computers.create_computer(
            "computer_b",
            "workspace_1",
            "agent_b",
            "fake_browser",
        )
        computers.set_status("computer_b", "ready")
        computers.create_session(
            "session_takeover",
            "computer_b",
            "workspace_1",
            "agent_b",
            "actor_owner",
            run_id="run_takeover",
            provider_session_ref="page-1",
        )
        takeover = computers.create_takeover(
            "takeover_1",
            "session_takeover",
            "workspace_1",
            "actor_b",
            run_id="run_takeover",
            reason="MFA requires a human",
        )
        parked = coordinator.park_run_for_takeover(
            "run_takeover",
            takeover["takeover_request_id"],
            lease_token=lease["lease_token"],
        )
        assert parked["run"]["status"] == "waiting_input"
        assert work.get_delivery(lease["delivery_id"])["status"] == "dispatched"
        assert work.list_ready_deliveries("agent_b") == []

        computers.decide_takeover(
            takeover["takeover_request_id"], "approved", actor_id="actor_owner"
        )
        resumed_lease = coordinator.claim_next("agent_b", "worker_b_resume")
        assert resumed_lease is not None
        assert resumed_lease["resume_run_id"] == "run_takeover"
        assert resumed_lease["resume_takeover_id"] == takeover["takeover_request_id"]
        started = coordinator.start_delivery(
            resumed_lease,
            run_id="run_takeover",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
        )
        assert started["run"]["run_id"] == "run_takeover"
        assert started["run"]["status"] == "running"
        assert len(work.list_runs_for_request(lease["request_id"])) == 1
    finally:
        database.close()
