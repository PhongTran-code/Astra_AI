"""Lifecycle ownership, authority coverage, and protected task rules."""

import pytest

from coworker.teams import (
    AGGREGATE_OWNERS,
    AgentStatus,
    AggregateKind,
    ApprovalStatus,
    ArtifactStatus,
    ContractError,
    ConversationStatus,
    InvalidTransitionError,
    RunStatus,
    aggregate_owner,
)
from coworker.teams.lifecycle import (
    STATE_EDGES,
    STATE_OWNERS,
    TRANSITION_AUTHORITIES,
    StateMachine,
    TaskStatus,
    TransitionAuthority,
    require_task_transition,
    require_transition_authority,
    task_dependencies_ready,
    transition_authorities,
)


def test_every_state_machine_has_one_authoritative_owner():
    assert set(STATE_OWNERS) == set(StateMachine)
    assert len(set(STATE_OWNERS.values())) < len(STATE_OWNERS)


def test_every_mutable_aggregate_has_exactly_one_authoritative_owner():
    assert set(AGGREGATE_OWNERS) == set(AggregateKind)
    assert all(owner is aggregate_owner(kind) for kind, owner in AGGREGATE_OWNERS.items())
    with pytest.raises(ContractError, match="invalid aggregate kind"):
        aggregate_owner("team")


def test_every_declared_transition_has_at_least_one_authority():
    expected = {
        machine: {(current, target) for current, targets in edges.items() for target in targets}
        for machine, edges in STATE_EDGES.items()
    }
    assert {machine: set(values) for machine, values in TRANSITION_AUTHORITIES.items()} == expected
    assert all(
        authorities
        for machine in StateMachine
        for authorities in TRANSITION_AUTHORITIES[machine].values()
    )


def test_transition_authority_checks_edge_and_actor_class():
    require_transition_authority(
        StateMachine.RUN,
        RunStatus.RUNNING,
        RunStatus.CANCELED,
        TransitionAuthority.REQUESTER,
    )
    with pytest.raises(ContractError, match="may not perform"):
        require_transition_authority(
            StateMachine.APPROVAL,
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
            TransitionAuthority.REQUESTER,
        )
    with pytest.raises(InvalidTransitionError, match="illegal agent transition"):
        transition_authorities(
            StateMachine.AGENT,
            AgentStatus.PROVISIONING,
            AgentStatus.WORKING,
        )


def test_conversation_and_artifact_archive_delete_lifecycles_are_explicit():
    require_transition_authority(
        StateMachine.CONVERSATION,
        ConversationStatus.ACTIVE,
        ConversationStatus.ARCHIVED,
        TransitionAuthority.CONVERSATION_MEMBER,
    )
    require_transition_authority(
        StateMachine.ARTIFACT,
        ArtifactStatus.PUBLISHED,
        ArtifactStatus.DELETED,
        TransitionAuthority.ARTIFACT_PUBLISHER,
    )
    with pytest.raises(InvalidTransitionError, match="illegal conversation transition"):
        transition_authorities(
            StateMachine.CONVERSATION,
            ConversationStatus.DELETED,
            ConversationStatus.ACTIVE,
        )


def test_machine_rejects_a_state_from_another_machine():
    with pytest.raises(ContractError, match="not a state"):
        transition_authorities(
            StateMachine.AGENT,
            RunStatus.QUEUED,
            RunStatus.RUNNING,
        )


def test_task_dependencies_are_ready_only_when_all_are_done():
    assert task_dependencies_ready({})
    assert task_dependencies_ready({"task_a": TaskStatus.DONE})
    assert not task_dependencies_ready(
        {"task_a": TaskStatus.DONE, "task_b": TaskStatus.REVIEW}
    )
    with pytest.raises(ContractError, match="invalid dependency state"):
        task_dependencies_ready({"task_a": "unknown"})


def test_incomplete_dependencies_prevent_task_start():
    with pytest.raises(ContractError, match="dependencies are incomplete"):
        require_task_transition(
            TaskStatus.OPEN,
            TaskStatus.IN_PROGRESS,
            TransitionAuthority.TASK_OWNER,
            actor_id="agent_builder",
            owner_actor_id="agent_builder",
            dependency_states={"task_research": TaskStatus.REVIEW},
        )


def test_task_owner_can_start_ready_work_and_submit_review():
    require_task_transition(
        TaskStatus.OPEN,
        TaskStatus.IN_PROGRESS,
        TransitionAuthority.TASK_OWNER,
        actor_id="agent_builder",
        owner_actor_id="agent_builder",
        dependency_states={"task_research": TaskStatus.DONE},
    )
    require_task_transition(
        TaskStatus.IN_PROGRESS,
        TaskStatus.REVIEW,
        TransitionAuthority.TASK_OWNER,
        actor_id="agent_builder",
        owner_actor_id="agent_builder",
    )


def test_protected_review_must_be_closed_by_an_independent_reviewer():
    with pytest.raises(ContractError, match="cannot approve their own"):
        require_task_transition(
            TaskStatus.REVIEW,
            TaskStatus.DONE,
            TransitionAuthority.TASK_REVIEWER,
            actor_id="agent_builder",
            owner_actor_id="agent_builder",
            reviewer_actor_id="agent_builder",
        )
    require_task_transition(
        TaskStatus.REVIEW,
        TaskStatus.DONE,
        TransitionAuthority.TASK_REVIEWER,
        actor_id="agent_reviewer",
        owner_actor_id="agent_builder",
        reviewer_actor_id="agent_reviewer",
    )


def test_task_owner_authority_must_match_active_owner():
    with pytest.raises(ContractError, match="does not match"):
        require_task_transition(
            TaskStatus.OPEN,
            TaskStatus.IN_PROGRESS,
            TransitionAuthority.TASK_OWNER,
            actor_id="agent_outsider",
            owner_actor_id="agent_builder",
        )
