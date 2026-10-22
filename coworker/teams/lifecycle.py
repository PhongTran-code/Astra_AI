"""Canonical lifecycle ownership and transition authority for Astra Team."""

from __future__ import annotations

from enum import Enum
from typing import Mapping, Optional

from .contracts import (
    AGENT_EDGES,
    APPROVAL_EDGES,
    ARTIFACT_EDGES,
    CONVERSATION_EDGES,
    DELIVERY_EDGES,
    RUN_EDGES,
    SANDBOX_EDGES,
    AgentStatus,
    ApprovalStatus,
    ArtifactStatus,
    ConversationStatus,
    ContractError,
    DeliveryStatus,
    InvalidTransitionError,
    RunStatus,
    SandboxStatus,
)
from .model import EDGES as LEGACY_TASK_EDGES
from .model import ItemState


TaskStatus = ItemState
TASK_EDGES: dict[TaskStatus, frozenset[TaskStatus]] = {
    current: frozenset(targets) for current, targets in LEGACY_TASK_EDGES.items()
}


class StateMachine(str, Enum):
    AGENT = "agent"
    SANDBOX = "sandbox"
    DELIVERY = "delivery"
    RUN = "run"
    TASK = "task"
    APPROVAL = "approval"
    CONVERSATION = "conversation"
    ARTIFACT = "artifact"


class StateOwner(str, Enum):
    AGENT_SERVICE = "agent_service"
    SANDBOX_PROVIDER = "sandbox_provider"
    WORK_COORDINATOR = "work_coordinator"
    TASK_SERVICE = "task_service"
    APPROVAL_SERVICE = "approval_service"
    CONVERSATION_SERVICE = "conversation_service"
    ARTIFACT_SERVICE = "artifact_service"


class TransitionAuthority(str, Enum):
    AGENT_OWNER = "agent_owner"
    WORKSPACE_ADMIN = "workspace_admin"
    REQUESTER = "requester"
    COORDINATOR = "coordinator"
    TASK_OWNER = "task_owner"
    TASK_REVIEWER = "task_reviewer"
    APPROVER = "approver"
    RUNTIME = "runtime"
    SANDBOX_PROVIDER = "sandbox_provider"
    AGENT_SERVICE = "agent_service"
    TASK_SERVICE = "task_service"
    APPROVAL_SERVICE = "approval_service"
    CONVERSATION_MEMBER = "conversation_member"
    CONVERSATION_ADMIN = "conversation_admin"
    ARTIFACT_PUBLISHER = "artifact_publisher"
    SYSTEM = "system"


STATE_OWNERS: dict[StateMachine, StateOwner] = {
    StateMachine.AGENT: StateOwner.AGENT_SERVICE,
    StateMachine.SANDBOX: StateOwner.SANDBOX_PROVIDER,
    StateMachine.DELIVERY: StateOwner.WORK_COORDINATOR,
    StateMachine.RUN: StateOwner.WORK_COORDINATOR,
    StateMachine.TASK: StateOwner.TASK_SERVICE,
    StateMachine.APPROVAL: StateOwner.APPROVAL_SERVICE,
    StateMachine.CONVERSATION: StateOwner.CONVERSATION_SERVICE,
    StateMachine.ARTIFACT: StateOwner.ARTIFACT_SERVICE,
}


STATE_EDGES: dict[StateMachine, Mapping[Enum, frozenset[Enum]]] = {
    StateMachine.AGENT: AGENT_EDGES,
    StateMachine.SANDBOX: SANDBOX_EDGES,
    StateMachine.DELIVERY: DELIVERY_EDGES,
    StateMachine.RUN: RUN_EDGES,
    StateMachine.TASK: TASK_EDGES,
    StateMachine.APPROVAL: APPROVAL_EDGES,
    StateMachine.CONVERSATION: CONVERSATION_EDGES,
    StateMachine.ARTIFACT: ARTIFACT_EDGES,
}


def _agent_authorities(_current: Enum, target: Enum) -> frozenset[TransitionAuthority]:
    authorities = {TransitionAuthority.AGENT_SERVICE, TransitionAuthority.SYSTEM}
    if target in {
        AgentStatus.PROVISIONING,
        AgentStatus.PAUSED,
        AgentStatus.ARCHIVED,
    }:
        authorities.update(
            {TransitionAuthority.AGENT_OWNER, TransitionAuthority.WORKSPACE_ADMIN}
        )
    if target in {
        AgentStatus.READY,
        AgentStatus.WORKING,
        AgentStatus.WAITING,
        AgentStatus.OFFLINE,
        AgentStatus.ERROR,
    }:
        authorities.add(TransitionAuthority.RUNTIME)
    return frozenset(authorities)


def _sandbox_authorities(_current: Enum, target: Enum) -> frozenset[TransitionAuthority]:
    authorities = {TransitionAuthority.SANDBOX_PROVIDER, TransitionAuthority.SYSTEM}
    if target in {
        SandboxStatus.PROVISIONING,
        SandboxStatus.SUSPENDED,
        SandboxStatus.ARCHIVED,
    }:
        authorities.add(TransitionAuthority.WORKSPACE_ADMIN)
    return frozenset(authorities)


def _delivery_authorities(_current: Enum, _target: Enum) -> frozenset[TransitionAuthority]:
    return frozenset({TransitionAuthority.COORDINATOR, TransitionAuthority.SYSTEM})


def _run_authorities(_current: Enum, target: Enum) -> frozenset[TransitionAuthority]:
    authorities = {
        TransitionAuthority.RUNTIME,
        TransitionAuthority.COORDINATOR,
        TransitionAuthority.SYSTEM,
    }
    if target is RunStatus.CANCELED:
        authorities.add(TransitionAuthority.REQUESTER)
    if target is RunStatus.RUNNING:
        authorities.add(TransitionAuthority.APPROVAL_SERVICE)
    return frozenset(authorities)


def _task_authorities(current: Enum, target: Enum) -> frozenset[TransitionAuthority]:
    if target is TaskStatus.DONE:
        return frozenset(
            {
                TransitionAuthority.TASK_REVIEWER,
                TransitionAuthority.TASK_SERVICE,
                TransitionAuthority.WORKSPACE_ADMIN,
                TransitionAuthority.SYSTEM,
            }
        )
    if target is TaskStatus.CANCELED or current is TaskStatus.CANCELED:
        return frozenset(
            {
                TransitionAuthority.COORDINATOR,
                TransitionAuthority.TASK_SERVICE,
                TransitionAuthority.WORKSPACE_ADMIN,
                TransitionAuthority.SYSTEM,
            }
        )
    if current is TaskStatus.REVIEW:
        return frozenset(
            {
                TransitionAuthority.TASK_REVIEWER,
                TransitionAuthority.COORDINATOR,
                TransitionAuthority.TASK_SERVICE,
                TransitionAuthority.WORKSPACE_ADMIN,
                TransitionAuthority.SYSTEM,
            }
        )
    return frozenset(
        {
            TransitionAuthority.TASK_OWNER,
            TransitionAuthority.COORDINATOR,
            TransitionAuthority.TASK_SERVICE,
            TransitionAuthority.WORKSPACE_ADMIN,
            TransitionAuthority.SYSTEM,
        }
    )


def _approval_authorities(_current: Enum, target: Enum) -> frozenset[TransitionAuthority]:
    if target in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
        return frozenset(
            {
                TransitionAuthority.APPROVER,
                TransitionAuthority.APPROVAL_SERVICE,
                TransitionAuthority.SYSTEM,
            }
        )
    if target is ApprovalStatus.CANCELED:
        return frozenset(
            {
                TransitionAuthority.REQUESTER,
                TransitionAuthority.APPROVAL_SERVICE,
                TransitionAuthority.SYSTEM,
            }
        )
    return frozenset(
        {TransitionAuthority.APPROVAL_SERVICE, TransitionAuthority.SYSTEM}
    )


def _conversation_authorities(
    _current: Enum, target: Enum
) -> frozenset[TransitionAuthority]:
    if target is ConversationStatus.DELETED:
        return frozenset(
            {
                TransitionAuthority.CONVERSATION_ADMIN,
                TransitionAuthority.WORKSPACE_ADMIN,
                TransitionAuthority.SYSTEM,
            }
        )
    return frozenset(
        {
            TransitionAuthority.CONVERSATION_MEMBER,
            TransitionAuthority.CONVERSATION_ADMIN,
            TransitionAuthority.WORKSPACE_ADMIN,
            TransitionAuthority.SYSTEM,
        }
    )


def _artifact_authorities(_current: Enum, target: Enum) -> frozenset[TransitionAuthority]:
    if target is ArtifactStatus.DELETED:
        return frozenset(
            {
                TransitionAuthority.ARTIFACT_PUBLISHER,
                TransitionAuthority.WORKSPACE_ADMIN,
                TransitionAuthority.SYSTEM,
            }
        )
    return frozenset(
        {
            TransitionAuthority.ARTIFACT_PUBLISHER,
            TransitionAuthority.WORKSPACE_ADMIN,
            TransitionAuthority.SYSTEM,
        }
    )


_AUTHORITY_RESOLVERS = {
    StateMachine.AGENT: _agent_authorities,
    StateMachine.SANDBOX: _sandbox_authorities,
    StateMachine.DELIVERY: _delivery_authorities,
    StateMachine.RUN: _run_authorities,
    StateMachine.TASK: _task_authorities,
    StateMachine.APPROVAL: _approval_authorities,
    StateMachine.CONVERSATION: _conversation_authorities,
    StateMachine.ARTIFACT: _artifact_authorities,
}


def _build_transition_authorities() -> dict[
    StateMachine,
    dict[tuple[Enum, Enum], frozenset[TransitionAuthority]],
]:
    result = {}
    for machine, edges in STATE_EDGES.items():
        resolver = _AUTHORITY_RESOLVERS[machine]
        result[machine] = {
            (current, target): resolver(current, target)
            for current, targets in edges.items()
            for target in targets
        }
    return result


TRANSITION_AUTHORITIES = _build_transition_authorities()


def transition_authorities(
    machine: StateMachine,
    current: Enum,
    target: Enum,
) -> frozenset[TransitionAuthority]:
    """Return authorities for one legal edge; reject wrong/illegal states."""

    try:
        machine = machine if isinstance(machine, StateMachine) else StateMachine(machine)
    except (TypeError, ValueError) as error:
        raise ContractError(f"invalid state machine: {machine!r}") from error
    edges = STATE_EDGES[machine]
    if current not in edges:
        raise ContractError(f"{current!r} is not a state in the {machine.value} machine")
    if target not in edges[current]:
        raise InvalidTransitionError(
            f"illegal {machine.value} transition {_value(current)} → {_value(target)}"
        )
    return TRANSITION_AUTHORITIES[machine][(current, target)]


def require_transition_authority(
    machine: StateMachine,
    current: Enum,
    target: Enum,
    authority: TransitionAuthority,
) -> None:
    """Require both a legal edge and an authority declared for that edge."""

    try:
        authority = (
            authority
            if isinstance(authority, TransitionAuthority)
            else TransitionAuthority(authority)
        )
    except (TypeError, ValueError) as error:
        raise ContractError(f"invalid transition authority: {authority!r}") from error
    allowed = transition_authorities(machine, current, target)
    if authority not in allowed:
        raise ContractError(
            f"{authority.value} may not perform {_value(current)} → {_value(target)}"
        )


def task_dependencies_ready(dependency_states: Mapping[str, TaskStatus]) -> bool:
    """A task is dispatch-ready only when every declared dependency is done."""

    if not isinstance(dependency_states, Mapping):
        raise ContractError("dependency_states must be an object")
    for task_id, state in dependency_states.items():
        if not str(task_id or "").strip():
            raise ContractError("dependency task id is required")
        try:
            normalized = state if isinstance(state, TaskStatus) else TaskStatus(state)
        except (TypeError, ValueError) as error:
            raise ContractError(f"invalid dependency state for {task_id!r}") from error
        if normalized is not TaskStatus.DONE:
            return False
    return True


def require_task_transition(
    current: TaskStatus,
    target: TaskStatus,
    authority: TransitionAuthority,
    *,
    actor_id: str,
    owner_actor_id: Optional[str],
    reviewer_actor_id: Optional[str] = None,
    dependency_states: Optional[Mapping[str, TaskStatus]] = None,
    protected_review: bool = True,
) -> None:
    """Enforce dependency readiness and independent review on a task edge."""

    try:
        current = current if isinstance(current, TaskStatus) else TaskStatus(current)
        target = target if isinstance(target, TaskStatus) else TaskStatus(target)
    except (TypeError, ValueError) as error:
        raise ContractError("invalid task state") from error
    actor_id = str(actor_id or "").strip()
    if not actor_id:
        raise ContractError("task transition actor id is required")

    require_transition_authority(StateMachine.TASK, current, target, authority)
    if target is TaskStatus.IN_PROGRESS and not task_dependencies_ready(
        dependency_states or {}
    ):
        raise ContractError("task dependencies are incomplete")
    if authority is TransitionAuthority.TASK_OWNER and actor_id != owner_actor_id:
        raise ContractError("task-owner authority does not match the active owner")
    if authority is TransitionAuthority.TASK_REVIEWER:
        if reviewer_actor_id is not None and actor_id != reviewer_actor_id:
            raise ContractError("task-reviewer authority does not match the assigned reviewer")
        if protected_review and actor_id == owner_actor_id:
            raise ContractError("a task owner cannot approve their own protected review")


def _value(value: Enum) -> str:
    return str(getattr(value, "value", value))
