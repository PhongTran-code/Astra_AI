"""Requester-scoped context assembly tests."""

from datetime import datetime, timezone

import pytest

from coworker.teams import (
    ActorKind,
    ActorRef,
    AgentRepository,
    ArtifactRepository,
    ContextBoundary,
    ContextPackageBuilder,
    ContextRepository,
    ContextScope,
    ConversationRepository,
    EventRepository,
    HandoffRepository,
    IdentityRepository,
    ResourceRef,
    SandboxRepository,
    SearchRepository,
    TaskRepository,
    TeamDatabase,
    TeamPermissionService,
    TeamService,
    WorkCoordinator,
    WorkRepository,
)


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")
NOW = "2026-08-30T00:00:00+00:00"


@pytest.fixture
def service(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER, handle="owner")
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    agents = AgentRepository(database)
    actors = {}
    for suffix, role in (("builder", "builder"), ("verifier", "verifier"), ("outsider", "builder")):
        actor = ActorRef(f"actor_{suffix}", ActorKind.AGENT, suffix.title())
        actors[suffix] = actor
        identities.create_actor(actor, handle=suffix)
        identities.add_membership(f"membership_{suffix}", "workspace_1", actor.id, "member")
        agents.create_definition(
            f"definition_{suffix}", "workspace_1", OWNER.id, suffix.title(), suffix, role
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
    conversations = ConversationRepository(database)
    tasks = TaskRepository(database)
    context = ContextRepository(database)
    artifacts = ArtifactRepository(database)
    builder = ContextPackageBuilder(
        identities, conversations, context, tasks, artifacts, agents=agents
    )
    service = TeamService(
        identities,
        agents,
        conversations,
        WorkCoordinator(
            WorkRepository(database),
            conversations,
            TeamPermissionService(),
            events=EventRepository(database),
            tasks=tasks,
            handoffs=HandoffRepository(database),
        ),
        TeamPermissionService(),
        workspace_id="workspace_1",
        actor_id=OWNER.id,
        tasks=tasks,
        artifacts=artifacts,
        search=SearchRepository(database),
        events=EventRepository(database),
        context=context,
        context_builder=builder,
    )
    yield service, database, actors
    database.close()


def test_context_package_keeps_dm_private_and_accepts_explicit_share(service):
    team, database, _actors = service
    direct = team.create_conversation(kind="direct", agent_ids=("agent_builder",))
    group = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    direct_message = team.send_message(
        direct["conversation_id"], body="private requirement", client_request_id="dm-1"
    )
    assert direct_message.message is not None
    group_package = team.build_context(group["conversation_id"])
    assert group_package["messages"] == []
    assert group_package["shared_resources"] == []

    share = team.create_context_share(
        resource=ResourceRef("message", direct_message.message["message_id"]),
        source=ContextBoundary(ContextScope.DIRECT_MESSAGE, direct["conversation_id"]),
        target=ContextBoundary(ContextScope.GROUP, group["conversation_id"]),
        reason="The group needs the approved requirement.",
        idempotency_key="share-dm-1",
    )
    assert share["resource_id"] == direct_message.message["message_id"]
    group_package = team.build_context(group["conversation_id"])
    assert group_package["messages"] == []
    assert group_package["shared_resources"][0]["resource"]["message"]["body"] == "private requirement"


def test_open_thread_is_idempotent_and_replies_stay_in_thread(service):
    team, _database, _actors = service
    group = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    root = team.send_message(
        group["conversation_id"], body="Need a focused review.", client_request_id="thread-root"
    )
    assert root.message is not None

    first = team.open_thread(group["conversation_id"], root.message["message_id"])
    replay = team.open_thread(group["conversation_id"], root.message["message_id"])
    assert first["thread"]["thread_id"] == replay["thread"]["thread_id"]
    assert [message["message_id"] for message in first["messages"]] == [root.message["message_id"]]

    reply = team.send_message(
        group["conversation_id"],
        body="I will review it.",
        client_request_id="thread-reply",
        thread_id=first["thread"]["thread_id"],
        parent_message_id=root.message["message_id"],
    )
    assert reply.message is not None
    view = team.get_thread(first["thread"]["thread_id"])
    assert [message["message_id"] for message in view["messages"]] == [
        root.message["message_id"],
        reply.message["message_id"],
    ]


def test_agent_reply_is_idempotent_across_delivery_run_retries(service):
    team, database, _actors = service
    SandboxRepository(database).create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
        status="ready",
    )
    conversation = team.create_conversation(kind="direct", agent_ids=("agent_builder",))
    submitted = team.send_message(
        conversation["conversation_id"],
        body="Reply exactly once even if the worker retries.",
        client_request_id="reply-retry-root",
    )
    delivery_id = submitted.deliveries[0]["delivery_id"]
    lease = team.coordinator.claim_next("agent_builder", "worker-1", now=NOW)
    assert lease is not None
    team.coordinator.start_delivery(
        lease,
        run_id="reply-run-1",
        agent_revision_id="revision_builder",
        sandbox_id="sandbox_builder",
        now=NOW,
    )
    first = team.append_agent_message(
        conversation["conversation_id"],
        agent_id="agent_builder",
        body="The durable reply.",
        run_id="reply-run-1",
        correlation_id=submitted.request["correlation_id"],
    )
    team.coordinator.settle_run(
        "reply-run-1",
        "failed",
        lease_token=lease["lease_token"],
        retry_policy={"max_attempts": 2, "base_delay_seconds": 1},
        now=NOW,
    )

    lease_retry = team.coordinator.claim_next(
        "agent_builder", "worker-2", now="2026-08-30T00:00:02+00:00"
    )
    assert lease_retry is not None
    team.coordinator.start_delivery(
        lease_retry,
        run_id="reply-run-2",
        agent_revision_id="revision_builder",
        sandbox_id="sandbox_builder",
        now="2026-08-30T00:00:02+00:00",
    )
    replay = team.append_agent_message(
        conversation["conversation_id"],
        agent_id="agent_builder",
        body="The durable reply.",
        run_id="reply-run-2",
        correlation_id=submitted.request["correlation_id"],
    )

    assert replay["message_id"] == first["message_id"]
    assert len(
        team.conversations.list_messages(
            conversation["conversation_id"], requester_actor_id=team.actor_id
        )["messages"]
    ) == 2
    assert database._conn.execute(
        "SELECT COUNT(*) FROM deliveries WHERE delivery_id = ?", (delivery_id,)
    ).fetchone()[0] == 1


def test_activity_projects_durable_user_facing_events(service):
    team, _database, _actors = service
    conversation = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    sent = team.send_message(
        conversation["conversation_id"],
        body="Show this in activity.",
        client_request_id="activity-message-1",
    )

    page = team.list_activity(limit=100)
    activity = next(
        item for item in page["activities"] if item["event_type"] == "message.created"
    )
    assert activity["activity_id"] == activity["event_id"]
    assert activity["activity_kind"] == "message created"
    assert activity["payload"]["conversation_id"] == conversation["conversation_id"]
    assert activity["aggregate_id"] == sent.message["message_id"]


def test_semantic_search_post_filters_to_real_visible_resources(service):
    team, _database, _actors = service
    conversation = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    visible = team.send_message(
        conversation["conversation_id"],
        body="Visible semantic source.",
        client_request_id="semantic-visible-message",
    )
    assert visible.message is not None

    def semantic_provider(**kwargs):
        assert conversation["conversation_id"] in kwargs["conversation_ids"]
        return {
            "enabled": True,
            "provider": "test-provider",
            "results": [
                # A provider cannot turn a made-up id into a visible hit, even
                # when it claims the hit belongs to a visible conversation.
                {
                    "kind": "message",
                    "id": "forged-message",
                    "conversation_id": conversation["conversation_id"],
                    "body": "forged result",
                },
                {
                    "kind": "message",
                    "id": "hidden-message",
                    "conversation_id": "conversation-from-another-tenant",
                    "body": "private result",
                },
                {
                    "kind": "message",
                    "id": visible.message["message_id"],
                    "conversation_id": conversation["conversation_id"],
                    "body": "real result",
                },
                {"kind": "artifact", "id": "artifact-from-another-tenant"},
                "malformed result",
            ],
        }

    team.semantic_search = semantic_provider
    result = team.search_all("semantic", kinds=("messages",), semantic=True)

    assert [item["id"] for item in result["semantic"]["results"]] == [
        visible.message["message_id"]
    ]


def test_notification_quiet_hours_support_local_and_overnight_windows():
    preferences = {
        "quiet_hours": {
            "start": "22:00",
            "end": "07:00",
            "timezone": "Asia/Ho_Chi_Minh",
        }
    }
    assert TeamService._quiet_hours_active(
        preferences, now=datetime(2026, 8, 30, 15, 30, tzinfo=timezone.utc)
    ) is True
    assert TeamService._quiet_hours_active(
        preferences, now=datetime(2026, 8, 30, 1, 30, tzinfo=timezone.utc)
    ) is False


def test_notification_quiet_hours_do_not_crash_on_invalid_legacy_timezone():
    assert TeamService._quiet_hours_active(
        {"quiet_hours": {"start": "09:00", "end": "17:00", "timezone": "Not/AZone"}},
        now=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    ) is False


def test_conversation_list_exposes_requester_scoped_unread_projection(service):
    team, _database, actors = service
    conversation = team.create_conversation(kind="direct", agent_ids=("agent_builder",))
    team.send_message(
        conversation["conversation_id"],
        body="Owner message is already read by its author.",
        client_request_id="unread-owner-message",
    )
    listed = next(
        item for item in team.list_conversations()
        if item["conversation_id"] == conversation["conversation_id"]
    )
    assert listed["agent_member_ids"] == ["agent_builder"]
    assert listed["agent_actor_ids"] == [actors["builder"].id]
    assert listed["unread_count"] == 0

    team.conversations.append_message(
        "unread-agent-message",
        conversation["conversation_id"],
        actors["builder"],
        "Agent message needs attention.",
        "unread-agent-client",
        "unread-agent-correlation",
    )
    listed = next(
        item for item in team.list_conversations()
        if item["conversation_id"] == conversation["conversation_id"]
    )
    assert listed["unread_count"] == 1


def test_direct_conversation_is_reused_for_the_same_agent(service):
    team, _database, _actors = service
    first = team.create_conversation(kind="direct", agent_ids=("agent_builder",))
    second = team.create_conversation(kind="direct", agent_ids=("agent_builder",))

    assert second["conversation_id"] == first["conversation_id"]
    direct_conversations = [
        item for item in team.list_conversations() if item["conversation_kind"] == "direct"
    ]
    assert len(direct_conversations) == 1
    assert direct_conversations[0]["conversation_id"] == first["conversation_id"]


def test_context_package_includes_only_agent_private_memory_for_that_agent(service):
    team, _database, actors = service
    group = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    team.create_memory(
        scope=ContextScope.AGENT_PRIVATE,
        scope_id="agent_builder",
        content="builder-only note",
    )
    builder_package = team.build_context(
        group["conversation_id"], agent_id="agent_builder"
    )
    reviewer_package = team.build_context(
        group["conversation_id"], agent_id="agent_verifier"
    )
    assert [item["content"] for item in builder_package["memories"]] == ["builder-only note"]
    assert reviewer_package["memories"] == []
    assert builder_package["principal_actor_id"] == actors["builder"].id


def test_human_cannot_share_an_agent_private_memory_by_guessing_its_id(service):
    team, _database, _actors = service
    group = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    memory = team.create_memory(
        scope=ContextScope.AGENT_PRIVATE,
        scope_id="agent_builder",
        content="private execution note",
    )
    with pytest.raises(ValueError, match="not readable"):
        team.create_context_share(
            resource=ResourceRef("memory", memory["memory_id"]),
            source=ContextBoundary(ContextScope.AGENT_PRIVATE, "agent_builder"),
            target=ContextBoundary(ContextScope.GROUP, group["conversation_id"]),
            reason="should be denied",
            idempotency_key="share-private-1",
        )


def test_context_package_is_hard_bounded_and_rejects_non_member_principal(service):
    team, _database, _actors = service
    group = team.create_conversation(kind="group", agent_ids=("agent_builder", "agent_verifier"))
    for index in range(5):
        team.send_message(
            group["conversation_id"], body=f"message-{index} with extra text", client_request_id=f"m-{index}"
        )
    package = team.build_context(group["conversation_id"], max_messages=5, max_chars=500)
    assert package["truncated"] is True
    assert len(str(package)) <= 500
    with pytest.raises(ValueError, match="not an active conversation member"):
        team.build_context(group["conversation_id"], agent_id="agent_outsider")
