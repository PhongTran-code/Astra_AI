"""Canonical Astra Team domain contracts and transition invariants."""

from dataclasses import FrozenInstanceError

import pytest

from coworker.teams import (
    AGENT_EDGES,
    APPROVAL_EDGES,
    DELIVERY_EDGES,
    RUN_EDGES,
    SANDBOX_EDGES,
    ActorKind,
    ActorRef,
    AgentStatus,
    ApprovalStatus,
    ContractError,
    DeliveryStatus,
    DomainEvent,
    HandoffContract,
    InvalidTransitionError,
    ResourceRef,
    RunStatus,
    SandboxStatus,
    WorkBudget,
    WorkRequest,
    WorkSource,
    can_transition,
    new_domain_id,
    require_transition,
)


NOW = "2026-08-29T12:00:00+00:00"
REQUESTER = ActorRef(id="human_owner", kind=ActorKind.HUMAN, display_name="Owner")


def request(**overrides):
    values = {
        "request_id": "request_1",
        "workspace_id": "workspace_1",
        "source": WorkSource.GROUP_MESSAGE,
        "source_ref": ResourceRef(kind="message", id="message_1"),
        "requester": REQUESTER,
        "target_agent_ids": ("agent_research", "agent_builder"),
        "instruction": "Research and implement the requested change.",
        "idempotency_key": "message_1:dispatch",
        "correlation_id": "trace_1",
        "conversation_id": "conversation_1",
        "expected_output": "A verified implementation.",
        "context_refs": (ResourceRef(kind="thread", id="thread_1"),),
        "input_artifact_ids": ("artifact_1",),
        "permission_scope": {"connectors": ["github"], "write": True},
        "budget": WorkBudget(max_tokens=10_000, max_fanout=3),
        "created_at": NOW,
    }
    values.update(overrides)
    return WorkRequest(**values)


def test_domain_id_is_prefixed_and_path_safe():
    value = new_domain_id("request")
    assert value.startswith("request_")
    assert "/" not in value
    assert len(value) == len("request_") + 32


@pytest.mark.parametrize("prefix", ["", "Request", "bad-prefix", "../escape"])
def test_domain_id_rejects_invalid_prefix(prefix):
    with pytest.raises(ContractError, match="id prefix"):
        new_domain_id(prefix)


def test_actor_ref_normalizes_and_round_trips():
    actor = ActorRef(id="agent_1", kind="agent", display_name="  Researcher  ")
    assert actor.kind is ActorKind.AGENT
    assert actor.display_name == "Researcher"
    assert ActorRef.from_dict(actor.to_dict()) == actor


@pytest.mark.parametrize("bad_id", ["", "../escape", "has space", "a/b"])
def test_actor_ref_rejects_unsafe_ids(bad_id):
    with pytest.raises(ContractError, match="actor id"):
        ActorRef(id=bad_id, kind=ActorKind.AGENT)


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_tokens", 0),
        ("max_cost_micros", -1),
        ("max_model_calls", True),
        ("max_wall_seconds", 1.5),
    ],
)
def test_budget_requires_positive_integer_caps(field, value):
    with pytest.raises(ContractError, match=field):
        WorkBudget(**{field: value})


def test_budget_none_means_inherit_and_round_trips():
    budget = WorkBudget(max_tokens=2000, max_tool_calls=8)
    assert budget.max_model_calls is None
    assert WorkBudget.from_dict(budget.to_dict()) == budget


def test_work_request_normalizes_targets_and_is_immutable():
    work = request(target_agent_ids=("agent_research", "agent_research", "agent_builder"))
    assert work.target_agent_ids == ("agent_research", "agent_builder")
    with pytest.raises(TypeError):
        work.permission_scope["write"] = False
    with pytest.raises(AttributeError):
        work.permission_scope["connectors"].append("slack")
    with pytest.raises(FrozenInstanceError):
        work.priority = 10


def test_work_request_round_trip_does_not_share_mutable_policy_data():
    work = request()
    raw = work.to_dict()
    restored = WorkRequest.from_dict(raw)
    raw["permission_scope"]["connectors"].append("slack")
    assert restored == work
    assert list(restored.permission_scope["connectors"]) == ["github"]


@pytest.mark.parametrize(
    "source,source_kind,extra",
    [
        (
            WorkSource.DIRECT_MESSAGE,
            "message",
            {"conversation_id": "dm_1", "target_agent_ids": ("agent_1",)},
        ),
        (
            WorkSource.GROUP_MESSAGE,
            "message",
            {"conversation_id": "group_1"},
        ),
        (WorkSource.TASK, "task", {"task_id": "task_1"}),
        (WorkSource.AUTOMATION, "automation", {}),
        (WorkSource.CONNECTOR, "connector_event", {}),
        (WorkSource.HANDOFF, "handoff", {"task_id": "task_1"}),
    ],
)
def test_work_request_accepts_every_canonical_source(source, source_kind, extra):
    extra = dict(extra)
    work = request(
        source=source,
        source_ref=ResourceRef(kind=source_kind, id="source_1"),
        conversation_id=extra.pop("conversation_id", None),
        task_id=extra.pop("task_id", None),
        **extra,
    )
    assert work.source is source


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"target_agent_ids": 7}, "must be a sequence"),
        ({"permission_scope": []}, "must be an object"),
        ({"permission_scope": {"limit": float("inf")}}, "finite JSON numbers"),
        ({"permission_scope": {"bad": object()}}, "JSON-compatible"),
    ],
)
def test_work_request_rejects_malformed_collection_values(changes, match):
    with pytest.raises(ContractError, match=match):
        request(**changes)


def test_direct_message_requires_exactly_one_target():
    with pytest.raises(ContractError, match="exactly one"):
        request(
            source=WorkSource.DIRECT_MESSAGE,
            source_ref=ResourceRef(kind="message", id="message_1"),
            conversation_id="dm_1",
        )


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"conversation_id": None}, "conversation id"),
        (
            {
                "source": WorkSource.TASK,
                "source_ref": ResourceRef(kind="task", id="task_1"),
                "conversation_id": None,
                "task_id": None,
            },
            "task id",
        ),
        (
            {
                "source": WorkSource.CONNECTOR,
                "source_ref": ResourceRef(kind="message", id="message_1"),
                "conversation_id": None,
            },
            "connector_event",
        ),
    ],
)
def test_work_request_rejects_source_shape_mismatch(changes, match):
    with pytest.raises(ContractError, match=match):
        request(**changes)


def test_work_request_requires_timezone_aware_timestamp():
    with pytest.raises(ContractError, match="timezone"):
        request(created_at="2026-08-29T12:00:00")


def test_handoff_requires_distinct_agents_and_dedupes_artifacts():
    handoff = HandoffContract(
        handoff_id="handoff_1",
        workspace_id="workspace_1",
        task_id="task_1",
        sender_agent_id="agent_builder",
        recipient_agent_id="agent_reviewer",
        purpose="Verify the implementation.",
        expected_output="Pass or fail with evidence.",
        requester=REQUESTER,
        idempotency_key="task_1:builder:reviewer",
        correlation_id="trace_1",
        artifact_ids=("artifact_1", "artifact_1", "artifact_2"),
        created_at=NOW,
    )
    assert handoff.artifact_ids == ("artifact_1", "artifact_2")
    assert HandoffContract.from_dict(handoff.to_dict()) == handoff


def test_handoff_rejects_self_handoff():
    with pytest.raises(ContractError, match="must differ"):
        HandoffContract(
            handoff_id="handoff_1",
            workspace_id="workspace_1",
            task_id="task_1",
            sender_agent_id="agent_1",
            recipient_agent_id="agent_1",
            purpose="Review",
            expected_output="Evidence",
            requester=REQUESTER,
            idempotency_key="task_1:self",
            correlation_id="trace_1",
            created_at=NOW,
        )


def test_handoff_blocks_loops_and_budget_overflow():
    common = {
        "handoff_id": "handoff_2",
        "workspace_id": "workspace_1",
        "task_id": "task_1",
        "sender_agent_id": "agent_reviewer",
        "recipient_agent_id": "agent_builder",
        "purpose": "Return findings",
        "expected_output": "A corrected artifact",
        "requester": REQUESTER,
        "idempotency_key": "task_1:reviewer:builder",
        "correlation_id": "trace_1",
        "depth": 2,
        "agent_path": ("agent_builder", "agent_reviewer"),
    }
    with pytest.raises(ContractError, match="already appears"):
        HandoffContract(**common)
    with pytest.raises(ContractError, match="exceeds its budget"):
        HandoffContract(
            **{**common, "recipient_agent_id": "agent_operator"},
            budget=WorkBudget(max_handoffs=1),
        )


def test_handoff_path_must_end_at_sender_and_match_depth():
    common = {
        "handoff_id": "handoff_2",
        "workspace_id": "workspace_1",
        "task_id": "task_1",
        "sender_agent_id": "agent_reviewer",
        "recipient_agent_id": "agent_operator",
        "purpose": "Escalate verification",
        "expected_output": "Operational evidence",
        "requester": REQUESTER,
        "idempotency_key": "task_1:reviewer:operator",
        "correlation_id": "trace_1",
        "depth": 2,
    }
    with pytest.raises(ContractError, match="end with the sender"):
        HandoffContract(**common, agent_path=("agent_builder", "agent_other"))
    with pytest.raises(ContractError, match="equal the number"):
        HandoffContract(**common, agent_path=("agent_reviewer",))


def test_handoff_path_rejects_repeated_agents_instead_of_hiding_a_loop():
    with pytest.raises(ContractError, match="must not contain duplicates"):
        HandoffContract(
            handoff_id="handoff_3",
            workspace_id="workspace_1",
            task_id="task_1",
            sender_agent_id="agent_reviewer",
            recipient_agent_id="agent_operator",
            purpose="Escalate verification",
            expected_output="Operational evidence",
            requester=REQUESTER,
            idempotency_key="task_1:reviewer:operator",
            correlation_id="trace_1",
            depth=3,
            agent_path=("agent_builder", "agent_builder", "agent_reviewer"),
        )


def test_domain_event_round_trip_and_payload_isolation():
    event = DomainEvent(
        event_id="event_1",
        event_type="message.created",
        workspace_id="workspace_1",
        actor_id="human_owner",
        aggregate_type="conversation",
        aggregate_id="conversation_1",
        correlation_id="trace_1",
        payload={"mentions": ["agent_1"]},
        sequence=1,
        occurred_at=NOW,
    )
    raw = event.to_dict()
    restored = DomainEvent.from_dict(raw)
    raw["payload"]["mentions"].append("agent_2")
    assert restored == event
    assert list(restored.payload["mentions"]) == ["agent_1"]
    with pytest.raises(TypeError):
        event.payload["extra"] = True
    with pytest.raises(AttributeError):
        event.payload["mentions"].append("agent_3")


@pytest.mark.parametrize("raw", [None, [], "event"])
def test_domain_event_from_dict_rejects_non_objects(raw):
    with pytest.raises(ContractError, match="must be an object"):
        DomainEvent.from_dict(raw)


@pytest.mark.parametrize("sequence", [0, -1, True, 1.5])
def test_domain_event_requires_positive_sequence(sequence):
    with pytest.raises(ContractError, match="sequence"):
        DomainEvent(
            event_id="event_1",
            event_type="run.started",
            workspace_id="workspace_1",
            actor_id="system",
            aggregate_type="run",
            aggregate_id="run_1",
            correlation_id="trace_1",
            sequence=sequence,
            occurred_at=NOW,
        )


@pytest.mark.parametrize(
    "factory,raw,match",
    [
        (WorkBudget.from_dict, {"unknown": 1}, "unknown fields"),
        (
            WorkRequest.from_dict,
            {**request().to_dict(), "permission_scope": []},
            "permission_scope must be an object",
        ),
        (
            HandoffContract.from_dict,
            {
                **HandoffContract(
                    handoff_id="handoff_4",
                    workspace_id="workspace_1",
                    task_id="task_1",
                    sender_agent_id="agent_builder",
                    recipient_agent_id="agent_reviewer",
                    purpose="Review",
                    expected_output="Evidence",
                    requester=REQUESTER,
                    idempotency_key="task_1:builder:reviewer",
                    correlation_id="trace_1",
                ).to_dict(),
                "artifact_ids": "artifact_1",
            },
            "artifact_ids must be a sequence",
        ),
        (
            DomainEvent.from_dict,
            {
                **DomainEvent(
                    event_id="event_2",
                    event_type="run.started",
                    workspace_id="workspace_1",
                    actor_id="system",
                    aggregate_type="run",
                    aggregate_id="run_1",
                    correlation_id="trace_1",
                    occurred_at=NOW,
                ).to_dict(),
                "payload": [],
            },
            "payload must be an object",
        ),
    ],
)
def test_contract_deserialization_rejects_invalid_shapes(factory, raw, match):
    with pytest.raises(ContractError, match=match):
        factory(raw)


@pytest.mark.parametrize(
    "current,target,edges",
    [
        (AgentStatus.PROVISIONING, AgentStatus.READY, AGENT_EDGES),
        (SandboxStatus.READY, SandboxStatus.BUSY, SANDBOX_EDGES),
        (DeliveryStatus.PENDING, DeliveryStatus.LEASED, DELIVERY_EDGES),
        (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL, RUN_EDGES),
        (ApprovalStatus.PENDING, ApprovalStatus.APPROVED, APPROVAL_EDGES),
    ],
)
def test_declared_transitions_are_allowed(current, target, edges):
    assert can_transition(current, target, edges)
    require_transition(current, target, edges)


@pytest.mark.parametrize(
    "current,target,edges",
    [
        (AgentStatus.PROVISIONING, AgentStatus.WORKING, AGENT_EDGES),
        (SandboxStatus.PROVISIONING, SandboxStatus.BUSY, SANDBOX_EDGES),
        (DeliveryStatus.ACKNOWLEDGED, DeliveryStatus.PENDING, DELIVERY_EDGES),
        (RunStatus.SUCCEEDED, RunStatus.RUNNING, RUN_EDGES),
        (ApprovalStatus.DENIED, ApprovalStatus.APPROVED, APPROVAL_EDGES),
    ],
)
def test_undeclared_transitions_are_rejected(current, target, edges):
    assert not can_transition(current, target, edges)
    with pytest.raises(InvalidTransitionError, match="illegal transition"):
        require_transition(current, target, edges)
