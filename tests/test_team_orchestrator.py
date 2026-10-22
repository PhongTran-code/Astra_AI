"""Crew fan-out, typed child results, and bounded graph behavior."""

from __future__ import annotations

import pytest

from coworker.teams import (
    AgentRepository,
    ActorKind,
    ActorRef,
    ChildResultEnvelope,
    ConversationRepository,
    ContractError,
    CrewLimitExceeded,
    CrewOrchestrator,
    HandoffContract,
    HandoffRepository,
    IdentityRepository,
    SandboxRepository,
    TaskRepository,
    TeamDatabase,
    TeamPermissionService,
    WorkBudget,
    WorkCoordinator,
    WorkRepository,
)
from coworker.teams.permissions import PermissionContext, SandboxPolicy, WorkspaceRole


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")
AGENT_A = ActorRef("actor_a", ActorKind.AGENT, "A")
AGENT_B = ActorRef("actor_b", ActorKind.AGENT, "B")


def _stack(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER)
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    agents = AgentRepository(database)
    for suffix, actor in (("a", AGENT_A), ("b", AGENT_B)):
        identities.create_actor(actor)
        identities.add_membership(f"membership_{suffix}", "workspace_1", actor.id, "member")
        agents.create_definition(f"definition_{suffix}", "workspace_1", OWNER.id, suffix.upper(), suffix, "builder")
        agents.create_revision(f"revision_{suffix}", f"definition_{suffix}", 1, "Work", OWNER.id)
        agents.install(f"agent_{suffix}", "workspace_1", actor.id, f"definition_{suffix}", f"revision_{suffix}", status="ready")
        SandboxRepository(database).create_sandbox(
            f"sandbox_{suffix}", "workspace_1", f"agent_{suffix}", "local", "weak_local", str(tmp_path / f"home_{suffix}"), status="ready"
        )
    tasks = TaskRepository(database)
    tasks.create_task("task_root", "workspace_1", "Root", "Child evidence", OWNER.id, owner_agent_id="agent_a")
    work = WorkRepository(database)
    coordinator = WorkCoordinator(work, ConversationRepository(database), TeamPermissionService(), tasks=tasks, handoffs=HandoffRepository(database))
    orchestrator = CrewOrchestrator(coordinator, work, HandoffRepository(database), max_depth=2, max_handoffs=2, max_fanout=1)
    context = {
        "agent_b": PermissionContext(
            workspace_id="workspace_1", requester=OWNER, requester_role=WorkspaceRole.OWNER,
            agent_id="agent_b", agent_actor_id=AGENT_B.id, agent_role="builder",
            sandbox=SandboxPolicy(actions=frozenset({"sandbox.read", "run.execute"})),
        )
    }
    return database, work, coordinator, orchestrator, tasks, context


def _handoff(key: str = "handoff_1") -> HandoffContract:
    return HandoffContract(
        handoff_id=key,
        workspace_id="workspace_1",
        task_id="task_root",
        sender_agent_id="agent_a",
        recipient_agent_id="agent_b",
        purpose="Investigate the issue",
        expected_output="Summary and evidence",
        requester=OWNER,
        idempotency_key=f"idempotency_{key}",
        correlation_id=f"correlation_{key}",
        budget=WorkBudget(max_handoffs=2),
    )


def test_handoff_is_queued_once_and_fanout_is_bounded(tmp_path):
    database, _work, _coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    try:
        first = orchestrator.submit_handoff(_handoff(), context)
        assert first.dispatchable
        assert orchestrator.handoffs.get("handoff_1")["status"] == "accepted"
        with pytest.raises(CrewLimitExceeded, match="fan-out"):
            orchestrator.submit_handoff(_handoff("handoff_2"), context)
    finally:
        database.close()


def test_child_result_is_typed_durable_and_aggregates_deterministically(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    try:
        orchestrator.submit_handoff(_handoff(), context)
        delivery = coordinator.claim_next("agent_b", "worker_b")
        assert delivery is not None
        run = coordinator.start_delivery(
            delivery, run_id="run_child", agent_revision_id="revision_b", sandbox_id="sandbox_b",
        )
        assert run["run"]["status"] == "running"
        persisted = orchestrator.record_child_result(
            ChildResultEnvelope(
                handoff_id="handoff_1", child_run_id="run_child", status="succeeded",
                summary="Found the root cause", evidence=({"kind": "test", "passed": True},),
            ),
            child_run_id="run_child", actor_id=AGENT_B.id,
        )
        assert persisted["event_type"] == "handoff.child_result"
        result = orchestrator.get_child_result("handoff_1")
        assert result.status == "succeeded"
        assert orchestrator.aggregate_task("task_root")["status"] == "succeeded"
        with pytest.raises(ContractError, match="running child agent"):
            orchestrator.record_child_result(
                ChildResultEnvelope(handoff_id="handoff_1", child_run_id="run_child", status="succeeded"),
                child_run_id="run_child", actor_id=OWNER.id,
            )
    finally:
        database.close()
