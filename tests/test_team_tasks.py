"""Task graph, structured handoff, and source-event invariants."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from coworker.teams import (
    AgentRepository,
    ActorKind,
    ActorRef,
    ConversationRepository,
    CoordinatorError,
    ContractError,
    EventRepository,
    HandoffContract,
    HandoffRepository,
    IdentityRepository,
    PersistenceConflictError,
    ResourceRef,
    TaskRepository,
    TeamDatabase,
    TeamPermissionService,
    WorkBudget,
    WorkCoordinator,
)
from coworker.teams.permissions import PermissionContext, SandboxPolicy, WorkspaceRole
from coworker.teams.persistence import WorkRepository


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")


@pytest.fixture
def stack(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER, handle="owner")
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    agents = AgentRepository(database)
    actors = {}
    for suffix in ("builder", "verifier"):
        actor = ActorRef(f"actor_{suffix}", ActorKind.AGENT, suffix.title())
        actors[suffix] = actor
        identities.create_actor(actor, handle=suffix)
        identities.add_membership(f"membership_{suffix}", "workspace_1", actor.id, "member")
        agents.create_definition(
            f"definition_{suffix}", "workspace_1", OWNER.id, suffix.title(), suffix, suffix
        )
        agents.create_revision(
            f"revision_{suffix}", f"definition_{suffix}", 1, "Work safely.", OWNER.id
        )
        agents.install(
            f"agent_{suffix}",
            "workspace_1",
            actor.id,
            f"definition_{suffix}",
            f"revision_{suffix}",
            status="ready",
        )
    yield database, identities, agents, actors
    database.close()


def test_task_claim_is_compare_and_set_and_owner_transition_uses_actor_identity(stack):
    database, _identities, _agents, actors = stack
    tasks = TaskRepository(database)
    tasks.create_task(
        "task_claim",
        "workspace_1",
        "Claim once",
        "one owner",
        OWNER.id,
    )

    def claim(agent_id):
        try:
            return ("ok", tasks.claim_task("task_claim", agent_id))
        except PersistenceConflictError:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("agent_builder", "agent_verifier")))
    assert [result[0] for result in results].count("ok") == 1
    assert tasks.get_task("task_claim")["owner_agent_id"] in {"agent_builder", "agent_verifier"}

    owned = tasks.create_task(
        "task_owned",
        "workspace_1",
        "Owner identity",
        "reviewed",
        OWNER.id,
        owner_agent_id="agent_builder",
        reviewer_actor_id=OWNER.id,
    )
    with pytest.raises(ContractError, match="does not match"):
        tasks.transition_task(
            owned["task_id"],
            "in_progress",
            actor_id="agent_builder",
            authority="task_owner",
        )
    assert tasks.transition_task(
        owned["task_id"],
        "in_progress",
        actor_id=actors["builder"].id,
        authority="task_owner",
    )["status"] == "in_progress"


def test_handoff_is_durable_idempotent_and_cannot_cross_workspace(stack):
    database, _identities, _agents, actors = stack
    tasks = TaskRepository(database)
    tasks.create_task("task_handoff", "workspace_1", "Verify", "report", OWNER.id)
    handoff = HandoffContract(
        handoff_id="handoff_1",
        workspace_id="workspace_1",
        task_id="task_handoff",
        sender_agent_id="agent_builder",
        recipient_agent_id="agent_verifier",
        purpose="Verify the builder output.",
        expected_output="A review report.",
        requester=OWNER,
        idempotency_key="handoff_dedupe_1",
        correlation_id="trace_handoff_1",
        budget=WorkBudget(max_handoffs=2),
        agent_path=("agent_builder",),
    )
    repository = HandoffRepository(database)
    first = repository.create(handoff)
    assert repository.create(handoff) == first
    assert first["status"] == "pending"
    assert repository.list_for_task("task_handoff")[0]["recipient_agent_id"] == "agent_verifier"

    with pytest.raises(PersistenceConflictError):
        repository.create(
            HandoffContract.from_dict(
                {
                    **handoff.to_dict(),
                    "handoff_id": "handoff_other",
                    "purpose": "Different immutable purpose.",
                }
            )
        )


def test_crew_run_membership_is_durable_and_bounds_task_requests(stack):
    database, _identities, _agents, actors = stack
    tasks = TaskRepository(database)
    tasks.create_task(
        "task_crew_root",
        "workspace_1",
        "Crew root",
        "a reviewed result",
        OWNER.id,
        owner_agent_id="agent_builder",
    )
    work = WorkRepository(database)
    crew = work.create_crew_run(
        "crew_run_1",
        "workspace_1",
        OWNER.id,
        "crew_trace_1",
        root_task_id="task_crew_root",
        agent_ids=("agent_builder", "agent_verifier"),
        agent_roles={"agent_builder": "builder", "agent_verifier": "reviewer"},
    )
    assert [item["agent_id"] for item in crew["agents"]] == [
        "agent_builder",
        "agent_verifier",
    ]
    assert work.create_crew_run(
        "crew_run_1",
        "workspace_1",
        OWNER.id,
        "crew_trace_1",
        root_task_id="task_crew_root",
        agent_ids=("agent_builder", "agent_verifier"),
        agent_roles={"agent_builder": "builder", "agent_verifier": "reviewer"},
    ) == crew

    service = WorkCoordinator(
        work,
        ConversationRepository(database),
        TeamPermissionService(),
        tasks=tasks,
    )
    builder_context = PermissionContext(
        workspace_id="workspace_1",
        requester=OWNER,
        requester_role=WorkspaceRole.OWNER,
        agent_id="agent_builder",
        agent_actor_id=actors["builder"].id,
        agent_role="builder",
        sandbox=SandboxPolicy(actions=frozenset({"sandbox.read", "run.execute"})),
    )
    submitted = service.submit_task(
        request_id="request_crew_1",
        workspace_id="workspace_1",
        task_id="task_crew_root",
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Build the crew result.",
        expected_output="A reviewed result.",
        idempotency_key="request_crew_1_key",
        correlation_id="crew_trace_1",
        context_by_agent={"agent_builder": builder_context},
        crew_run_id="crew_run_1",
    )
    assert submitted.request["crew_run_id"] == "crew_run_1"
    with pytest.raises(ContractError, match="active work"):
        work.transition_crew_run("crew_run_1", "completed")

    service.cancel_request(submitted.request["request_id"])
    assert work.transition_crew_run("crew_run_1", "completed")["status"] == "completed"
    with pytest.raises(ContractError, match="active crew"):
        service.submit_task(
            request_id="request_crew_outside",
            workspace_id="workspace_1",
            task_id="task_crew_root",
            requester=OWNER,
            target_agent_ids=("agent_builder",),
            instruction="This should be rejected after the crew ends.",
            idempotency_key="request_crew_outside_key",
            correlation_id="crew_trace_outside",
            context_by_agent={"agent_builder": builder_context},
            crew_run_id="crew_run_1",
        )


def test_task_coordinator_rejects_incomplete_dependencies_and_message_source_event_precedes_auth(stack):
    database, _identities, _agents, actors = stack
    tasks = TaskRepository(database)
    tasks.create_task("task_dep", "workspace_1", "Dependency", "done", OWNER.id)
    tasks.create_task("task_downstream", "workspace_1", "Downstream", "done", OWNER.id)
    tasks.add_dependency("task_downstream", "task_dep")
    context = PermissionContext(
        workspace_id="workspace_1",
        requester=OWNER,
        requester_role=WorkspaceRole.OWNER,
        agent_id="agent_builder",
        agent_actor_id=actors["builder"].id,
        agent_role="builder",
        sandbox=SandboxPolicy(actions=frozenset({"sandbox.read", "run.execute"})),
    )
    coordinator = WorkCoordinator(
        WorkRepository(database),
        ConversationRepository(database),
        TeamPermissionService(),
        tasks=tasks,
        events=EventRepository(database),
    )
    with pytest.raises(CoordinatorError, match="dependencies are incomplete"):
        coordinator.submit_task(
            request_id="request_blocked",
            workspace_id="workspace_1",
            task_id="task_downstream",
            requester=OWNER,
            target_agent_ids=("agent_builder",),
            instruction="Do not start yet.",
            idempotency_key="request_blocked_dedupe",
            correlation_id="trace_blocked",
            context_by_agent={"agent_builder": context},
        )

    conversations = ConversationRepository(database)
    conversations.create_conversation(
        "conversation_event", "workspace_1", "direct", OWNER.id, reply_mode="coordinator"
    )
    conversations.add_member("conversation_event", OWNER.id)
    conversations.add_member("conversation_event", actors["builder"].id)
    with pytest.raises(CoordinatorError, match="authorization context"):
        coordinator.submit_message(
            message_id="message_event",
            conversation_id="conversation_event",
            sender=OWNER,
            body="The source must survive authorization failure.",
            context_by_agent={},
            correlation_id="trace_event",
        )
    events = EventRepository(database).list_workspace_events("workspace_1")
    source = [event for event in events["events"] if event["aggregate_id"] == "message_event"]
    assert [event["event_type"] for event in source] == ["message.created"]
