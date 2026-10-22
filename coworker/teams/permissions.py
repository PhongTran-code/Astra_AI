"""Requester-aware Team authorization and governance policy.

The legacy :mod:`coworker.permissions` module remains the tool-call safety floor.  This
module adds the Team-specific authority intersection around it.  It is intentionally
side-effect free: persistence, audit writes, and approval UI are callers' concerns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional

from .contracts import ActorKind, ActorRef, AgentRole, ContractError, ResourceRef, WorkRequest


class WorkspaceRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class PolicyError(ContractError):
    """A Team policy input is malformed or attempts to cross an authority boundary."""


class PromptPolicyMutationError(PolicyError):
    """An agent instruction attempts to rewrite policy instead of doing work."""


TEAM_ACTIONS = frozenset(
    {
        "agent.read",
        "agent.manage",
        "conversation.read",
        "conversation.send",
        "conversation.manage",
        "task.read",
        "task.create",
        "task.assign",
        "task.transition",
        "task.review",
        "task.cancel",
        "run.read",
        "run.execute",
        "run.cancel",
        "sandbox.read",
        "sandbox.write",
        "sandbox.mount",
        "sandbox.network",
        "computer.read",
        "computer.control",
        "artifact.read",
        "artifact.publish",
        "connector.read",
        "connector.send",
        "connector.write",
        "approval.read",
        "approval.decide",
        "grant.read",
        "grant.manage",
        "audit.read",
        "workspace.manage",
    }
)

READ_ACTIONS = frozenset(action for action in TEAM_ACTIONS if action.endswith(".read"))

PLATFORM_CEILING = TEAM_ACTIONS

WORKSPACE_ROLE_ACTIONS: dict[WorkspaceRole, frozenset[str]] = {
    WorkspaceRole.OWNER: TEAM_ACTIONS,
    WorkspaceRole.ADMIN: TEAM_ACTIONS - frozenset({"workspace.manage", "grant.manage", "agent.manage"}),
    WorkspaceRole.MEMBER: frozenset(
        {
            "agent.read",
            "conversation.read",
            "conversation.send",
            "task.read",
            "task.create",
            "task.assign",
            "task.transition",
            "task.review",
            "run.read",
            "run.execute",
            "run.cancel",
            "sandbox.read",
            "computer.read",
            "computer.control",
            "artifact.read",
            "artifact.publish",
            "connector.read",
            "connector.send",
            "approval.read",
            "grant.read",
            "audit.read",
        }
    ),
    WorkspaceRole.VIEWER: frozenset(READ_ACTIONS),
}

AGENT_ROLE_ACTIONS: dict[AgentRole, frozenset[str]] = {
    AgentRole.SUPERVISOR: frozenset(
        {
            "agent.read",
            "conversation.read",
            "conversation.send",
            "task.read",
            "task.create",
            "task.assign",
            "task.transition",
            "task.review",
            "run.read",
            "run.execute",
            "run.cancel",
            "sandbox.read",
            "computer.read",
            "computer.control",
            "artifact.read",
            "artifact.publish",
            "connector.read",
            "connector.send",
            "approval.read",
            "grant.read",
            "audit.read",
        }
    ),
    AgentRole.EXPLORER: frozenset(
        {
            "agent.read",
            "conversation.read",
            "conversation.send",
            "task.read",
            "task.create",
            "run.read",
            "run.execute",
            "sandbox.read",
            "sandbox.write",
            "computer.read",
            "computer.control",
            "artifact.read",
            "artifact.publish",
            "connector.read",
            "audit.read",
        }
    ),
    AgentRole.BUILDER: frozenset(
        {
            "agent.read",
            "conversation.read",
            "conversation.send",
            "task.read",
            "task.assign",
            "task.transition",
            "run.read",
            "run.execute",
            "run.cancel",
            "sandbox.read",
            "sandbox.write",
            "sandbox.mount",
            "computer.read",
            "computer.control",
            "artifact.read",
            "artifact.publish",
            "connector.read",
            "connector.send",
            "audit.read",
        }
    ),
    AgentRole.VERIFIER: frozenset(
        {
            "agent.read",
            "conversation.read",
            "conversation.send",
            "task.read",
            "task.review",
            "run.read",
            "run.execute",
            "sandbox.read",
            "computer.read",
            "artifact.read",
            "connector.read",
            "audit.read",
        }
    ),
    AgentRole.OPERATOR: frozenset(
        {
            "agent.read",
            "conversation.read",
            "conversation.send",
            "task.read",
            "run.read",
            "run.execute",
            "run.cancel",
            "sandbox.read",
            "sandbox.mount",
            "sandbox.network",
            "computer.read",
            "computer.control",
            "artifact.read",
            "artifact.publish",
            "connector.read",
            "connector.send",
            "connector.write",
            "approval.read",
            "audit.read",
        }
    ),
}


@dataclass(frozen=True)
class ConnectorGrant:
    """Exact connector authority; no wildcard resource is accepted."""

    grant_id: str
    workspace_id: str
    requester_actor_id: str
    agent_id: str
    connector_kind: str
    resource_id: str
    actions: frozenset[str]
    expires_at: Optional[str] = None
    revoked: bool = False

    def __post_init__(self) -> None:
        for name in (
            "grant_id",
            "workspace_id",
            "requester_actor_id",
            "agent_id",
            "connector_kind",
            "resource_id",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value or value == "*":
                raise PolicyError(f"{name} is required and cannot be wildcarded")
            object.__setattr__(self, name, value)
        if isinstance(self.actions, (str, bytes)):
            raise PolicyError("connector grant actions must be a sequence")
        actions = frozenset(str(action).strip() for action in self.actions if str(action).strip())
        if not actions or not actions <= {"connector.read", "connector.send", "connector.write"}:
            raise PolicyError("connector grant contains unsupported actions")
        object.__setattr__(self, "actions", actions)
        if self.expires_at is not None:
            _parse_timestamp(self.expires_at, "grant expiry")
        if not isinstance(self.revoked, bool):
            raise PolicyError("grant revoked must be boolean")

    def matches(self, *, workspace_id: str, requester_actor_id: str, agent_id: str, connector_kind: str, resource_id: str, now: Optional[str] = None) -> bool:
        if self.revoked:
            return False
        if (
            self.workspace_id != workspace_id
            or self.requester_actor_id != requester_actor_id
            or self.agent_id != agent_id
            or self.connector_kind != connector_kind
            or self.resource_id != resource_id
        ):
            return False
        return self.expires_at is None or _parse_timestamp(self.expires_at, "grant expiry") > _parse_timestamp(now or _utc_now(), "current time")


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    workspace_id: str
    requester_actor_id: str
    agent_id: str
    action: str
    resource: ResourceRef
    decided_by_actor_id: str
    status: str = "pending"
    expires_at: Optional[str] = None
    revoked: bool = False

    def __post_init__(self) -> None:
        for name in (
            "approval_id",
            "workspace_id",
            "requester_actor_id",
            "agent_id",
            "decided_by_actor_id",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise PolicyError(f"{name} is required")
            object.__setattr__(self, name, value)
        if not isinstance(self.resource, ResourceRef):
            raise PolicyError("approval resource must be a ResourceRef")
        if self.action not in TEAM_ACTIONS:
            raise PolicyError("approval action is not supported")
        if self.status not in {"pending", "approved", "denied", "expired", "canceled"}:
            raise PolicyError("approval status is not supported")
        if self.expires_at is not None:
            _parse_timestamp(self.expires_at, "approval expiry")

    def matches(self, *, workspace_id: str, requester_actor_id: str, agent_id: str, action: str, resource: ResourceRef, now: Optional[str] = None) -> bool:
        if self.revoked or self.status != "approved":
            return False
        if (
            self.workspace_id != workspace_id
            or self.requester_actor_id != requester_actor_id
            or self.agent_id != agent_id
            or self.action != action
            or self.resource != resource
        ):
            return False
        return self.expires_at is None or _parse_timestamp(self.expires_at, "approval expiry") > _parse_timestamp(now or _utc_now(), "current time")


@dataclass(frozen=True)
class SandboxPolicy:
    """Run-time sandbox capabilities, constrained independently from grants."""

    actions: frozenset[str] = field(default_factory=lambda: frozenset({"sandbox.read"}))
    allowed_mounts: frozenset[str] = frozenset()
    network_domains: frozenset[str] = frozenset()
    max_tokens: Optional[int] = None
    max_tool_calls: Optional[int] = None
    max_wall_seconds: Optional[int] = None

    def __post_init__(self) -> None:
        actions = frozenset(str(action).strip() for action in self.actions if str(action).strip())
        if not actions <= {"sandbox.read", "sandbox.write", "sandbox.mount", "sandbox.network", "run.execute"}:
            raise PolicyError("sandbox policy contains unsupported actions")
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "allowed_mounts", frozenset(_safe_scope(value, "mount") for value in self.allowed_mounts))
        object.__setattr__(self, "network_domains", frozenset(_safe_scope(value, "network domain") for value in self.network_domains))
        for name in ("max_tokens", "max_tool_calls", "max_wall_seconds"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise PolicyError(f"{name} must be a positive integer or null")


@dataclass(frozen=True)
class PermissionContext:
    """All layers used to calculate one effective authority intersection."""

    workspace_id: str
    requester: ActorRef
    requester_role: WorkspaceRole
    agent_id: str
    agent_role: AgentRole
    plugin_actions: frozenset[str] = TEAM_ACTIONS
    conversation_actions: frozenset[str] = TEAM_ACTIONS
    task_actions: frozenset[str] = TEAM_ACTIONS
    sandbox: SandboxPolicy = field(default_factory=SandboxPolicy)
    agent_actor_id: Optional[str] = None
    connector_grants: tuple[ConnectorGrant, ...] = ()
    approvals: tuple[ApprovalDecision, ...] = ()
    task_id: Optional[str] = None
    conversation_id: Optional[str] = None
    connector_kind: Optional[str] = None
    connector_resource_id: Optional[str] = None
    correlation_id: Optional[str] = None
    now: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.requester, ActorRef):
            raise PolicyError("requester must be an ActorRef")
        for name in ("workspace_id", "agent_id"):
            _safe_scope(getattr(self, name), name)
        object.__setattr__(self, "requester_role", _as_enum(WorkspaceRole, self.requester_role, "workspace role"))
        object.__setattr__(self, "agent_role", _as_enum(AgentRole, self.agent_role, "agent role"))
        for name in ("plugin_actions", "conversation_actions", "task_actions"):
            actions = frozenset(str(action).strip() for action in getattr(self, name) if str(action).strip())
            if not actions <= TEAM_ACTIONS:
                raise PolicyError(f"{name} contains unsupported actions")
            object.__setattr__(self, name, actions)
        if not isinstance(self.sandbox, SandboxPolicy):
            raise PolicyError("sandbox must be a SandboxPolicy")
        if self.task_id is not None:
            _safe_scope(self.task_id, "task id")
        if self.agent_actor_id is not None:
            _safe_scope(self.agent_actor_id, "agent actor id")
        if self.conversation_id is not None:
            _safe_scope(self.conversation_id, "conversation id")
        if self.connector_kind is not None:
            _safe_scope(self.connector_kind, "connector kind")
        if self.connector_resource_id is not None:
            _safe_scope(self.connector_resource_id, "connector resource id")
        if self.correlation_id is not None:
            _safe_scope(self.correlation_id, "correlation id")


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    action: str
    reason: str
    missing_layers: tuple[str, ...] = ()
    requires_approval: bool = False
    human_only: bool = False


@dataclass(frozen=True)
class PolicyAudit:
    """Safe attribution emitted for every policy verdict."""

    workspace_id: str
    actor_id: str
    requester_actor_id: str
    agent_id: str
    action: str
    allowed: bool
    reason: str
    scope: Mapping[str, str] = field(default_factory=dict)
    requires_approval: bool = False
    occurred_at: str = field(default_factory=lambda: _utc_now())


class TeamPermissionService:
    """Single backend entry point for Team action and WorkRequest authorization."""

    def __init__(self, *, audit_sink: Optional[Callable[[PolicyAudit], None]] = None) -> None:
        self.audit_sink = audit_sink

    def authorize(
        self,
        context: PermissionContext,
        action: str,
        *,
        resource: Optional[ResourceRef] = None,
    ) -> PermissionDecision:
        decision = authorize_action(context, action)
        audit = PolicyAudit(
            workspace_id=context.workspace_id,
            actor_id=context.agent_actor_id or context.agent_id,
            requester_actor_id=context.requester.id,
            agent_id=context.agent_id,
            action=decision.action,
            allowed=decision.allowed,
            reason=decision.reason,
            scope={
                **({"conversation_id": context.conversation_id} if context.conversation_id else {}),
                **({"task_id": context.task_id} if context.task_id else {}),
                **({"resource_kind": resource.kind, "resource_id": resource.id} if resource else {}),
            },
            requires_approval=decision.requires_approval,
        )
        if self.audit_sink is not None:
            self.audit_sink(audit)
        return decision

    def authorize_request(
        self,
        request: WorkRequest,
        context: PermissionContext,
        *,
        action: str = "run.execute",
    ) -> PermissionDecision:
        decision = authorize_work_request(request, context, action=action)
        if self.audit_sink is not None:
            self.audit_sink(
                PolicyAudit(
                    workspace_id=context.workspace_id,
                    actor_id=context.agent_actor_id or context.agent_id,
                    requester_actor_id=context.requester.id,
                    agent_id=context.agent_id,
                    action=decision.action,
                    allowed=decision.allowed,
                    reason=decision.reason,
                    scope={
                        "request_id": request.request_id,
                        **({"conversation_id": request.conversation_id} if request.conversation_id else {}),
                        **({"task_id": request.task_id} if request.task_id else {}),
                    },
                    requires_approval=decision.requires_approval,
                )
            )
        return decision

    def authorize_tool(
        self,
        context: PermissionContext,
        action: str,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        engine: Any,
        metadata: Any = None,
        resource: Optional[ResourceRef] = None,
    ) -> PermissionDecision:
        """Apply Team policy and then the existing tool-level hard-deny floor."""

        team_decision = self.authorize(context, action, resource=resource)
        if not team_decision.allowed:
            return team_decision
        legacy = engine.evaluate(tool_name, dict(arguments or {}), metadata)
        if legacy.allowed:
            return team_decision
        return PermissionDecision(
            allowed=False,
            action=action,
            reason=f"tool floor denied: {legacy.reason}",
            missing_layers=("tool_floor",),
            requires_approval=legacy.needs_user,
            human_only=legacy.human_only,
        )


@dataclass(frozen=True)
class CapabilityInstallReview:
    capability_kind: str
    capability_ref: str
    requested_by_actor_id: str
    allowed: bool
    requires_approval: bool
    reason: str


def review_capability_install(
    requested_by: ActorRef,
    capability_kind: str,
    capability_ref: str,
    *,
    trusted_source: bool = False,
    human_approved: bool = False,
) -> CapabilityInstallReview:
    """Require explicit human review before installing executable capabilities."""

    if not isinstance(requested_by, ActorRef):
        raise PolicyError("capability requester must be an ActorRef")
    kind = _safe_scope(capability_kind, "capability kind")
    ref = _safe_scope(capability_ref, "capability reference")
    executable = kind in {"plugin", "tool", "connector", "mcp", "skill"}
    if not executable:
        raise PolicyError(f"unsupported capability kind: {kind}")
    if requested_by.kind is not ActorKind.HUMAN:
        return CapabilityInstallReview(kind, ref, requested_by.id, False, True, "only a human may install capabilities")
    if not trusted_source and not human_approved:
        return CapabilityInstallReview(kind, ref, requested_by.id, False, True, "untrusted capability requires human approval")
    return CapabilityInstallReview(kind, ref, requested_by.id, True, False, "capability install reviewed")


def effective_authority(
    *,
    platform: Iterable[str],
    workspace: Iterable[str],
    requester: Iterable[str],
    agent: Iterable[str],
    plugin: Iterable[str],
    connector: Iterable[str] = TEAM_ACTIONS,
    conversation: Iterable[str] = TEAM_ACTIONS,
    task: Iterable[str] = TEAM_ACTIONS,
    sandbox: Iterable[str] = TEAM_ACTIONS,
    approval: Iterable[str] = TEAM_ACTIONS,
) -> frozenset[str]:
    """Compute the literal policy intersection; unknown actions are discarded."""

    layers = (
        frozenset(platform),
        frozenset(workspace),
        frozenset(requester),
        frozenset(agent),
        frozenset(plugin),
        frozenset(connector),
        frozenset(conversation),
        frozenset(task),
        frozenset(sandbox),
        frozenset(approval),
    )
    return frozenset(set.intersection(*(set(layer) for layer in layers))) & PLATFORM_CEILING


def authorize_action(context: PermissionContext, action: str) -> PermissionDecision:
    """Evaluate a Team action without mutating grants or approvals."""

    action = str(action or "").strip()
    if action not in TEAM_ACTIONS:
        raise PolicyError(f"unsupported Team action: {action!r}")
    workspace = WORKSPACE_ROLE_ACTIONS[context.requester_role]
    requester = _requester_actions(context.requester)
    agent = AGENT_ROLE_ACTIONS[context.agent_role]
    connector = TEAM_ACTIONS
    if action.startswith("connector."):
        connector = frozenset(
            grant_action
            for grant in context.connector_grants
            if context.connector_kind is not None
            and context.connector_resource_id is not None
            and grant.matches(
                workspace_id=context.workspace_id,
                requester_actor_id=context.requester.id,
                agent_id=context.agent_id,
                connector_kind=context.connector_kind,
                resource_id=context.connector_resource_id,
                now=context.now,
            )
            for grant_action in grant.actions
        )
    approval = TEAM_ACTIONS
    approval_match = False
    if action.startswith("connector.") or action in {"sandbox.mount", "sandbox.network", "grant.manage", "agent.manage", "workspace.manage"}:
        approval = frozenset(
            decision.action
            for decision in context.approvals
            if decision.matches(
                workspace_id=context.workspace_id,
                requester_actor_id=context.requester.id,
                agent_id=context.agent_id,
                action=action,
                resource=ResourceRef(
                    context.connector_kind or "team_action",
                    context.connector_resource_id or action,
                ),
                now=context.now,
            )
        )
        approval_match = action in approval
    sandbox = context.sandbox.actions | frozenset({
        action for action in TEAM_ACTIONS if not action.startswith("sandbox.") and action != "run.execute"
    })
    layers = {
        "platform": PLATFORM_CEILING,
        "workspace": workspace,
        "requester": requester,
        "agent_role": agent,
        "plugin": context.plugin_actions,
        "connector_grant": connector,
        "conversation": context.conversation_actions,
        "task": context.task_actions,
        "sandbox": sandbox,
        "approval": approval,
    }
    effective = effective_authority(
        platform=layers["platform"],
        workspace=layers["workspace"],
        requester=layers["requester"],
        agent=layers["agent_role"],
        plugin=layers["plugin"],
        connector=layers["connector_grant"],
        conversation=layers["conversation"],
        task=layers["task"],
        sandbox=layers["sandbox"],
        approval=layers["approval"],
    )
    if action in effective:
        return PermissionDecision(True, action, "allowed by effective authority intersection")
    missing = tuple(name for name, actions in layers.items() if action not in actions)
    needs_approval = bool(
        action.startswith("connector.")
        or action in {"sandbox.mount", "sandbox.network", "grant.manage", "agent.manage", "workspace.manage"}
    ) and not approval_match
    return PermissionDecision(
        False,
        action,
        "action is outside the effective authority intersection",
        missing_layers=missing,
        requires_approval=needs_approval,
        human_only=needs_approval and action in {"grant.manage", "agent.manage", "workspace.manage"},
    )


def authorize_work_request(
    request: WorkRequest,
    context: PermissionContext,
    *,
    action: str = "run.execute",
) -> PermissionDecision:
    """Bind requester, workspace, and target agent before evaluating execution."""

    if not isinstance(request, WorkRequest):
        raise PolicyError("request must be a WorkRequest")
    if request.workspace_id != context.workspace_id or request.requester.id != context.requester.id:
        raise PolicyError("work request requester/workspace does not match authorization context")
    if context.agent_id not in request.target_agent_ids:
        raise PolicyError("work request is not addressed to this agent")
    if request.requester.kind is ActorKind.AGENT and request.requester.id in {
        context.agent_id,
        context.agent_actor_id,
    }:
        raise PolicyError("an agent cannot authorize work as its own requester")
    return authorize_action(context, action)


def reject_prompt_policy_mutation(instruction: str) -> None:
    """Reject natural-language attempts to mutate security policy.

    Policy changes must arrive through structured, human-attributed commands.  This
    detector is deliberately conservative around *policy nouns plus mutation verbs*;
    ordinary work text such as "review the budget" remains valid.
    """

    if not isinstance(instruction, str) or not instruction.strip():
        raise PromptPolicyMutationError("agent instruction is required")
    normalized = re.sub(r"\s+", " ", instruction).strip().lower()
    mutation = r"(?:ignore|override|bypass|change|modify|edit|expand|increase|disable|revoke|grant|reset|rewrite|remove)"
    policy = r"(?:permission|permissions|grant|role|budget|mount|network|approval|sandbox|connector)"
    if re.search(rf"\b{mutation}\b(?:[^.\n]{{0,100}})\b{policy}\b", normalized):
        raise PromptPolicyMutationError("policy changes require a structured human command")
    if re.search(rf"\b{policy}\b(?:[^.\n]{{0,100}})\b{mutation}\b", normalized):
        raise PromptPolicyMutationError("policy changes require a structured human command")


_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization|credential)")
_SECRET_VALUE = re.compile(r"(?i)\b(?:bearer\s+)?[a-z0-9_./+=-]{24,}\b")


def redact_sensitive(value: Any) -> Any:
    """Return a recursively redacted copy suitable for audit/notification payloads."""

    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    return value


def _requester_actions(requester: ActorRef) -> frozenset[str]:
    if requester.kind is ActorKind.SYSTEM:
        return TEAM_ACTIONS
    if requester.kind is ActorKind.AGENT:
        return AGENT_ROLE_ACTIONS[AgentRole.OPERATOR]
    return TEAM_ACTIONS


def _safe_scope(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or text == "*" or "/" in text or "\\" in text:
        raise PolicyError(f"{label} must be a non-empty exact scope")
    return text


def _as_enum(enum_type, value: Any, label: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as error:
        raise PolicyError(f"invalid {label}: {value!r}") from error


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PolicyError(f"{label} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise PolicyError(f"{label} must include a timezone")
    return parsed
