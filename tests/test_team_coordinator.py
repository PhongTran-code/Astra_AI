"""Coordinator tests for routing, queue fairness, retries, and settlement."""

import pytest

from coworker.teams import (
    AgentRepository,
    AgentStatus,
    ActorKind,
    ActorRef,
    BudgetExceededError,
    ConversationRepository,
    ContractError,
    DeliveryStatus,
    IdentityRepository,
    SandboxPolicy,
    SandboxRepository,
    TaskRepository,
    TeamDatabase,
    TeamPermissionService,
    WorkBudget,
    WorkCoordinator,
)
from coworker.teams.permissions import PermissionContext, WorkspaceRole
from coworker.teams.persistence import WorkRepository


NOW = "2026-08-29T12:00:00+00:00"
OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")


@pytest.fixture
def database(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    yield database
    database.close()


def seed_agent(database):
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER)
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    agent_actor = ActorRef("actor_agent", ActorKind.AGENT, "Builder")
    identities.create_actor(agent_actor)
    identities.add_membership("membership_agent", "workspace_1", agent_actor.id, "member")
    agents = AgentRepository(database)
    agents.create_definition(
        "agent_definition_builder",
        "workspace_1",
        OWNER.id,
        "Builder",
        "builder",
        "builder",
    )
    agents.create_revision(
        "agent_revision_builder_1",
        "agent_definition_builder",
        1,
        "Build and report evidence.",
        OWNER.id,
    )
    agents.install(
        "agent_builder",
        "workspace_1",
        agent_actor.id,
        "agent_definition_builder",
        "agent_revision_builder_1",
        status="ready",
    )
    return agent_actor


def auth_context(agent_id="agent_builder", agent_actor_id="actor_agent"):
    return PermissionContext(
        workspace_id="workspace_1",
        requester=OWNER,
        requester_role=WorkspaceRole.OWNER,
        agent_id=agent_id,
        agent_actor_id=agent_actor_id,
        agent_role="builder",
        sandbox=SandboxPolicy(actions=frozenset({"sandbox.read", "run.execute"})),
    )


def coordinator(database):
    return WorkCoordinator(
        WorkRepository(database),
        ConversationRepository(database),
        TeamPermissionService(),
    )


def test_message_is_persisted_before_confirmation_and_duplicate_dispatch_is_idempotent(database):
    agent_actor = seed_agent(database)
    conversations = ConversationRepository(database)
    conversations.create_conversation(
        "conversation_group",
        "workspace_1",
        "group",
        OWNER.id,
        reply_mode="all_messages",
        allow_at_all=True,
    )
    conversations.add_member("conversation_group", OWNER.id)
    conversations.add_member("conversation_group", agent_actor.id)
    service = coordinator(database)
    context = {"agent_builder": auth_context()}

    preview = service.submit_message(
        message_id="message_all",
        conversation_id="conversation_group",
        sender=OWNER,
        body="Review this release.",
        context_by_agent=context,
        correlation_id="trace_all",
        at_all=True,
    )
    assert preview.request is None
    assert preview.requires_confirmation
    assert conversations.get_message("message_all")["body"] == "Review this release."

    confirmed = service.submit_message(
        message_id="message_all",
        conversation_id="conversation_group",
        sender=OWNER,
        body="Review this release.",
        context_by_agent=context,
        correlation_id="trace_all",
        at_all=True,
        fanout_confirmed=True,
    )
    duplicate = service.submit_message(
        message_id="message_all",
        conversation_id="conversation_group",
        sender=OWNER,
        body="Review this release.",
        context_by_agent=context,
        correlation_id="trace_all",
        at_all=True,
        fanout_confirmed=True,
    )
    assert confirmed.request["request_id"] == duplicate.request["request_id"]
    assert [item["delivery_id"] for item in confirmed.deliveries] == [
        item["delivery_id"] for item in duplicate.deliveries
    ]


def test_direct_message_priority_beats_automation_and_expired_lease_returns_to_queue(database):
    agent_actor = seed_agent(database)
    conversations = ConversationRepository(database)
    conversations.create_conversation(
        "conversation_dm",
        "workspace_1",
        "direct",
        OWNER.id,
        reply_mode="coordinator",
        coordinator_agent_id="agent_builder",
    )
    conversations.add_member("conversation_dm", OWNER.id)
    conversations.add_member("conversation_dm", agent_actor.id)
    service = coordinator(database)
    context = {"agent_builder": auth_context()}
    service.submit_automation(
        request_id="request_automation",
        workspace_id="workspace_1",
        automation_id="automation_daily",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Run the daily report.",
        idempotency_key="automation_daily",
        correlation_id="trace_automation",
        context_by_agent=context,
        priority=100,
    )
    service.submit_message(
        message_id="message_direct",
        conversation_id="conversation_dm",
        sender=OWNER,
        body="Answer the user now.",
        context_by_agent=context,
        correlation_id="trace_direct",
        priority=-100,
    )
    ready = service.work.list_ready_deliveries("agent_builder", now=NOW)
    assert ready[0]["source_kind"] == "direct_message"

    lease = service.claim_next("agent_builder", "worker_1", now=NOW, lease_seconds=1)
    assert lease is not None
    assert service.work.recover_expired_leases(now="2026-08-29T12:00:02+00:00") == [lease["delivery_id"]]
    assert service.work.get_delivery(lease["delivery_id"])["status"] == DeliveryStatus.PENDING.value


def test_paused_offline_and_archived_agents_keep_work_queued_but_cannot_claim(database):
    seed_agent(database)
    service = coordinator(database)
    submitted = service.submit_automation(
        request_id="request_status_gate",
        workspace_id="workspace_1",
        automation_id="automation_status_gate",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Wait for the agent to recover.",
        idempotency_key="automation_status_gate",
        correlation_id="trace_status_gate",
        context_by_agent={"agent_builder": auth_context()},
    )
    agents = AgentRepository(database)
    for status in (AgentStatus.PAUSED, AgentStatus.OFFLINE, AgentStatus.ARCHIVED):
        agents.set_status("agent_builder", status)
        assert service.claim_next("agent_builder", f"worker-{status.value}", now=NOW) is None
        assert service.work.get_delivery(submitted.deliveries[0]["delivery_id"])["status"] == DeliveryStatus.PENDING.value
        if status is not AgentStatus.ARCHIVED:
            agents.set_status("agent_builder", AgentStatus.READY)


def test_new_work_requests_reject_paused_and_archived_targets(database):
    seed_agent(database)
    service = coordinator(database)
    agents = AgentRepository(database)
    for status in (AgentStatus.PAUSED, AgentStatus.ARCHIVED):
        agents.set_status("agent_builder", status)
        with pytest.raises(ContractError, match=f"{status.value}"):
            service.submit_automation(
                request_id=f"request_reject_{status.value}",
                workspace_id="workspace_1",
                automation_id=f"automation_reject_{status.value}",
                requester=OWNER,
                target_agent_ids=("agent_builder",),
                instruction="must not be queued",
                idempotency_key=f"automation_reject_{status.value}",
                correlation_id=f"trace_reject_{status.value}",
                context_by_agent={"agent_builder": auth_context()},
            )
        if status is AgentStatus.PAUSED:
            agents.set_status("agent_builder", AgentStatus.READY)


def test_failed_run_retries_as_a_new_run_and_success_completes_request(database):
    seed_agent(database)
    service = coordinator(database)
    submitted = service.submit_automation(
        request_id="request_retry",
        workspace_id="workspace_1",
        automation_id="automation_retry",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Run the resilient job.",
        idempotency_key="automation_retry",
        correlation_id="trace_retry",
        context_by_agent={"agent_builder": auth_context()},
    )
    SandboxRepository(database).create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
    )
    lease = service.claim_next("agent_builder", "worker_1", now=NOW)
    assert lease is not None
    started = service.start_delivery(
        lease,
        run_id="run_retry_1",
        agent_revision_id="agent_revision_builder_1",
        sandbox_id="sandbox_builder",
        now=NOW,
    )
    failed = service.settle_run(
        "run_retry_1",
        "failed",
        lease_token=lease["lease_token"],
        error_code="provider_unavailable",
        retry_policy={"max_attempts": 2, "base_delay_seconds": 10, "max_delay_seconds": 30},
        now=NOW,
    )
    assert failed["run"]["status"] == "failed"
    assert failed["delivery"]["status"] == DeliveryStatus.RETRY_WAIT.value
    assert failed["request"]["status"] == "active"

    lease_retry = service.claim_next(
        "agent_builder",
        "worker_2",
        now="2026-08-29T12:00:11+00:00",
    )
    assert lease_retry is not None
    service.start_delivery(
        lease_retry,
        run_id="run_retry_2",
        agent_revision_id="agent_revision_builder_1",
        sandbox_id="sandbox_builder",
        now="2026-08-29T12:00:11+00:00",
    )
    settled = service.settle_run(
        "run_retry_2",
        "succeeded",
        lease_token=lease_retry["lease_token"],
        now="2026-08-29T12:00:12+00:00",
    )
    assert settled["delivery"]["status"] == DeliveryStatus.ACKNOWLEDGED.value
    assert settled["request"]["status"] == "completed"
    assert service.work.get_request(submitted.request["request_id"])["status"] == "completed"
    assert database._conn.execute("SELECT COUNT(*) FROM runs WHERE delivery_id = ?", (lease["delivery_id"],)).fetchone()[0] == 2


def test_expired_run_with_consequential_activity_is_not_blindly_retried(database):
    agent_actor = seed_agent(database)
    service = coordinator(database)
    submitted = service.submit_automation(
        request_id="request_recovery_side_effect",
        workspace_id="workspace_1",
        automation_id="automation_recovery_side_effect",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Run the side effect once.",
        idempotency_key="automation_recovery_side_effect",
        correlation_id="trace_recovery_side_effect",
        context_by_agent={"agent_builder": auth_context()},
    )
    SandboxRepository(database).create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
    )
    lease = service.claim_next(
        "agent_builder", "worker_recovery", now=NOW, lease_seconds=1
    )
    assert lease is not None
    service.start_delivery(
        lease,
        run_id="run_recovery_side_effect",
        agent_revision_id="agent_revision_builder_1",
        sandbox_id="sandbox_builder",
        now=NOW,
    )
    database._conn.execute(
        """
        INSERT INTO run_events
            (run_event_id, run_id, event_type, actor_id, payload_json, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "run-event-recovery-side-effect",
            "run_recovery_side_effect",
            "run.consequential_started",
            agent_actor.id,
            '{"tool":"team_send_message"}',
            NOW,
        ),
    )
    database._conn.commit()

    recovered = service.work.recover_expired_leases(
        now="2026-08-29T12:00:02+00:00"
    )

    assert recovered == [submitted.deliveries[0]["delivery_id"]]
    assert service.work.get_delivery(recovered[0])["status"] == DeliveryStatus.DEAD_LETTER.value
    assert service.work.get_run("run_recovery_side_effect")["status"] == "timed_out"


def test_usage_budget_is_atomic_and_cancellation_is_terminal(database):
    seed_agent(database)
    service = coordinator(database)
    submitted = service.submit_automation(
        request_id="request_budget",
        workspace_id="workspace_1",
        automation_id="automation_budget",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Run the bounded job.",
        idempotency_key="automation_budget",
        correlation_id="trace_budget",
        context_by_agent={"agent_builder": auth_context()},
        budget=WorkBudget(max_tool_calls=1),
    )
    assert service.consume_usage(submitted.request["request_id"], {"tool_calls": 1})["tool_calls"] == 1
    with pytest.raises(BudgetExceededError):
        service.consume_usage(submitted.request["request_id"], {"tool_calls": 1})
    assert service.work.get_usage(submitted.request["request_id"])["tool_calls"] == 1
    paused = service.work.get_request(submitted.request["request_id"])
    assert paused["budget_paused_at"]
    assert service.claim_next("agent_builder", "worker_paused", now=NOW) is None
    service.resume_request(submitted.request["request_id"])
    lease = service.claim_next("agent_builder", "worker_resumed", now=NOW)
    assert lease is not None
    canceled = service.cancel_request(submitted.request["request_id"], reason="user stopped it")
    assert canceled["status"] == "canceled"
    assert service.work.get_delivery(submitted.deliveries[0]["delivery_id"])["status"] == DeliveryStatus.CANCELED.value


def test_task_scope_cancel_cascades_requests_interrupts_live_run_and_preserves_completed_history(database):
    seed_agent(database)
    tasks = TaskRepository(database)
    tasks.create_task(
        "task_parent",
        "workspace_1",
        "Parent task",
        "parent result",
        OWNER.id,
    )
    tasks.create_task(
        "task_child",
        "workspace_1",
        "Child task",
        "child result",
        OWNER.id,
        parent_task_id="task_parent",
    )
    work = WorkRepository(database)
    service = WorkCoordinator(
        work,
        ConversationRepository(database),
        TeamPermissionService(),
        tasks=tasks,
    )
    SandboxRepository(database).create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
    )
    context = {"agent_builder": auth_context()}
    completed = service.submit_task(
        request_id="request_child_completed",
        workspace_id="workspace_1",
        task_id="task_child",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Complete the child before stopping the parent.",
        idempotency_key="request_child_completed_key",
        correlation_id="trace_child_completed",
        context_by_agent=context,
    )
    child_lease = service.claim_next("agent_builder", "worker_child", now=NOW)
    assert child_lease is not None
    service.start_delivery(
        child_lease,
        run_id="run_child_completed",
        agent_revision_id="agent_revision_builder_1",
        sandbox_id="sandbox_builder",
        now=NOW,
    )
    service.settle_run(
        "run_child_completed",
        "succeeded",
        lease_token=child_lease["lease_token"],
        now="2026-08-29T12:00:01+00:00",
    )
    assert work.get_request(completed.request["request_id"])["status"] == "completed"

    active = service.submit_task(
        request_id="request_parent_active",
        workspace_id="workspace_1",
        task_id="task_parent",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="This parent run must be stopped.",
        idempotency_key="request_parent_active_key",
        correlation_id="trace_parent_active",
        context_by_agent=context,
    )
    parent_lease = service.claim_next("agent_builder", "worker_parent", now=NOW)
    assert parent_lease is not None
    service.start_delivery(
        parent_lease,
        run_id="run_parent_active",
        agent_revision_id="agent_revision_builder_1",
        sandbox_id="sandbox_builder",
        now="2026-08-29T12:00:02+00:00",
    )

    canceled = service.cancel_scope(
        "task", "task_parent", reason="parent task was stopped"
    )

    assert canceled["canceled_request_ids"] == [active.request["request_id"]]
    assert canceled["untouched_terminal_request_ids"] == [completed.request["request_id"]]
    assert canceled["canceled_run_ids"] == ["run_parent_active"]
    assert work.get_request(active.request["request_id"])["status"] == "canceled"
    assert work.get_run("run_parent_active")["status"] == "canceled"
    assert work.get_delivery(parent_lease["delivery_id"])["status"] == DeliveryStatus.CANCELED.value
    assert work.get_delivery(child_lease["delivery_id"])["status"] == DeliveryStatus.ACKNOWLEDGED.value

    canceled_children = tasks.cancel_descendants("task_parent")
    assert [task["task_id"] for task in canceled_children] == ["task_child"]
    assert tasks.get_task("task_child")["status"] == "canceled"
