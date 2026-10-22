"""Canonical domain contracts shared by Astra Team stores, runtime, and surfaces.

The contracts in this module describe durable identities and transitions. They do
not perform I/O and do not grant authority; callers must still use the permission
engine before creating deliveries or executing work.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Optional, TypeVar


class ContractError(ValueError):
    """A durable Team contract is malformed or violates a domain invariant."""


class InvalidTransitionError(ContractError):
    """A requested state transition is not part of the canonical state machine."""


class ActorKind(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class AgentRole(str, Enum):
    SUPERVISOR = "supervisor"
    EXPLORER = "explorer"
    BUILDER = "builder"
    VERIFIER = "verifier"
    OPERATOR = "operator"


AGENT_AVATAR_COUNT = 30
AGENT_AVATAR_IDS = tuple(f"{index:02d}" for index in range(1, AGENT_AVATAR_COUNT + 1))


def normalize_agent_avatar_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError("agent avatar id must be a string")
    normalized = value.strip()
    if normalized not in AGENT_AVATAR_IDS:
        raise ContractError("agent avatar id must be between 01 and 30")
    return normalized


class ConversationKind(str, Enum):
    DIRECT = "direct"
    GROUP = "group"


class ReplyMode(str, Enum):
    MENTION_ONLY = "mention_only"
    COORDINATOR = "coordinator"
    ALL_MESSAGES = "all_messages"


class WorkSource(str, Enum):
    DIRECT_MESSAGE = "direct_message"
    GROUP_MESSAGE = "group_message"
    TASK = "task"
    AUTOMATION = "automation"
    CONNECTOR = "connector"
    HANDOFF = "handoff"


class AgentStatus(str, Enum):
    PROVISIONING = "provisioning"
    READY = "ready"
    WORKING = "working"
    WAITING = "waiting"
    PAUSED = "paused"
    OFFLINE = "offline"
    ERROR = "error"
    ARCHIVED = "archived"


class SandboxStatus(str, Enum):
    PROVISIONING = "provisioning"
    READY = "ready"
    BUSY = "busy"
    SUSPENDED = "suspended"
    OFFLINE = "offline"
    ERROR = "error"
    ARCHIVED = "archived"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    RETRY_WAIT = "retry_wait"
    DEAD_LETTER = "dead_letter"
    CANCELED = "canceled"


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELED = "canceled"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ArtifactStatus(str, Enum):
    PUBLISHED = "published"
    ARCHIVED = "archived"
    DELETED = "deleted"


class AggregateKind(str, Enum):
    WORKSPACE = "workspace"
    MEMBERSHIP = "membership"
    INVITATION = "invitation"
    AGENT_DEFINITION = "agent_definition"
    AGENT_REVISION = "agent_revision"
    AGENT_INSTALLATION = "agent_installation"
    CONVERSATION = "conversation"
    THREAD = "thread"
    MESSAGE = "message"
    TASK = "task"
    CREW_RUN = "crew_run"
    WORK_REQUEST = "work_request"
    DELIVERY = "delivery"
    RUN = "run"
    SANDBOX = "sandbox"
    ARTIFACT = "artifact"
    APPROVAL = "approval"
    GRANT = "grant"
    MEMORY = "memory"
    CONTEXT_SHARE = "context_share"
    AUTOMATION = "automation"
    CONNECTOR_EVENT = "connector_event"
    NOTIFICATION = "notification"


class AggregateOwner(str, Enum):
    IDENTITY_SERVICE = "identity_service"
    AGENT_CATALOG = "agent_catalog"
    AGENT_SERVICE = "agent_service"
    CONVERSATION_SERVICE = "conversation_service"
    TASK_SERVICE = "task_service"
    WORK_COORDINATOR = "work_coordinator"
    SANDBOX_PROVIDER = "sandbox_provider"
    ARTIFACT_SERVICE = "artifact_service"
    APPROVAL_SERVICE = "approval_service"
    CONTEXT_SERVICE = "context_service"
    AUTOMATION_SCHEDULER = "automation_scheduler"
    CONNECTOR_SERVICE = "connector_service"
    NOTIFICATION_SERVICE = "notification_service"


AGGREGATE_OWNERS: dict[AggregateKind, AggregateOwner] = {
    AggregateKind.WORKSPACE: AggregateOwner.IDENTITY_SERVICE,
    AggregateKind.MEMBERSHIP: AggregateOwner.IDENTITY_SERVICE,
    AggregateKind.INVITATION: AggregateOwner.IDENTITY_SERVICE,
    AggregateKind.AGENT_DEFINITION: AggregateOwner.AGENT_CATALOG,
    AggregateKind.AGENT_REVISION: AggregateOwner.AGENT_CATALOG,
    AggregateKind.AGENT_INSTALLATION: AggregateOwner.AGENT_SERVICE,
    AggregateKind.CONVERSATION: AggregateOwner.CONVERSATION_SERVICE,
    AggregateKind.THREAD: AggregateOwner.CONVERSATION_SERVICE,
    AggregateKind.MESSAGE: AggregateOwner.CONVERSATION_SERVICE,
    AggregateKind.TASK: AggregateOwner.TASK_SERVICE,
    AggregateKind.CREW_RUN: AggregateOwner.WORK_COORDINATOR,
    AggregateKind.WORK_REQUEST: AggregateOwner.WORK_COORDINATOR,
    AggregateKind.DELIVERY: AggregateOwner.WORK_COORDINATOR,
    AggregateKind.RUN: AggregateOwner.WORK_COORDINATOR,
    AggregateKind.SANDBOX: AggregateOwner.SANDBOX_PROVIDER,
    AggregateKind.ARTIFACT: AggregateOwner.ARTIFACT_SERVICE,
    AggregateKind.APPROVAL: AggregateOwner.APPROVAL_SERVICE,
    AggregateKind.GRANT: AggregateOwner.APPROVAL_SERVICE,
    AggregateKind.MEMORY: AggregateOwner.CONTEXT_SERVICE,
    AggregateKind.CONTEXT_SHARE: AggregateOwner.CONTEXT_SERVICE,
    AggregateKind.AUTOMATION: AggregateOwner.AUTOMATION_SCHEDULER,
    AggregateKind.CONNECTOR_EVENT: AggregateOwner.CONNECTOR_SERVICE,
    AggregateKind.NOTIFICATION: AggregateOwner.NOTIFICATION_SERVICE,
}


class DomainIdPrefix(str, Enum):
    WORKSPACE = "workspace"
    ACTOR = "actor"
    MEMBERSHIP = "membership"
    INVITATION = "invitation"
    AGENT_DEFINITION = "agent_definition"
    AGENT_REVISION = "agent_revision"
    AGENT = "agent"
    CONVERSATION = "conversation"
    THREAD = "thread"
    MESSAGE = "message"
    MESSAGE_REVISION = "message_revision"
    TASK = "task"
    CREW_RUN = "crew_run"
    REQUEST = "request"
    DELIVERY = "delivery"
    DELIVERY_ATTEMPT = "delivery_attempt"
    RUN = "run"
    RUN_EVENT = "run_event"
    SANDBOX = "sandbox"
    SNAPSHOT = "snapshot"
    ARTIFACT = "artifact"
    ARTIFACT_REVISION = "artifact_revision"
    APPROVAL = "approval"
    GRANT = "grant"
    MEMORY = "memory"
    CONTEXT_PIN = "context_pin"
    CONTEXT_SHARE = "context_share"
    EVENT = "event"
    COMMAND = "command"
    QUERY = "query"
    NOTIFICATION = "notification"
    AUTOMATION = "automation"
    CONNECTOR_EVENT = "connector_event"
    HANDOFF = "handoff"
    INTEGRATION = "integration"
    AUDIT = "audit"


CANONICAL_ID_PREFIXES = frozenset(prefix.value for prefix in DomainIdPrefix)


AGENT_EDGES: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.PROVISIONING: frozenset(
        {AgentStatus.READY, AgentStatus.ERROR, AgentStatus.ARCHIVED}
    ),
    AgentStatus.READY: frozenset(
        {
            AgentStatus.WORKING,
            AgentStatus.PAUSED,
            AgentStatus.OFFLINE,
            AgentStatus.ERROR,
            AgentStatus.ARCHIVED,
        }
    ),
    AgentStatus.WORKING: frozenset(
        {
            AgentStatus.READY,
            AgentStatus.WAITING,
            AgentStatus.PAUSED,
            AgentStatus.OFFLINE,
            AgentStatus.ERROR,
            AgentStatus.ARCHIVED,
        }
    ),
    AgentStatus.WAITING: frozenset(
        {
            AgentStatus.WORKING,
            AgentStatus.READY,
            AgentStatus.PAUSED,
            AgentStatus.OFFLINE,
            AgentStatus.ERROR,
            AgentStatus.ARCHIVED,
        }
    ),
    AgentStatus.PAUSED: frozenset(
        {AgentStatus.READY, AgentStatus.OFFLINE, AgentStatus.ERROR, AgentStatus.ARCHIVED}
    ),
    AgentStatus.OFFLINE: frozenset(
        {AgentStatus.READY, AgentStatus.PAUSED, AgentStatus.ERROR, AgentStatus.ARCHIVED}
    ),
    AgentStatus.ERROR: frozenset(
        {
            AgentStatus.PROVISIONING,
            AgentStatus.READY,
            AgentStatus.PAUSED,
            AgentStatus.OFFLINE,
            AgentStatus.ARCHIVED,
        }
    ),
    AgentStatus.ARCHIVED: frozenset({AgentStatus.PROVISIONING}),
}

SANDBOX_EDGES: dict[SandboxStatus, frozenset[SandboxStatus]] = {
    SandboxStatus.PROVISIONING: frozenset(
        {SandboxStatus.READY, SandboxStatus.ERROR, SandboxStatus.ARCHIVED}
    ),
    SandboxStatus.READY: frozenset(
        {
            SandboxStatus.BUSY,
            SandboxStatus.SUSPENDED,
            SandboxStatus.OFFLINE,
            SandboxStatus.ERROR,
            SandboxStatus.ARCHIVED,
        }
    ),
    SandboxStatus.BUSY: frozenset(
        {
            SandboxStatus.READY,
            SandboxStatus.SUSPENDED,
            SandboxStatus.OFFLINE,
            SandboxStatus.ERROR,
            SandboxStatus.ARCHIVED,
        }
    ),
    SandboxStatus.SUSPENDED: frozenset(
        {SandboxStatus.READY, SandboxStatus.OFFLINE, SandboxStatus.ERROR, SandboxStatus.ARCHIVED}
    ),
    SandboxStatus.OFFLINE: frozenset(
        {SandboxStatus.PROVISIONING, SandboxStatus.READY, SandboxStatus.ERROR, SandboxStatus.ARCHIVED}
    ),
    SandboxStatus.ERROR: frozenset(
        {SandboxStatus.PROVISIONING, SandboxStatus.SUSPENDED, SandboxStatus.ARCHIVED}
    ),
    SandboxStatus.ARCHIVED: frozenset({SandboxStatus.PROVISIONING}),
}

DELIVERY_EDGES: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.PENDING: frozenset({DeliveryStatus.LEASED, DeliveryStatus.CANCELED}),
    DeliveryStatus.LEASED: frozenset(
        {
            DeliveryStatus.PENDING,
            DeliveryStatus.DISPATCHED,
            DeliveryStatus.RETRY_WAIT,
            DeliveryStatus.DEAD_LETTER,
            DeliveryStatus.CANCELED,
        }
    ),
    DeliveryStatus.DISPATCHED: frozenset(
        {
            DeliveryStatus.ACKNOWLEDGED,
            DeliveryStatus.RETRY_WAIT,
            DeliveryStatus.DEAD_LETTER,
            DeliveryStatus.CANCELED,
        }
    ),
    DeliveryStatus.ACKNOWLEDGED: frozenset(),
    DeliveryStatus.RETRY_WAIT: frozenset(
        {DeliveryStatus.PENDING, DeliveryStatus.DEAD_LETTER, DeliveryStatus.CANCELED}
    ),
    DeliveryStatus.DEAD_LETTER: frozenset(
        {DeliveryStatus.PENDING, DeliveryStatus.CANCELED}
    ),
    DeliveryStatus.CANCELED: frozenset(),
}

RUN_EDGES: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_INPUT,
            RunStatus.WAITING_APPROVAL,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
            RunStatus.CANCELED,
        }
    ),
    RunStatus.WAITING_INPUT: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELED}
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.TIMED_OUT: frozenset(),
    RunStatus.CANCELED: frozenset(),
}

APPROVAL_EDGES: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.DENIED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELED,
        }
    ),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.DENIED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.CANCELED: frozenset(),
}

CONVERSATION_EDGES: dict[ConversationStatus, frozenset[ConversationStatus]] = {
    ConversationStatus.ACTIVE: frozenset(
        {ConversationStatus.ARCHIVED, ConversationStatus.DELETED}
    ),
    ConversationStatus.ARCHIVED: frozenset(
        {ConversationStatus.ACTIVE, ConversationStatus.DELETED}
    ),
    ConversationStatus.DELETED: frozenset(),
}

ARTIFACT_EDGES: dict[ArtifactStatus, frozenset[ArtifactStatus]] = {
    ArtifactStatus.PUBLISHED: frozenset(
        {ArtifactStatus.ARCHIVED, ArtifactStatus.DELETED}
    ),
    ArtifactStatus.ARCHIVED: frozenset(
        {ArtifactStatus.PUBLISHED, ArtifactStatus.DELETED}
    ),
    ArtifactStatus.DELETED: frozenset(),
}


State = TypeVar("State", bound=Enum)


def can_transition(
    current: State,
    target: State,
    edges: Mapping[State, frozenset[State]],
) -> bool:
    """Return whether `current → target` is a declared state transition."""

    return target in edges.get(current, frozenset())


def require_transition(
    current: State,
    target: State,
    edges: Mapping[State, frozenset[State]],
) -> None:
    """Raise when a transition is not declared in the supplied state machine."""

    if not can_transition(current, target, edges):
        raise InvalidTransitionError(
            f"illegal transition {_enum_value(current)} → {_enum_value(target)}"
        )


def aggregate_owner(kind: AggregateKind) -> AggregateOwner:
    """Return the sole service authorized to mutate an aggregate's durable state."""

    try:
        kind = kind if isinstance(kind, AggregateKind) else AggregateKind(kind)
    except (TypeError, ValueError) as error:
        raise ContractError(f"invalid aggregate kind: {kind!r}") from error
    return AGGREGATE_OWNERS[kind]


_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_KIND_RE = re.compile(r"\A[a-z][a-z0-9_.-]{0,63}\Z")
_PREFIX_RE = re.compile(r"\A[a-z][a-z0-9_]{0,23}\Z")


def new_domain_id(prefix: str | DomainIdPrefix) -> str:
    """Create a path-safe, opaque identifier with a stable domain prefix."""

    value = prefix.value if isinstance(prefix, DomainIdPrefix) else prefix
    if not isinstance(value, str) or not _PREFIX_RE.fullmatch(value):
        raise ContractError("id prefix must use lowercase letters, digits, and underscores")
    if value not in CANONICAL_ID_PREFIXES:
        raise ContractError(f"unknown canonical id prefix: {value}")
    return f"{value}_{uuid.uuid4().hex}"


def utc_now() -> str:
    """Return the canonical timezone-aware timestamp format used by Team records."""

    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ActorRef:
    id: str
    kind: ActorKind
    display_name: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_id(self.id, "actor id"))
        object.__setattr__(self, "kind", _as_enum(ActorKind, self.kind, "actor kind"))
        object.__setattr__(self, "display_name", str(self.display_name or "").strip())

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActorRef":
        raw = _required_mapping(raw, "actor")
        return cls(
            id=raw.get("id", ""),
            kind=_as_enum(ActorKind, raw.get("kind"), "actor kind"),
            display_name=raw.get("display_name", ""),
        )


@dataclass(frozen=True)
class ResourceRef:
    kind: str
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _required_kind(self.kind, "resource kind"))
        object.__setattr__(self, "id", _required_id(self.id, "resource id"))

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ResourceRef":
        raw = _required_mapping(raw, "resource")
        return cls(kind=raw.get("kind", ""), id=raw.get("id", ""))


@dataclass(frozen=True)
class WorkBudget:
    """Per-request caps. `None` means inherit policy, never implicit unlimited."""

    max_tokens: Optional[int] = None
    max_cost_micros: Optional[int] = None
    max_model_calls: Optional[int] = None
    max_tool_calls: Optional[int] = None
    max_wall_seconds: Optional[int] = None
    max_wakes: Optional[int] = None
    max_handoffs: Optional[int] = None
    max_fanout: Optional[int] = None

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ContractError(f"{name} must be a positive integer or null")

    def to_dict(self) -> dict[str, Optional[int]]:
        return {
            "max_tokens": self.max_tokens,
            "max_cost_micros": self.max_cost_micros,
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_wall_seconds": self.max_wall_seconds,
            "max_wakes": self.max_wakes,
            "max_handoffs": self.max_handoffs,
            "max_fanout": self.max_fanout,
        }

    @classmethod
    def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "WorkBudget":
        if raw is None:
            return cls()
        values = dict(_required_mapping(raw, "budget"))
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ContractError(f"budget contains unknown fields: {', '.join(unknown)}")
        try:
            return cls(**values)
        except TypeError as error:
            raise ContractError("budget is malformed") from error


@dataclass(frozen=True)
class WorkRequest:
    request_id: str
    workspace_id: str
    source: WorkSource
    source_ref: ResourceRef
    requester: ActorRef
    target_agent_ids: tuple[str, ...]
    instruction: str
    idempotency_key: str
    correlation_id: str
    conversation_id: Optional[str] = None
    thread_id: Optional[str] = None
    task_id: Optional[str] = None
    expected_output: str = ""
    context_refs: tuple[ResourceRef, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    permission_scope: Mapping[str, Any] = field(default_factory=dict)
    budget: WorkBudget = field(default_factory=WorkBudget)
    priority: int = 0
    causation_id: Optional[str] = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required_id(self.request_id, "request id"))
        object.__setattr__(self, "workspace_id", _required_id(self.workspace_id, "workspace id"))
        object.__setattr__(self, "source", _as_enum(WorkSource, self.source, "work source"))
        if not isinstance(self.source_ref, ResourceRef):
            raise ContractError("source_ref must be a ResourceRef")
        if not isinstance(self.requester, ActorRef):
            raise ContractError("requester must be an ActorRef")
        object.__setattr__(
            self,
            "target_agent_ids",
            _unique_ids(self.target_agent_ids, "target agent id", require_one=True),
        )
        instruction = str(self.instruction or "").strip()
        if not instruction:
            raise ContractError("instruction is required")
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(
            self, "idempotency_key", _required_id(self.idempotency_key, "idempotency key")
        )
        object.__setattr__(
            self, "correlation_id", _required_id(self.correlation_id, "correlation id")
        )
        object.__setattr__(
            self, "conversation_id", _optional_id(self.conversation_id, "conversation id")
        )
        object.__setattr__(self, "thread_id", _optional_id(self.thread_id, "thread id"))
        object.__setattr__(self, "task_id", _optional_id(self.task_id, "task id"))
        object.__setattr__(self, "causation_id", _optional_id(self.causation_id, "causation id"))
        object.__setattr__(self, "expected_output", str(self.expected_output or "").strip())
        refs = tuple(self.context_refs or ())
        if any(not isinstance(ref, ResourceRef) for ref in refs):
            raise ContractError("context_refs must contain ResourceRef values")
        object.__setattr__(self, "context_refs", refs)
        object.__setattr__(
            self,
            "input_artifact_ids",
            _unique_ids(self.input_artifact_ids, "artifact id", require_one=False),
        )
        object.__setattr__(
            self,
            "permission_scope",
            _freeze_json_object(self.permission_scope, "permission_scope"),
        )
        if not isinstance(self.budget, WorkBudget):
            raise ContractError("budget must be a WorkBudget")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ContractError("priority must be an integer")
        if not -100 <= self.priority <= 100:
            raise ContractError("priority must be between -100 and 100")
        object.__setattr__(self, "created_at", _required_timestamp(self.created_at, "created_at"))
        self._validate_source_shape()

    def _validate_source_shape(self) -> None:
        if self.source in {WorkSource.DIRECT_MESSAGE, WorkSource.GROUP_MESSAGE}:
            if self.source_ref.kind != "message" or self.conversation_id is None:
                raise ContractError("message work requires a message source and conversation id")
        if self.source is WorkSource.DIRECT_MESSAGE and len(self.target_agent_ids) != 1:
            raise ContractError("direct-message work requires exactly one target agent")
        if self.source is WorkSource.TASK:
            if self.source_ref.kind != "task" or self.task_id is None:
                raise ContractError("task work requires a task source and task id")
        if self.source is WorkSource.AUTOMATION and self.source_ref.kind != "automation":
            raise ContractError("automation work requires an automation source")
        if self.source is WorkSource.CONNECTOR and self.source_ref.kind != "connector_event":
            raise ContractError("connector work requires a connector_event source")
        if self.source is WorkSource.HANDOFF:
            if self.source_ref.kind != "handoff" or self.task_id is None:
                raise ContractError("handoff work requires a handoff source and task id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "workspace_id": self.workspace_id,
            "source": self.source.value,
            "source_ref": self.source_ref.to_dict(),
            "requester": self.requester.to_dict(),
            "target_agent_ids": list(self.target_agent_ids),
            "instruction": self.instruction,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "conversation_id": self.conversation_id,
            "thread_id": self.thread_id,
            "task_id": self.task_id,
            "expected_output": self.expected_output,
            "context_refs": [ref.to_dict() for ref in self.context_refs],
            "input_artifact_ids": list(self.input_artifact_ids),
            "permission_scope": _thaw_json(self.permission_scope),
            "budget": self.budget.to_dict(),
            "priority": self.priority,
            "causation_id": self.causation_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "WorkRequest":
        raw = _required_mapping(raw, "work request")
        try:
            source_ref = ResourceRef.from_dict(raw.get("source_ref") or {})
            requester = ActorRef.from_dict(raw.get("requester") or {})
            context_refs = tuple(
                ResourceRef.from_dict(value) for value in (raw.get("context_refs") or [])
            )
        except (TypeError, AttributeError) as error:
            raise ContractError("work request contains malformed nested records") from error
        return cls(
            request_id=raw.get("request_id", ""),
            workspace_id=raw.get("workspace_id", ""),
            source=_as_enum(WorkSource, raw.get("source"), "work source"),
            source_ref=source_ref,
            requester=requester,
            target_agent_ids=tuple(raw.get("target_agent_ids") or ()),
            instruction=raw.get("instruction", ""),
            idempotency_key=raw.get("idempotency_key", ""),
            correlation_id=raw.get("correlation_id", ""),
            conversation_id=raw.get("conversation_id"),
            thread_id=raw.get("thread_id"),
            task_id=raw.get("task_id"),
            expected_output=raw.get("expected_output", ""),
            context_refs=context_refs,
            input_artifact_ids=tuple(raw.get("input_artifact_ids") or ()),
            permission_scope=_mapping_or_empty(raw, "permission_scope"),
            budget=WorkBudget.from_dict(raw.get("budget")),
            priority=raw.get("priority", 0),
            causation_id=raw.get("causation_id"),
            created_at=raw.get("created_at") or "",
        )


@dataclass(frozen=True)
class HandoffContract:
    handoff_id: str
    workspace_id: str
    task_id: str
    sender_agent_id: str
    recipient_agent_id: str
    purpose: str
    expected_output: str
    requester: ActorRef
    idempotency_key: str
    correlation_id: str
    artifact_ids: tuple[str, ...] = ()
    budget: WorkBudget = field(default_factory=WorkBudget)
    depth: int = 1
    agent_path: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in (
            "handoff_id",
            "workspace_id",
            "task_id",
            "sender_agent_id",
            "recipient_agent_id",
            "idempotency_key",
            "correlation_id",
        ):
            object.__setattr__(self, name, _required_id(getattr(self, name), name.replace("_", " ")))
        if self.sender_agent_id == self.recipient_agent_id:
            raise ContractError("handoff sender and recipient must differ")
        if not isinstance(self.requester, ActorRef):
            raise ContractError("handoff requester must be an ActorRef")
        purpose = str(self.purpose or "").strip()
        expected = str(self.expected_output or "").strip()
        if not purpose or not expected:
            raise ContractError("handoff purpose and expected_output are required")
        object.__setattr__(self, "purpose", purpose)
        object.__setattr__(self, "expected_output", expected)
        object.__setattr__(
            self, "artifact_ids", _unique_ids(self.artifact_ids, "artifact id", require_one=False)
        )
        if not isinstance(self.budget, WorkBudget):
            raise ContractError("budget must be a WorkBudget")
        if isinstance(self.depth, bool) or not isinstance(self.depth, int) or self.depth <= 0:
            raise ContractError("handoff depth must be a positive integer")
        path = _ordered_ids(self.agent_path, "handoff path agent id", require_one=False)
        if not path:
            path = (self.sender_agent_id,)
        if path[-1] != self.sender_agent_id:
            raise ContractError("handoff path must end with the sender agent")
        if len(path) != self.depth:
            raise ContractError("handoff depth must equal the number of agents in its path")
        if self.recipient_agent_id in path:
            raise ContractError("handoff recipient already appears in the handoff path")
        if self.budget.max_handoffs is not None and self.depth > self.budget.max_handoffs:
            raise ContractError("handoff depth exceeds its budget")
        object.__setattr__(self, "agent_path", path)
        object.__setattr__(self, "created_at", _required_timestamp(self.created_at, "created_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "workspace_id": self.workspace_id,
            "task_id": self.task_id,
            "sender_agent_id": self.sender_agent_id,
            "recipient_agent_id": self.recipient_agent_id,
            "purpose": self.purpose,
            "expected_output": self.expected_output,
            "requester": self.requester.to_dict(),
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "artifact_ids": list(self.artifact_ids),
            "budget": self.budget.to_dict(),
            "depth": self.depth,
            "agent_path": list(self.agent_path),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "HandoffContract":
        raw = _required_mapping(raw, "handoff")
        try:
            requester = ActorRef.from_dict(raw.get("requester") or {})
        except (TypeError, AttributeError) as error:
            raise ContractError("handoff contains a malformed requester") from error
        return cls(
            handoff_id=raw.get("handoff_id", ""),
            workspace_id=raw.get("workspace_id", ""),
            task_id=raw.get("task_id", ""),
            sender_agent_id=raw.get("sender_agent_id", ""),
            recipient_agent_id=raw.get("recipient_agent_id", ""),
            purpose=raw.get("purpose", ""),
            expected_output=raw.get("expected_output", ""),
            requester=requester,
            idempotency_key=raw.get("idempotency_key", ""),
            correlation_id=raw.get("correlation_id", ""),
            artifact_ids=_sequence_or_empty(raw, "artifact_ids"),
            budget=WorkBudget.from_dict(raw.get("budget")),
            depth=raw.get("depth", 1),
            agent_path=_sequence_or_empty(raw, "agent_path"),
            created_at=raw.get("created_at") or "",
        )


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    workspace_id: str
    actor_id: str
    aggregate_type: str
    aggregate_id: str
    correlation_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    causation_id: Optional[str] = None
    sequence: Optional[int] = None
    schema_version: int = 1
    occurred_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "workspace_id",
            "actor_id",
            "aggregate_id",
            "correlation_id",
        ):
            object.__setattr__(self, name, _required_id(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "event_type", _required_kind(self.event_type, "event type"))
        object.__setattr__(
            self, "aggregate_type", _required_kind(self.aggregate_type, "aggregate type")
        )
        object.__setattr__(self, "causation_id", _optional_id(self.causation_id, "causation id"))
        if self.sequence is not None and (
            isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence <= 0
        ):
            raise ContractError("sequence must be a positive integer or null")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version <= 0
        ):
            raise ContractError("schema_version must be a positive integer")
        object.__setattr__(
            self, "payload", _freeze_json_object(self.payload, "event payload")
        )
        object.__setattr__(self, "occurred_at", _required_timestamp(self.occurred_at, "occurred_at"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "correlation_id": self.correlation_id,
            "payload": _thaw_json(self.payload),
            "causation_id": self.causation_id,
            "sequence": self.sequence,
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DomainEvent":
        raw = _required_mapping(raw, "domain event")
        return cls(
            event_id=raw.get("event_id", ""),
            event_type=raw.get("event_type", ""),
            workspace_id=raw.get("workspace_id", ""),
            actor_id=raw.get("actor_id", ""),
            aggregate_type=raw.get("aggregate_type", ""),
            aggregate_id=raw.get("aggregate_id", ""),
            correlation_id=raw.get("correlation_id", ""),
            payload=_mapping_or_empty(raw, "payload"),
            causation_id=raw.get("causation_id"),
            sequence=raw.get("sequence"),
            schema_version=raw.get("schema_version", 1),
            occurred_at=raw.get("occurred_at") or "",
        )


def _enum_value(value: Enum) -> str:
    return str(getattr(value, "value", value))


def _as_enum(enum_type, value: Any, label: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as error:
        raise ContractError(f"invalid {label}: {value!r}") from error


def _required_id(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a safe non-empty identifier")
    text = value.strip()
    if not _ID_RE.fullmatch(text):
        raise ContractError(f"{label} must be a safe non-empty identifier")
    return text


def _optional_id(value: Any, label: str) -> Optional[str]:
    if value is None or value == "":
        return None
    return _required_id(value, label)


def _required_kind(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ContractError(
            f"{label} must use lowercase letters, digits, dots, dashes, or underscores"
        )
    text = value.strip()
    if not _KIND_RE.fullmatch(text):
        raise ContractError(f"{label} must use lowercase letters, digits, dots, dashes, or underscores")
    return text


def _required_timestamp(value: Any, label: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must include a timezone")
    return text


def _unique_ids(values, label: str, *, require_one: bool) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ContractError(f"{label}s must be a sequence")
    result: list[str] = []
    seen: set[str] = set()
    try:
        for value in values or ():
            item = _required_id(value, label)
            if item not in seen:
                seen.add(item)
                result.append(item)
    except TypeError as error:
        raise ContractError(f"{label}s must be a sequence") from error
    if require_one and not result:
        raise ContractError(f"at least one {label} is required")
    return tuple(result)


def _ordered_ids(values, label: str, *, require_one: bool) -> tuple[str, ...]:
    """Validate an ordered identifier path without silently hiding a cycle."""

    result = _unique_ids(values, label, require_one=require_one)
    original = tuple(values or ())
    if len(result) != len(original):
        raise ContractError(f"{label}s must not contain duplicates")
    return result


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return value


def _mapping_or_empty(raw: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = raw.get(field)
    if value is None:
        return {}
    return _required_mapping(value, field)


def _sequence_or_empty(raw: Mapping[str, Any], field: str) -> tuple[Any, ...]:
    value = raw.get(field)
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        raise ContractError(f"{field} must be a sequence")
    try:
        return tuple(value)
    except TypeError as error:
        raise ContractError(f"{field} must be a sequence") from error


def _freeze_json_object(value: Any, label: str) -> Mapping[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    frozen = _freeze_json(value, label)
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_json(value: Any, label: str) -> Any:
    """Validate and recursively freeze a JSON-compatible contract value."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{label} must contain finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"{label} object keys must be strings")
            result[key] = _freeze_json(item, label)
        return MappingProxyType(result)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, label) for item in value)
    raise ContractError(f"{label} must contain only JSON-compatible values")


def _thaw_json(value: Any) -> Any:
    """Return a detached JSON-ready copy of a recursively frozen value."""

    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
