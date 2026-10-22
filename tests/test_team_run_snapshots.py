"""Regression tests for dispatch snapshots and normalized execution events."""

from coworker.events import Event, EventType
from coworker.teams import (
    AgentExecutionEvent,
    AgentRepository,
    ActorKind,
    ActorRef,
    ConversationRepository,
    EventRepository,
    IdentityRepository,
    SandboxRepository,
    TeamDatabase,
    TeamPermissionService,
    TeamService,
    WorkCoordinator,
    WorkRepository,
)


def _team(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    owner = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")
    agent_actor = ActorRef("actor_builder", ActorKind.AGENT, "Builder")
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(owner, handle="owner")
    identities.create_actor(agent_actor, handle="builder")
    identities.add_membership("membership_owner", "workspace_1", owner.id, "owner")
    identities.add_membership("membership_builder", "workspace_1", agent_actor.id, "member")

    agents = AgentRepository(database)
    agents.create_definition(
        "definition_builder",
        "workspace_1",
        owner.id,
        "Builder",
        "builder",
        "builder",
    )
    agents.create_revision(
        "revision_builder_1",
        "definition_builder",
        1,
        "Build using revision one.",
        owner.id,
    )
    agents.install(
        "agent_builder",
        "workspace_1",
        agent_actor.id,
        "definition_builder",
        "revision_builder_1",
        status="ready",
    )
    sandboxes = SandboxRepository(database)
    sandboxes.create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent-home-builder",
        status="ready",
    )
    conversations = ConversationRepository(database)
    coordinator = WorkCoordinator(
        WorkRepository(database),
        conversations,
        TeamPermissionService(),
        events=EventRepository(database),
    )
    service = TeamService(
        identities,
        agents,
        conversations,
        coordinator,
        TeamPermissionService(),
        workspace_id="workspace_1",
        actor_id=owner.id,
        sandboxes=sandboxes,
        events=EventRepository(database),
    )
    return database, agents, service


def test_interactive_work_is_pinned_and_replay_keeps_original_snapshot(tmp_path):
    database, agents, team = _team(tmp_path)
    try:
        conversation = team.create_conversation(
            kind="direct", agent_ids=("agent_builder",)
        )
        first = team.send_message(
            conversation["conversation_id"],
            body="Use the queued revision.",
            client_request_id="snapshot-replay",
        )
        request = first.request
        assert request is not None
        scope = request["permission_scope"]
        assert scope["snapshot_version"] == 1
        assert scope["agent_revision_ids"] == {"agent_builder": "revision_builder_1"}
        assert scope["sandbox_ids"] == {"agent_builder": "sandbox_builder"}
        assert scope["agent_revision_snapshots"]["agent_builder"]["instructions"] == (
            "Build using revision one."
        )

        agents.create_revision(
            "revision_builder_2",
            "definition_builder",
            2,
            "Build using revision two.",
            "actor_owner",
        )
        agents.activate_revision("agent_builder", "revision_builder_2")

        replay = team.send_message(
            conversation["conversation_id"],
            body="Use the queued revision.",
            client_request_id="snapshot-replay",
        )
        assert replay.request is not None
        assert replay.request["request_id"] == request["request_id"]
        assert replay.request["permission_scope"]["agent_revision_ids"] == {
            "agent_builder": "revision_builder_1"
        }
    finally:
        database.close()


def test_turn_events_normalize_without_reasoning_or_secret_leakage():
    reasoning = AgentExecutionEvent.from_turn_event(
        run_id="run_1",
        agent_id="agent_builder",
        actor_id="actor_builder",
        ordinal=1,
        event=Event(EventType.REASONING_DELTA, {"text": "private chain of thought"}),
    )
    assert reasoning is None

    normalized = AgentExecutionEvent.from_turn_event(
        run_id="run_1",
        agent_id="agent_builder",
        actor_id="actor_builder",
        ordinal=2,
        event=Event(
            EventType.ASSISTANT_DELTA,
            {"text": "safe progress", "api_key": "must-not-persist"},
        ),
    )
    assert normalized is not None
    assert normalized.event_type.value == "agent.content.delta"
    assert normalized.payload["text"] == "safe progress"
    assert normalized.payload["api_key"] == "[REDACTED]"
    assert normalized.to_dict()["ordinal"] == 2


def test_run_event_queries_suppress_legacy_reasoning_deltas(tmp_path):
    database, _agents, team = _team(tmp_path)
    try:
        conversation = team.create_conversation(
            kind="direct", agent_ids=("agent_builder",)
        )
        sent = team.send_message(
            conversation["conversation_id"],
            body="Do work.",
            client_request_id="req-for-legacy-event-test",
        )
        request = sent.request
        delivery_id = sent.deliveries[0]["delivery_id"]
        leased = team.coordinator.work.lease_delivery(delivery_id, "worker_1")
        started = team.coordinator.start_delivery(
            leased,
            run_id="run_legacy_test",
            agent_revision_id="revision_builder_1",
            sandbox_id="sandbox_builder",
        )
        run_id = started["run"]["run_id"]
        legacy_reasoning_id = "run-event:legacy-reasoning-1"
        safe_content_id = "run-event:safe-content-2"
        with database._lock:
            database._conn.execute(
                """
                INSERT INTO run_events (run_event_id, run_id, actor_id, event_type, payload_json, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (legacy_reasoning_id, run_id, "actor_builder", "agent.reasoning.delta", '{"text": "legacy secret chain of thought"}', "2026-09-01T00:00:00Z"),
            )
            database._conn.execute(
                """
                INSERT INTO run_events (run_event_id, run_id, actor_id, event_type, payload_json, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (safe_content_id, run_id, "actor_builder", "agent.content.delta", '{"text": "safe response"}', "2026-09-01T00:00:01Z"),
            )
            database._conn.commit()

        # 1. Test list_run_events suppresses the legacy reasoning event
        events = team.coordinator.work.list_run_events(run_id)
        event_types = [e["event_type"] for e in events]
        event_ids = [e["run_event_id"] for e in events]
        assert "agent.reasoning.delta" not in event_types
        assert legacy_reasoning_id not in event_ids
        assert "agent.content.delta" in event_types
        assert safe_content_id in event_ids

        # 2. Test single lookup get_run_event suppresses the legacy reasoning event
        assert team.coordinator.work.get_run_event(legacy_reasoning_id) is None
        assert team.coordinator.work.get_run_event(safe_content_id) is not None
    finally:
        database.close()
