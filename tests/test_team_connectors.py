"""Team connector authority: exact grants, replay safety, and outbound fencing."""

import pytest

from coworker.teams import (
    AgentRepository,
    AgentRole,
    AgentStatus,
    ActorKind,
    ActorRef,
    ConnectorRepository,
    ConversationRepository,
    EventRepository,
    HandoffRepository,
    IdentityRepository,
    SandboxRepository,
    SandboxStatus,
    TaskRepository,
    TeamDatabase,
    TeamPermissionService,
    WorkCoordinator,
    WorkRepository,
)
from coworker.teams.connectors import ConnectorAuthorityError, TeamConnectorService
from coworker.teams.permissions import PermissionContext, SandboxPolicy, WorkspaceRole


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")
AGENT_ACTOR = ActorRef("actor_agent", ActorKind.AGENT, "Operator")


@pytest.fixture
def fixture():
    database = TeamDatabase(":memory:")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Workspace")
    identities.create_actor(OWNER, handle="owner")
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    identities.create_actor(AGENT_ACTOR, handle="agent")
    identities.add_membership("membership_agent", "workspace_1", AGENT_ACTOR.id, "member")
    agents = AgentRepository(database)
    agents.create_definition(
        "agent_definition_operator",
        "workspace_1",
        OWNER.id,
        "Operator",
        "operator",
        AgentRole.OPERATOR,
    )
    agents.create_revision(
        "agent_revision_operator",
        "agent_definition_operator",
        1,
        "Handle connector work safely.",
        OWNER.id,
    )
    agents.install(
        "agent_operator",
        "workspace_1",
        AGENT_ACTOR.id,
        "agent_definition_operator",
        "agent_revision_operator",
        status=AgentStatus.READY,
    )
    sandboxes = SandboxRepository(database)
    sandboxes.create_sandbox(
        "sandbox_operator",
        "workspace_1",
        "agent_operator",
        "local",
        "weak_local",
        "/tmp/astra-team-connector-test",
        status=SandboxStatus.READY,
    )
    work = WorkRepository(database)
    coordinator = WorkCoordinator(
        work,
        ConversationRepository(database),
        TeamPermissionService(),
        events=EventRepository(database),
        tasks=TaskRepository(database),
        handoffs=HandoffRepository(database),
    )

    def context(agent_id, requester):
        return PermissionContext(
            workspace_id="workspace_1",
            requester=requester,
            requester_role=WorkspaceRole.OWNER,
            agent_id=agent_id,
            agent_actor_id=AGENT_ACTOR.id,
            agent_role=AgentRole.OPERATOR,
            sandbox=SandboxPolicy(actions=frozenset({"sandbox.read", "run.execute"})),
        )

    sent = []
    service = TeamConnectorService(
        ConnectorRepository(database),
        coordinator,
        workspace_id="workspace_1",
        actor_id=OWNER.id,
        context_factory=context,
        outbound_sender=lambda identity, payload, resource, thread: sent.append(
            (identity["connector_kind"], payload["text"], resource, thread)
        )
        or {"ok": True},
    )
    yield database, work, service, sent
    database.close()


def _grant(service, *, actions=("connector.read", "connector.send")):
    identity = service.register_identity(
        connector_kind="slack",
        external_workspace_id="T123",
        external_identity_id="B123",
        metadata={"access_token": "must-not-leak", "label": "bot"},
    )
    grant = service.grant_agent(
        external_identity_record_id=identity["external_identity_record_id"],
        agent_id="agent_operator",
        actions=actions,
        resource_ids=("C123",),
    )
    return identity, grant


def test_external_identity_is_separate_and_metadata_is_redacted(fixture):
    _database, _work, service, _sent = fixture
    identity, _grant_record = _grant(service)

    assert identity["external_workspace_id"] == "T123"
    assert identity["external_identity_id"] == "B123"
    assert identity["workspace_id"] == "workspace_1"
    assert identity["metadata"]["access_token"] == "[REDACTED]"
    assert identity["metadata"]["label"] == "bot"


def test_inbound_event_is_exactly_once_and_instruction_is_immutable(fixture):
    _database, work, service, _sent = fixture
    identity, _grant_record = _grant(service)
    kwargs = dict(
        external_identity_record_id=identity["external_identity_record_id"],
        external_event_id="Ev123",
        external_resource_id="C123",
        external_thread_id="171000.1",
        target_agent_id="agent_operator",
        instruction="Summarize the thread.",
        payload={"password": "hidden", "text": "hello"},
        idempotency_key="slack:T123:Ev123",
    )

    first = service.ingest_event(**kwargs)
    replay = service.ingest_event(**kwargs)

    assert first.replayed is False
    assert replay.replayed is True
    assert len(first.submission.deliveries) == 1
    assert replay.submission.deliveries == ()
    assert len(service.list_events()) == 1
    assert len(work.list_ready_deliveries("agent_operator")) == 1
    assert first.event["payload"]["password"] == "[REDACTED]"

    with pytest.raises(Exception, match="different payload"):
        service.ingest_event(**{**kwargs, "instruction": "Send it elsewhere."})


def test_unauthorized_inbound_event_is_durable_and_replay_safe(fixture):
    _database, work, service, _sent = fixture
    identity = service.register_identity(
        connector_kind="slack",
        external_workspace_id="T123",
        external_identity_id="B123",
    )
    kwargs = dict(
        external_identity_record_id=identity["external_identity_record_id"],
        external_event_id="Ev-no-grant",
        external_resource_id="C123",
        target_agent_id="agent_operator",
        instruction="Read this without an active grant.",
        payload={"text": "should remain auditable"},
    )

    ignored = service.ingest_event(**kwargs)
    replay = service.ingest_event(**kwargs)

    assert ignored.event["status"] == "ignored"
    assert ignored.submission is None
    assert ignored.replayed is False
    assert replay.event["status"] == "ignored"
    assert replay.submission is None
    assert replay.replayed is True
    assert work.list_ready_deliveries("agent_operator") == []
    assert len(service.list_events()) == 1


def test_revoke_fences_outbound_and_regrant_can_retry_same_delivery(fixture):
    _database, work, service, sent = fixture
    identity, grant_record = _grant(service)
    event = service.ingest_event(
        external_identity_record_id=identity["external_identity_record_id"],
        external_event_id="Ev456",
        external_resource_id="C123",
        external_thread_id="171000.2",
        target_agent_id="agent_operator",
        instruction="Answer the customer.",
        payload={},
    )
    leased = work.lease_delivery(event.submission.deliveries[0]["delivery_id"], "worker")
    service.coordinator.start_delivery(
        leased,
        run_id="run_connector_1",
        agent_revision_id="agent_revision_operator",
        sandbox_id="sandbox_operator",
    )
    queued = service.queue_result(run_id="run_connector_1", text="Here is the answer.")
    service.revoke_grant(grant_record["grant"]["connector_grant_id"])

    blocked = service.send_outbox(queued["connector_delivery_id"])
    assert blocked["status"] == "failed"
    assert sent == []

    service.grant_agent(
        external_identity_record_id=identity["external_identity_record_id"],
        agent_id="agent_operator",
        actions=("connector.read", "connector.send"),
        resource_ids=("C123",),
    )
    retried = service.send_outbox(queued["connector_delivery_id"])
    assert retried["status"] == "sent"
    assert sent == [("slack", "Here is the answer.", "C123", "171000.2")]
    assert service.list_events()[0]["status"] == "completed"


def test_connector_result_replay_reuses_one_outbox_across_run_retries(fixture):
    _database, work, service, _sent = fixture
    identity, _grant_record = _grant(service)
    event = service.ingest_event(
        external_identity_record_id=identity["external_identity_record_id"],
        external_event_id="Ev-retry-result",
        external_resource_id="C123",
        external_thread_id="171000.retry",
        target_agent_id="agent_operator",
        instruction="Answer once.",
        payload={},
    )
    leased = work.lease_delivery(event.submission.deliveries[0]["delivery_id"], "worker-1")
    service.coordinator.start_delivery(
        leased,
        run_id="run_connector_retry_1",
        agent_revision_id="agent_revision_operator",
        sandbox_id="sandbox_operator",
    )
    first = service.queue_result(run_id="run_connector_retry_1", text="One answer.")
    service.coordinator.settle_run(
        "run_connector_retry_1",
        "failed",
        lease_token=leased["lease_token"],
        retry_policy={"max_attempts": 2, "base_delay_seconds": 1},
    )

    retry_lease = work.lease_delivery(
        event.submission.deliveries[0]["delivery_id"], "worker-2"
    )
    service.coordinator.start_delivery(
        retry_lease,
        run_id="run_connector_retry_2",
        agent_revision_id="agent_revision_operator",
        sandbox_id="sandbox_operator",
    )
    replay = service.queue_result(run_id="run_connector_retry_2", text="One answer.")

    assert replay["connector_delivery_id"] == first["connector_delivery_id"]
    assert len(service.list_outbox()) == 1


def test_outbox_sending_boundary_blocks_a_second_provider_call(fixture):
    _database, work, service, sent = fixture
    identity, _grant_record = _grant(service)
    event = service.ingest_event(
        external_identity_record_id=identity["external_identity_record_id"],
        external_event_id="Ev-send-claim",
        external_resource_id="C123",
        target_agent_id="agent_operator",
        instruction="Send once.",
        payload={},
    )
    leased = work.lease_delivery(event.submission.deliveries[0]["delivery_id"], "worker")
    service.coordinator.start_delivery(
        leased,
        run_id="run_connector_send_claim",
        agent_revision_id="agent_revision_operator",
        sandbox_id="sandbox_operator",
    )
    queued = service.queue_result(run_id="run_connector_send_claim", text="Exactly once.")

    claimed = service.repository.claim_outbox(queued["connector_delivery_id"])
    assert claimed is not None and claimed["status"] == "sending"

    observed = service.send_outbox(queued["connector_delivery_id"])

    assert observed["status"] == "sending"
    assert sent == []

    service.repository.transition_outbox(
        queued["connector_delivery_id"], "failed", last_error="provider call interrupted"
    )
    retried = service.send_outbox(queued["connector_delivery_id"])
    assert retried["status"] == "sent"
    assert sent == [("slack", "Exactly once.", "C123", "")]


def test_wrong_resource_and_wrong_requester_are_rejected(fixture):
    _database, _work, service, _sent = fixture
    identity, _grant_record = _grant(service, actions=("connector.read",))

    with pytest.raises(ConnectorAuthorityError):
        service.authorize(
            external_identity_record_id=identity["external_identity_record_id"],
            agent_id="agent_operator",
            requester_actor_id="actor_other",
            resource_id="C123",
            action="connector.read",
        )
    with pytest.raises(ConnectorAuthorityError):
        service.authorize(
            external_identity_record_id=identity["external_identity_record_id"],
            agent_id="agent_operator",
            requester_actor_id=OWNER.id,
            resource_id="C999",
            action="connector.read",
        )


def test_health_reconnect_fences_transport_and_outbound_is_audited(fixture):
    _database, work, service, sent = fixture
    events = []
    service.event_sink = events.append
    identity, _grant_record = _grant(service)
    changed = service.update_identity_health(
        identity["external_identity_record_id"],
        "offline",
        error_detail="provider timeout",
        reconnect=True,
    )
    assert changed["health_status"] == "offline"
    assert changed["reconnect_count"] == 1
    assert changed["last_health_error"] == "provider timeout"

    event = service.ingest_event(
        external_identity_record_id=identity["external_identity_record_id"],
        external_event_id="Ev-health",
        external_resource_id="C123",
        external_thread_id="171000.health",
        target_agent_id="agent_operator",
        instruction="Answer the customer.",
        payload={},
    )
    leased = work.lease_delivery(event.submission.deliveries[0]["delivery_id"], "worker")
    service.coordinator.start_delivery(
        leased,
        run_id="run_connector_health",
        agent_revision_id="agent_revision_operator",
        sandbox_id="sandbox_operator",
    )
    queued = service.queue_result(run_id="run_connector_health", text="Health-safe reply")

    failed = service.send_outbox(queued["connector_delivery_id"])
    assert failed["status"] == "failed"
    assert sent == []
    failure_event = next(item for item in events if item["event_type"] == "connector.outbound_failed")
    assert failure_event["payload"]["requester_actor_id"] == OWNER.id
    assert failure_event["payload"]["resource_id"] == "C123"
    assert "Health-safe reply" not in str(failure_event["payload"])

    service.update_identity_health(
        identity["external_identity_record_id"], "healthy"
    )
    retried = service.send_outbox(queued["connector_delivery_id"])
    assert retried["status"] == "sent"
    assert sent == [("slack", "Health-safe reply", "C123", "171000.health")]
    assert any(item["event_type"] == "connector.outbound_sent" for item in events)
