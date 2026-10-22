"""Application service for the canonical local Team surface.

The HTTP/UI layers use this boundary instead of reaching into SQLite or the
legacy one-group chat.  It resolves the local human principal, builds the
requester-aware policy context for each installed agent, and delegates message
dispatch to the durable coordinator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    ActorKind,
    ActorRef,
    ApprovalStatus,
    ContractError,
    DomainEvent,
    DomainIdPrefix,
    HandoffContract,
    ResourceRef,
    WorkBudget,
    WorkRequest,
    new_domain_id,
    utc_now,
)
from .coordinator import WorkCoordinator, WorkSubmission
from .context import ContextPackageBuilder
from .lifecycle import TaskStatus, TransitionAuthority
from .permissions import (
    WORKSPACE_ROLE_ACTIONS,
    PermissionContext,
    SandboxPolicy,
    TeamPermissionService,
    WorkspaceRole,
    redact_sensitive,
)
from .persistence import (
    AgentRepository,
    ArtifactRepository,
    ConversationRepository,
    EventRepository,
    GovernanceRepository,
    HandoffRepository,
    IdentityRepository,
    IntegrationRepository,
    ConnectorRepository,
    PersistenceConflictError,
    SearchRepository,
    SandboxRepository,
    TaskRepository,
)
from .sharing import (
    ArtifactPublication,
    ArtifactVisibility,
    ContextBoundary,
    ContextScope,
    ContextShare,
)


def _stable_id(prefix: str, value: str) -> str:
    """Build a deterministic domain id for a retried client command."""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _clock_minutes(value: str) -> int:
    if len(value) != 5 or value[2] != ":":
        raise ValueError("clock must use HH:MM")
    hour, minute = int(value[:2]), int(value[3:])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("clock is outside the valid range")
    return hour * 60 + minute


class TeamService:
    """Transport-neutral commands and queries for one local Team workspace."""

    def __init__(
        self,
        identities: IdentityRepository,
        agents: AgentRepository,
        conversations: ConversationRepository,
        coordinator: WorkCoordinator,
        permissions: TeamPermissionService,
        *,
        workspace_id: str,
        actor_id: str,
        tasks: Optional[TaskRepository] = None,
        artifacts: Optional[ArtifactRepository] = None,
        search: Optional[SearchRepository] = None,
        events: Optional[EventRepository] = None,
        governance: Optional[GovernanceRepository] = None,
        handoffs: Optional[HandoffRepository] = None,
        integrations: Optional[IntegrationRepository] = None,
        sandboxes: Optional[SandboxRepository] = None,
        computers: Optional[Any] = None,
        connectors: Optional[Any] = None,
        context: Optional[Any] = None,
        context_builder: Optional[ContextPackageBuilder] = None,
        event_sink: Optional[Any] = None,
        cancel_interrupt: Optional[Callable[[str], bool]] = None,
        semantic_search: Optional[Callable[..., Mapping[str, Any]]] = None,
        skill_snapshot_resolver: Optional[
            Callable[[str, str, Mapping[str, Any]], Optional[Mapping[str, Any]]]
        ] = None,
    ) -> None:
        self.identities = identities
        self.agents = agents
        self.conversations = conversations
        self.coordinator = coordinator
        self.permissions = permissions
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.tasks = tasks
        self.artifacts = artifacts
        self.search = search
        self.events = events
        self.governance = governance
        self.handoffs = handoffs
        self.integrations = integrations
        self.sandboxes = sandboxes
        self.computers = computers
        self.connectors = connectors
        self.context = context
        self.context_builder = context_builder
        self.event_sink = event_sink
        self.cancel_interrupt = cancel_interrupt
        self.semantic_search = semantic_search
        self.skill_snapshot_resolver = skill_snapshot_resolver
        if getattr(self.coordinator, "snapshot_builder", None) is None:
            self.coordinator.snapshot_builder = self.snapshot_work_request

    def snapshot_work_request(
        self,
        request: WorkRequest,
        _context_by_agent: Mapping[str, PermissionContext],
    ) -> WorkRequest:
        """Capture dispatch-time Bot/runtime inputs for every Team request.

        Automation already supplied this snapshot in older implementations. The
        coordinator callback applies the same contract to messages, tasks,
        connector events, and handoffs without duplicating snapshot logic in each
        caller. Existing explicit scope fields are preserved for replay.
        """

        if not isinstance(request, WorkRequest):
            raise ContractError("snapshot builder requires a WorkRequest")
        scope = dict(request.permission_scope)
        scope.setdefault("snapshot_version", 1)
        scope.setdefault("captured_at", utc_now())

        target_ids = tuple(request.target_agent_ids)
        revision_ids = dict(scope.get("agent_revision_ids") or {})
        revision_snapshots = dict(scope.get("agent_revision_snapshots") or {})
        sandbox_ids = dict(scope.get("sandbox_ids") or {})
        sandbox_snapshots = dict(scope.get("sandbox_snapshots") or {})
        computer_ids = dict(scope.get("computer_ids") or {})
        computer_snapshots = dict(scope.get("computer_snapshots") or {})
        skill_snapshots = dict(scope.get("skill_snapshots") or {})

        for agent_id in target_ids:
            installation = self.agents.get_installation(agent_id)
            if installation is None or installation["workspace_id"] != self.workspace_id:
                raise ContractError(f"agent installation not found: {agent_id}")
            revision_id = str(
                revision_ids.get(agent_id) or installation["active_revision_id"] or ""
            ).strip()
            revision = self.agents.get_revision(revision_id)
            if not revision:
                raise ContractError(f"agent revision is missing: {agent_id}")
            revision_ids.setdefault(agent_id, revision_id)
            revision_snapshots.setdefault(
                agent_id,
                redact_sensitive(
                    {
                        "agent_revision_id": revision_id,
                        "agent_definition_id": revision["agent_definition_id"],
                        "revision_number": revision["revision_number"],
                        "instructions": revision["instructions"],
                        "model_policy": revision.get("model_policy") or {},
                        "sandbox_policy": revision.get("sandbox_policy") or {},
                        "memory_policy": revision.get("memory_policy") or {},
                        "reply_policy": revision.get("reply_policy") or {},
                        "budget_policy": revision.get("budget_policy") or {},
                        "approval_policy": revision.get("approval_policy") or {},
                        "capabilities": revision.get("capabilities") or [],
                    }
                ),
            )
            if agent_id not in skill_snapshots:
                pinned_skills: list[dict[str, Any]] = []
                for item in (revision.get("capabilities") or ()):
                    if not isinstance(item, Mapping) or item.get("capability_kind") != "skill":
                        continue
                    capability_ref = str(item.get("capability_ref") or "").strip()
                    skill = {
                        "capability_kind": "skill",
                        "capability_ref": capability_ref,
                        "required": bool(item.get("required", False)),
                        "config": redact_sensitive(item.get("config") or {}),
                        "available": False,
                    }
                    if self.skill_snapshot_resolver is not None and capability_ref:
                        resolved = self.skill_snapshot_resolver(
                            agent_id, capability_ref, item.get("config") or {}
                        )
                        if isinstance(resolved, Mapping):
                            skill.update(
                                {
                                    str(key): value
                                    for key, value in resolved.items()
                                    if str(key)
                                    in {
                                        "name",
                                        "description",
                                        "instructions",
                                        "allowed_tools",
                                        "content_sha256",
                                        "available",
                                    }
                                }
                            )
                    pinned_skills.append(skill)
                skill_snapshots[agent_id] = pinned_skills

            if self.sandboxes is not None and agent_id not in sandbox_ids:
                sandbox = self.sandboxes.get_sandbox_for_agent(agent_id)
                if sandbox is not None and sandbox["workspace_id"] == self.workspace_id:
                    sandbox_ids[agent_id] = sandbox["sandbox_id"]
                    sandbox_snapshots.setdefault(
                        agent_id,
                        {
                            key: sandbox.get(key)
                            for key in (
                                "sandbox_id",
                                "workspace_id",
                                "agent_id",
                                "provider_kind",
                                "isolation_level",
                                "home_ref",
                                "mounts",
                                "resource_policy",
                            )
                        },
                    )

            # A Bot may explicitly have no Computer. Persist that fact in the
            # dispatch snapshot so a later installation change cannot silently
            # introduce browser authority into already queued work.
            if self.computers is not None and agent_id not in computer_ids:
                computer = self.computers.get_for_agent(agent_id)
                computer_ids[agent_id] = computer["computer_id"] if computer is not None else None
                if computer is not None and computer["workspace_id"] == self.workspace_id:
                    computer_snapshots.setdefault(
                        agent_id,
                        {
                            key: computer.get(key)
                            for key in (
                                "computer_id",
                                "workspace_id",
                                "agent_id",
                                "provider_kind",
                                "status",
                                "capabilities",
                            )
                        },
                    )

        scope["agent_revision_ids"] = revision_ids
        scope["agent_revision_snapshots"] = revision_snapshots
        scope["sandbox_ids"] = sandbox_ids
        scope["sandbox_snapshots"] = sandbox_snapshots
        scope["computer_ids"] = computer_ids
        scope["computer_snapshots"] = computer_snapshots
        scope["skill_snapshots"] = skill_snapshots

        if "context_snapshot" not in scope:
            context_snapshot: dict[str, Any] = {
                "captured_at": scope["captured_at"],
                "conversation_id": request.conversation_id,
                "thread_id": request.thread_id,
                "task_id": request.task_id,
                "agent_ids": list(target_ids),
                "materialized": False,
                "packages_by_agent": {},
            }
            if request.conversation_id and self.context_builder is not None:
                for agent_id in target_ids:
                    installation = self.agents.get_installation(agent_id)
                    if installation is None:
                        raise ContractError(f"agent installation not found: {agent_id}")
                    context_snapshot["packages_by_agent"][agent_id] = self.context_builder.build(
                        request.conversation_id,
                        request.requester.id,
                        principal_actor_id=installation["actor_id"],
                        private_agent_id=agent_id,
                        task_id=request.task_id,
                        max_messages=200,
                        max_chars=200_000,
                    )
                context_snapshot["materialized"] = True
            scope["context_snapshot"] = context_snapshot

        connector_repository = getattr(self.connectors, "repository", None)
        if connector_repository is not None and "connector_grant_snapshots" not in scope:
            scope["connector_grant_snapshots"] = {
                agent_id: [
                    {
                        "connector_grant_id": grant["connector_grant_id"],
                        "external_identity_record_id": grant["external_identity_record_id"],
                        "agent_id": grant["agent_id"],
                        "requester_actor_id": grant["requester_actor_id"],
                        "actions": list(grant.get("actions") or ()),
                        "resource_ids": list(grant.get("resource_ids") or ()),
                        "expires_at": grant.get("expires_at"),
                    }
                    for grant in connector_repository.list_grants(self.workspace_id)
                    if grant.get("agent_id") == agent_id
                    and grant.get("requester_actor_id") == request.requester.id
                    and grant.get("status") == "active"
                ]
                for agent_id in target_ids
            }

        return replace(request, permission_scope=scope)

    @property
    def requester(self) -> ActorRef:
        with self.identities.database._lock:
            row = self.identities.database._conn.execute(
                "SELECT actor_id, actor_kind, display_name FROM actors WHERE actor_id = ?",
                (self.actor_id,),
            ).fetchone()
        if row is None or row["actor_kind"] != ActorKind.HUMAN.value:
            raise ContractError("local Team requester identity is missing")
        return ActorRef(row["actor_id"], ActorKind.HUMAN, row["display_name"])

    def list_agents(self) -> list[dict[str, Any]]:
        self._require_workspace_member()
        with self.identities.database._lock:
            rows = self.identities.database._conn.execute(
                """
                SELECT i.*, a.display_name, a.handle, d.description, d.base_role,
                       d.avatar_id, d.handle AS definition_handle
                FROM agent_installations AS i
                JOIN actors AS a ON a.actor_id = i.actor_id
                JOIN agent_definitions AS d ON d.agent_definition_id = i.agent_definition_id
                LEFT JOIN agent_deletions AS deleted ON deleted.agent_id = i.agent_id
                WHERE i.workspace_id = ?
                  AND deleted.agent_id IS NULL
                ORDER BY a.display_name, i.agent_id
                """,
                (self.workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Return one installed agent only after applying workspace ACLs."""

        self._require_workspace_member()
        installation = self.agents.get_installation(str(agent_id or "").strip())
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError("agent not found")
        for agent in self.list_agents():
            if agent["agent_id"] == installation["agent_id"]:
                return agent
        raise ContractError("agent not found")

    def list_agent_presence(self) -> list[dict[str, Any]]:
        """Project truthful presence from installation, sandbox, queue, and runs."""

        self._require_workspace_member()
        with self.identities.database._lock:
            rows = self.identities.database._conn.execute(
                """
                SELECT i.agent_id, i.status AS agent_status, i.max_concurrency,
                       a.display_name, a.handle, d.avatar_id,
                       s.sandbox_id, s.status AS sandbox_status,
                       COALESCE(active.active_runs, 0) AS active_runs,
                       COALESCE(queued.queued_deliveries, 0) AS queued_deliveries
                FROM agent_installations AS i
                JOIN actors AS a ON a.actor_id = i.actor_id
                JOIN agent_definitions AS d ON d.agent_definition_id = i.agent_definition_id
                LEFT JOIN sandboxes AS s ON s.agent_id = i.agent_id
                LEFT JOIN agent_deletions AS deleted ON deleted.agent_id = i.agent_id
                LEFT JOIN (
                    SELECT agent_id, COUNT(*) AS active_runs
                    FROM runs
                    WHERE status IN ('queued', 'running', 'waiting_input', 'waiting_approval')
                    GROUP BY agent_id
                ) AS active ON active.agent_id = i.agent_id
                LEFT JOIN (
                    SELECT target_agent_id, COUNT(*) AS queued_deliveries
                    FROM deliveries
                    WHERE status IN ('pending', 'leased', 'dispatched', 'retry_wait')
                    GROUP BY target_agent_id
                ) AS queued ON queued.target_agent_id = i.agent_id
                WHERE i.workspace_id = ?
                  AND deleted.agent_id IS NULL
                ORDER BY a.display_name, i.agent_id
                """,
                (self.workspace_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            agent_status = item["agent_status"]
            sandbox_status = item["sandbox_status"]
            if agent_status == "archived":
                state, reason = "archived", "agent archived"
            elif agent_status == "paused":
                state, reason = "paused", "agent paused"
            elif agent_status == "error":
                state, reason = "error", "agent is in an error state"
            elif agent_status == "offline":
                state, reason = "offline", "agent is offline"
            elif sandbox_status is None:
                state, reason = "offline", "agent sandbox is missing"
            elif sandbox_status in {"offline", "error", "suspended", "archived"}:
                state, reason = "offline", f"sandbox is {sandbox_status}"
            elif sandbox_status == "busy":
                state, reason = "working", "sandbox is busy"
            elif int(item["active_runs"] or 0) and agent_status == "working":
                state, reason = "working", "agent has an active run"
            elif int(item["active_runs"] or 0):
                state, reason = "waiting", "agent has a waiting run"
            elif agent_status == "provisioning" or sandbox_status == "provisioning":
                state, reason = "offline", "provisioning is incomplete"
            else:
                state, reason = "ready", "ready for work"
            item.update(
                {
                    "state": state,
                    "reason": reason,
                    "ready": state == "ready",
                    "capacity": max(0, int(item["max_concurrency"] or 0) - int(item["active_runs"] or 0)),
                }
            )
            result.append(item)
        return result

    def overview(self) -> dict[str, Any]:
        """Return stable counts for the Team home and status badges."""
        self._require_workspace_member()
        conversations = self.list_conversations(limit=200)
        agent_rows = self.list_agents()
        task_rows = self.list_tasks(limit=200) if self.tasks is not None else {"tasks": []}
        unread = 0
        for conversation in conversations:
            unread += int(conversation.get("unread_count", 0))
        return {
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "agents": agent_rows,
            "conversations": conversations,
            "counts": {
                "agents": len(agent_rows),
                "conversations": len(conversations),
                "tasks": len(task_rows.get("tasks", [])),
                "unread": unread,
            },
        }

    def list_conversations(self, *, include_archived: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        conversations = self.conversations.list_conversations(
            self.workspace_id,
            self.actor_id,
            include_archived=include_archived,
            limit=limit,
        )
        for conversation in conversations:
            state = self.conversations.unread_state(
                conversation["conversation_id"], self.actor_id
            )
            conversation["unread_count"] = state["unread_count"]
            conversation["thread_unread"] = state["thread_unread"]
        return conversations

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.conversations.get_conversation(conversation_id)
        if conversation is None or conversation["workspace_id"] != self.workspace_id or not any(
            member["actor_id"] == self.actor_id and member["left_at"] is None
            for member in conversation["members"]
        ):
            raise ContractError("conversation not found")
        return conversation

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Return one message only after applying the requester conversation ACL."""

        message = self.conversations.get_message(message_id, self.actor_id)
        if message is None:
            raise ContractError("message not found")
        conversation = self.conversations.get_conversation(message["conversation_id"])
        if conversation is None or conversation["workspace_id"] != self.workspace_id:
            raise ContractError("message not found")
        return message

    def set_conversation_pinned(self, conversation_id: str, *, pinned: bool) -> dict[str, Any]:
        """Set the requester's local pin for a conversation."""

        self.get_conversation(conversation_id)
        result = self.conversations.set_pinned(
            conversation_id, self.actor_id, pinned=pinned
        )
        changed = bool(result.pop("changed", True))
        if changed:
            self._emit(
                "conversation.pinned" if pinned else "conversation.unpinned",
                "conversation",
                conversation_id,
                {"actor_id": self.actor_id, "pinned": pinned},
            )
        return result

    def create_conversation(
        self,
        *,
        kind: str,
        title: str = "",
        agent_ids: Sequence[str] = (),
        reply_mode: str = "mention_only",
        coordinator_agent_id: Optional[str] = None,
        allow_at_all: bool = False,
        max_fanout: int = 8,
    ) -> dict[str, Any]:
        agent_ids = tuple(str(agent_id).strip() for agent_id in agent_ids if str(agent_id).strip())
        if not agent_ids:
            raise ContractError("at least one agent is required")
        if kind == "direct" and len(agent_ids) != 1:
            raise ContractError("a direct conversation must have exactly one agent")
        if kind == "group" and len(set(agent_ids)) < 2:
            raise ContractError("a group conversation must have at least two agents")

        agent_actors: list[str] = []
        for agent_id in agent_ids:
            installation = self.agents.get_installation(agent_id)
            if installation is None or installation["workspace_id"] != self.workspace_id:
                raise ContractError(f"agent is missing from local workspace: {agent_id}")
            agent_actors.append(installation["actor_id"])
        if kind == "direct":
            existing = next(
                (
                    item
                    for item in self.conversations.list_conversations(
                        self.workspace_id,
                        self.actor_id,
                        limit=200,
                    )
                    if item["conversation_kind"] == "direct"
                    and item.get("agent_member_ids") == [agent_ids[0]]
                ),
                None,
            )
            if existing is not None:
                return self.get_conversation(existing["conversation_id"])
        conversation_id = new_domain_id(DomainIdPrefix.CONVERSATION)
        conversation = self.conversations.create_conversation(
            conversation_id,
            self.workspace_id,
            kind,
            self.actor_id,
            title=title,
            reply_mode="coordinator" if kind == "direct" else reply_mode,
            coordinator_agent_id=coordinator_agent_id,
            allow_at_all=allow_at_all,
            max_fanout=max_fanout,
        )
        self.conversations.add_member(
            conversation_id,
            self.actor_id,
            member_role="owner",
        )
        for actor_id in agent_actors:
            self.conversations.add_member(
                conversation_id,
                actor_id,
                requester_actor_id=self.actor_id,
            )
        result = self.get_conversation(conversation_id)
        self._emit(
            "conversation.created",
            "conversation",
            conversation_id,
            {"conversation_kind": result["conversation_kind"]},
        )
        return result

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: Optional[str] = None,
        reply_mode: Optional[str] = None,
        coordinator_agent_id: Optional[str] = None,
        allow_at_all: Optional[bool] = None,
        max_fanout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Update conversation policy and publish one canonical change event."""

        before = self.get_conversation(conversation_id)
        updated = self.conversations.update_conversation(
            conversation_id,
            self.actor_id,
            title=title,
            reply_mode=reply_mode,
            coordinator_agent_id=coordinator_agent_id,
            allow_at_all=allow_at_all,
            max_fanout=max_fanout,
        )
        self._emit(
            "conversation.updated",
            "conversation",
            conversation_id,
            {
                "changed": [
                    key
                    for key in ("title", "reply_mode", "coordinator_agent_id", "allow_at_all", "max_fanout")
                    if before.get(key) != updated.get(key)
                ]
            },
        )
        return self.get_conversation(conversation_id)

    def add_conversation_agent(self, conversation_id: str, agent_id: str) -> dict[str, Any]:
        """Add an installed workspace agent to a group through its admin ACL."""

        conversation = self.get_conversation(conversation_id)
        if conversation["conversation_kind"] != "group":
            raise ContractError("only group conversations accept additional agents")
        installation = self.agents.get_installation(str(agent_id or "").strip())
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError("agent is missing from local workspace")
        self.conversations.add_member(
            conversation_id,
            installation["actor_id"],
            requester_actor_id=self.actor_id,
        )
        self._emit(
            "conversation.member_added",
            "conversation",
            conversation_id,
            {"agent_id": installation["agent_id"], "actor_id": installation["actor_id"]},
        )
        return self.get_conversation(conversation_id)

    def remove_conversation_agent(self, conversation_id: str, agent_id: str) -> dict[str, Any]:
        """Remove an installed agent from a group while preserving its history."""

        conversation = self.get_conversation(conversation_id)
        if conversation["conversation_kind"] != "group":
            raise ContractError("only group conversations support agent removal")
        if len(conversation.get("agent_member_ids") or ()) <= 2:
            raise ContractError("group conversations must keep at least two agents")

        installation = self.agents.get_installation(str(agent_id or "").strip())
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError("agent is missing from local workspace")
        self.conversations.remove_member(
            conversation_id,
            installation["actor_id"],
            self.actor_id,
        )
        self._emit(
            "conversation.member_removed",
            "conversation",
            conversation_id,
            {"agent_id": installation["agent_id"], "actor_id": installation["actor_id"]},
        )
        return self.get_conversation(conversation_id)

    def transition_conversation(
        self,
        conversation_id: str,
        target_status: str,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        before = self.get_conversation(conversation_id)
        updated = self.conversations.transition_conversation(
            conversation_id,
            self.actor_id,
            target_status,
            reason=reason,
        )
        self._emit(
            "conversation.transitioned",
            "conversation",
            conversation_id,
            {"from": before["status"], "to": updated["status"], "reason": str(reason or "").strip()},
        )
        return self.get_conversation(conversation_id)

    def send_message(
        self,
        conversation_id: str,
        *,
        body: str,
        message_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        mention_actor_ids: Sequence[str] = (),
        at_all: bool = False,
        fanout_confirmed: bool = False,
        thread_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        quote_message_id: Optional[str] = None,
        budget: Optional[Mapping[str, Any]] = None,
        priority: int = 0,
    ) -> WorkSubmission:
        conversation = self.get_conversation(conversation_id)
        context = {
            agent_id: self._permission_context(agent_id)
            for agent_id in conversation["agent_member_ids"]
        }
        stable_message_id = message_id or (
            _stable_id("message", f"{conversation_id}:{client_request_id}")
            if client_request_id
            else new_domain_id(DomainIdPrefix.MESSAGE)
        )
        submitted = self.coordinator.submit_message(
            message_id=stable_message_id,
            conversation_id=conversation_id,
            sender=self.requester,
            body=body,
            context_by_agent=context,
            message_idempotency_key=client_request_id,
            correlation_id=client_request_id or stable_message_id,
            thread_id=thread_id,
            parent_message_id=parent_message_id,
            quote_message_id=quote_message_id,
            mention_actor_ids=tuple(mention_actor_ids),
            at_all=at_all,
            fanout_confirmed=fanout_confirmed,
            budget=self._effective_work_budget(
                conversation["agent_member_ids"], budget
            ),
            priority=priority,
        )
        if self.search is not None and submitted.message is not None:
            self.search.index_message(
                submitted.message["message_id"],
                self.workspace_id,
                conversation_id,
                submitted.message["body"],
            )
        if submitted.message is not None:
            self._notify_conversation(
                conversation,
                notification_kind="message.created",
                aggregate=ResourceRef("message", submitted.message["message_id"]),
                dedupe_key=f"message:{submitted.message['message_id']}",
                mention_actor_ids=submitted.message.get("mentions", ()),
            )
        return submitted

    def add_message_attachment(
        self, message_id: str, attachment: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Attach one already-stored immutable blob to a visible message."""

        message = self.get_message(message_id)
        if message is None:
            raise ContractError("message not found")
        result = self.conversations.add_attachment(message_id, self.actor_id, attachment)
        self._emit(
            "message.attachment_added",
            "message",
            message_id,
            {
                "conversation_id": message["conversation_id"],
                "attachment_id": result["attachment_id"],
                "filename": result["filename"],
                "size_bytes": result["size_bytes"],
            },
        )
        return result

    def get_message_attachment(self, message_id: str, attachment_id: str) -> dict[str, Any]:
        """Resolve attachment metadata only after enforcing message membership."""

        message = self.get_message(message_id)
        if message is None:
            raise ContractError("message not found")
        attachment = next(
            (
                item
                for item in message.get("attachments", ())
                if item.get("attachment_id") == attachment_id
            ),
            None,
        )
        if attachment is None:
            raise ContractError("attachment not found")
        return attachment

    def set_message_translation(
        self, message_id: str, *, language: str, body: str, model: str = ""
    ) -> dict[str, Any]:
        """Persist a requester-visible translation without changing the source message."""

        message = self.get_message(message_id)
        if message is None:
            raise ContractError("message not found")
        result = self.conversations.set_translation(
            message_id,
            self.actor_id,
            language,
            body,
            model=model,
        )
        self._emit(
            "message.translated",
            "message",
            message_id,
            {
                "conversation_id": message["conversation_id"],
                "language": result["language"],
                "model": result.get("model", ""),
            },
        )
        return result

    def post_agent_message(
        self,
        conversation_id: str,
        *,
        agent_id: str,
        body: str,
        message_key: str,
        request_id: str,
        run_id: str,
        correlation_id: str,
        thread_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        mention_actor_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Post one idempotent agent-authored message in its current conversation.

        This is deliberately separate from :meth:`send_message`: an agent may publish
        progress or coordination context, but an agent-authored message never becomes an
        implicit WorkRequest.  Executable delegation must use a structured handoff.
        """

        conversation = self.get_conversation(conversation_id)
        message_key = str(message_key or "").strip()
        request_id = str(request_id or "").strip()
        run_id = str(run_id or "").strip()
        if not message_key or len(message_key) > 200:
            raise ContractError("agent message_key is required and must be at most 200 characters")
        if not request_id or not run_id:
            raise ContractError("agent message request and run identities are required")
        installation = self.agents.get_installation(agent_id)
        if (
            installation is None
            or installation["workspace_id"] != self.workspace_id
            or not any(
                member["actor_id"] == installation["actor_id"]
                and member["left_at"] is None
                for member in conversation["members"]
            )
        ):
            raise ContractError("agent is not an active member of this conversation")
        author = ActorRef(
            installation["actor_id"],
            ActorKind.AGENT,
            str(
                next(
                    (
                        member.get("display_name")
                        for member in conversation["members"]
                        if member.get("actor_id") == installation["actor_id"]
                    ),
                    "",
                )
            ),
        )
        stable_key = f"{request_id}:{message_key}"
        message_id = _stable_id("message", f"team-agent:{stable_key}")
        message = self.conversations.append_message(
            message_id,
            conversation_id,
            author,
            body,
            _stable_id("message-agent", stable_key),
            correlation_id,
            thread_id=thread_id,
            parent_message_id=parent_message_id,
            # The message is replayed across run retries.  Keep its immutable
            # causation stable at the request level; the run event stream carries
            # the attempt-specific run identity.
            causation_id=request_id,
            mentions=mention_actor_ids,
        )
        if self.search is not None:
            self.search.index_message(
                message["message_id"],
                self.workspace_id,
                conversation_id,
                message["body"],
            )
        self._notify_conversation(
            conversation,
            notification_kind="message.created",
            aggregate=ResourceRef("message", message["message_id"]),
            dedupe_key=f"message:{message['message_id']}",
            mention_actor_ids=mention_actor_ids,
        )
        self._emit(
            "message.created",
            "message",
            message["message_id"],
            {
                "conversation_id": conversation_id,
                "source": "agent_message",
                "agent_id": agent_id,
                "request_id": request_id,
                "message_key": message_key,
            },
            event_id=_stable_id("event", f"agent-message:{message['message_id']}"),
            correlation_id=correlation_id,
            causation_id=request_id,
            actor_id=author.id,
            occurred_at=message["created_at"],
        )
        return message

    def append_agent_message(
        self,
        conversation_id: str,
        *,
        agent_id: str,
        body: str,
        run_id: str,
        correlation_id: str,
        thread_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        reasoning: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Persist one worker reply without routing it back into the queue.

        Agent output is a conversation fact, not a new user work request.  The
        stable message/event identities make a crash after the DB commit safe to
        replay before the worker ACKs its delivery.
        """

        conversation = self.get_conversation(conversation_id)
        run = self.coordinator.work.get_run(run_id)
        if run is None or run["workspace_id"] != self.workspace_id:
            raise ContractError("agent reply run is missing or belongs to another workspace")
        request = self.coordinator.work.get_request(run["request_id"])
        if (
            request is None
            or request["workspace_id"] != self.workspace_id
            or request.get("conversation_id") != conversation_id
            or run["agent_id"] != agent_id
        ):
            raise ContractError("agent reply run is not bound to this conversation and agent")
        if str(correlation_id).strip() != str(request["correlation_id"]).strip():
            raise ContractError("agent reply correlation does not match its request")
        delivery_id = str(run["delivery_id"])
        reply_key = f"{self.workspace_id}:{delivery_id}"
        installation = self.agents.get_installation(agent_id)
        if (
            installation is None
            or installation["workspace_id"] != self.workspace_id
            or not any(
                member["actor_id"] == installation["actor_id"]
                and member["left_at"] is None
                for member in conversation["members"]
            )
        ):
            raise ContractError("agent is not an active member of this conversation")
        author = ActorRef(
            installation["actor_id"],
            ActorKind.AGENT,
            str(
                next(
                    (
                        member.get("display_name")
                        for member in conversation["members"]
                        if member.get("actor_id") == installation["actor_id"]
                    ),
                    "",
                )
            ),
        )
        message_id = _stable_id("message", f"team-reply:{reply_key}")
        message = self.conversations.append_message(
            message_id,
            conversation_id,
            author,
            body,
            _stable_id("message-reply", reply_key),
            correlation_id,
            thread_id=thread_id,
            parent_message_id=parent_message_id,
            # The logical request is the causation; run_id is attempt-specific
            # and must not make a replay look like a second conversation fact.
            causation_id=request["request_id"],
            reasoning=reasoning,
            metadata=metadata,
        )
        if self.search is not None:
            self.search.index_message(
                message["message_id"],
                self.workspace_id,
                conversation_id,
                message["body"],
            )
        self._notify_conversation(
            conversation,
            notification_kind="message.created",
            aggregate=ResourceRef("message", message["message_id"]),
            dedupe_key=f"message:{message['message_id']}",
        )
        self._emit(
            "message.created",
            "message",
            message["message_id"],
            {
                "conversation_id": conversation_id,
                "source": "agent_reply",
                "delivery_id": delivery_id,
                "reasoning": reasoning,
                "metadata": metadata,
            },
            event_id=_stable_id("event", f"agent-reply:{message['message_id']}"),
            correlation_id=correlation_id,
            causation_id=request["request_id"],
            actor_id=author.id,
            occurred_at=message["created_at"],
        )
        return message

    def edit_message(
        self,
        message_id: str,
        body: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        before = self.get_message(message_id)
        if before is None:
            raise ContractError("message not found")
        updated = self.conversations.edit_message(
            message_id,
            self.actor_id,
            body,
            expected_revision=expected_revision,
        )
        if self.search is not None:
            self.search.index_message(
                message_id,
                self.workspace_id,
                before["conversation_id"],
                updated["body"],
            )
        self._emit(
            "message.edited",
            "message",
            message_id,
            {"conversation_id": before["conversation_id"], "revision": updated.get("revision_number")},
        )
        return updated

    def delete_message(self, message_id: str) -> dict[str, Any]:
        before = self.get_message(message_id)
        if before is None:
            raise ContractError("message not found")
        updated = self.conversations.delete_message(message_id, self.actor_id)
        if self.search is not None:
            self.search.remove_message(message_id)
        self._emit(
            "message.deleted",
            "message",
            message_id,
            {"conversation_id": before["conversation_id"]},
        )
        return updated

    def set_message_reaction(self, message_id: str, emoji: str, *, active: bool = True) -> dict[str, Any]:
        message = self.get_message(message_id)
        if message is None:
            raise ContractError("message not found")
        result = self.conversations.set_reaction(
            message_id,
            self.actor_id,
            emoji,
            active=active,
        )
        self._emit(
            "message.reaction_changed",
            "message",
            message_id,
            {"conversation_id": message["conversation_id"], "emoji": emoji, "active": active},
        )
        return result

    def pin_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        """Pin one visible message after the repository enforces admin ACL."""

        conversation = self.get_conversation(conversation_id)
        message = self.get_message(message_id)
        if message is None or message["conversation_id"] != conversation["conversation_id"]:
            raise ContractError("message not found in conversation")
        pin = self.conversations.pin_message(conversation_id, message_id, self.actor_id)
        self._emit(
            "message.pinned",
            "message",
            message_id,
            {
                "conversation_id": conversation_id,
                "pinned_by_actor_id": self.actor_id,
            },
        )
        return pin

    def unpin_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        """Remove one pin idempotently and publish only a real state change."""

        conversation = self.get_conversation(conversation_id)
        message = self.get_message(message_id)
        if message is None or message["conversation_id"] != conversation["conversation_id"]:
            raise ContractError("message not found in conversation")
        removed = self.conversations.unpin_message(conversation_id, message_id, self.actor_id)
        if removed:
            self._emit(
                "message.unpinned",
                "message",
                message_id,
                {
                    "conversation_id": conversation_id,
                    "unpinned_by_actor_id": self.actor_id,
                },
            )
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "removed": removed,
        }

    def list_pinned_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return pins only after the requester-scoped conversation ACL passes."""

        self.get_conversation(conversation_id)
        return self.conversations.list_pinned_messages(conversation_id, self.actor_id)

    def open_thread(self, conversation_id: str, root_message_id: str) -> dict[str, Any]:
        """Open or reuse a durable thread rooted at one visible message."""

        conversation = self.get_conversation(conversation_id)
        root = self.get_message(root_message_id)
        if root is None or root["conversation_id"] != conversation["conversation_id"]:
            raise ContractError("thread root message is not visible in this conversation")
        thread_id = str(root.get("thread_id") or "").strip()
        created = False
        if thread_id:
            thread = self.conversations.get_thread(thread_id)
        else:
            thread_id = _stable_id("thread", f"{conversation_id}:{root_message_id}")
            try:
                thread = self.conversations.create_thread(
                    thread_id, conversation_id, root_message_id
                )
                created = True
            except PersistenceConflictError:
                # Another window may have opened the same thread between the
                # root read and the insert. The deterministic id makes the
                # command safe to replay.
                thread = self.conversations.get_thread(thread_id)
        if thread is None or thread["conversation_id"] != conversation["conversation_id"]:
            raise ContractError("thread could not be opened")
        if created:
            self._emit(
                "thread.created",
                "thread",
                thread_id,
                {"conversation_id": conversation_id, "root_message_id": root_message_id},
            )
        messages = self.conversations.list_messages(
            conversation_id,
            thread_id=thread_id,
            requester_actor_id=self.actor_id,
            include_tombstones=True,
        )
        return {"thread": thread, **messages}

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Return one recipient-scoped thread and its durable messages."""

        self._require_workspace_member()
        thread = self.conversations.get_thread(thread_id)
        if thread is None or thread["workspace_id"] != self.workspace_id:
            raise ContractError("thread not found")
        messages = self.conversations.list_messages(
            thread["conversation_id"],
            thread_id=thread_id,
            requester_actor_id=self.actor_id,
            include_tombstones=True,
        )
        return {"thread": thread, **messages}

    def set_read_cursor(self, conversation_id: str, message_id: Optional[str]) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        result = self.conversations.set_read_cursor(conversation_id, self.actor_id, message_id)
        self._emit(
            "conversation.read_cursor_changed",
            "conversation",
            conversation_id,
            {"message_id": message_id},
        )
        return result

    def list_tasks(
        self,
        *,
        statuses: Sequence[TaskStatus | str] = (),
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        return self.tasks.list_tasks(
            self.workspace_id,
            statuses=statuses,
            after_cursor=cursor,
            limit=limit,
        )

    def list_crew_runs(
        self,
        *,
        statuses: Sequence[str] = (),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List crew runs visible in this workspace, including their members."""

        self._require_workspace_member()
        return self.coordinator.work.list_crew_runs(
            self.workspace_id,
            statuses=statuses,
            limit=limit,
        )

    def get_crew_run(self, crew_run_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        crew = self.coordinator.work.get_crew_run(crew_run_id)
        if crew is None or crew["workspace_id"] != self.workspace_id:
            raise ContractError("crew run not found")
        return crew

    def create_crew_run(
        self,
        *,
        agent_ids: Sequence[str],
        root_task_id: Optional[str] = None,
        agent_roles: Optional[Mapping[str, str]] = None,
        client_request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create one bounded set of persistent agents for a task work graph."""

        self._require_workspace_member()
        normalized_agents = tuple(
            dict.fromkeys(str(agent_id).strip() for agent_id in agent_ids if str(agent_id).strip())
        )
        if not normalized_agents:
            raise ContractError("a crew run requires at least one agent")
        for agent_id in normalized_agents:
            installation = self.agents.get_installation(agent_id)
            if (
                installation is None
                or installation["workspace_id"] != self.workspace_id
                or installation["status"] in {"paused", "archived"}
            ):
                raise ContractError(f"crew agent cannot receive work: {agent_id}")
        if root_task_id is not None:
            task = self.get_task(root_task_id)
            if task["status"] in {TaskStatus.DONE.value, TaskStatus.CANCELED.value}:
                raise ContractError("a terminal task cannot start a crew run")
        roles = {str(key): str(value) for key, value in (agent_roles or {}).items()}
        client_key = str(client_request_id or "").strip()
        if client_key and len(client_key) > 200:
            raise ContractError("client_request_id must be at most 200 characters")
        crew_run_id = (
            _stable_id("crew-run", f"{self.workspace_id}:{client_key}")
            if client_key
            else new_domain_id(DomainIdPrefix.CREW_RUN)
        )
        correlation_id = (
            _stable_id("crew-correlation", f"{self.workspace_id}:{client_key}")
            if client_key
            else new_domain_id(DomainIdPrefix.EVENT)
        )
        crew = self.coordinator.work.create_crew_run(
            crew_run_id,
            self.workspace_id,
            self.actor_id,
            correlation_id,
            root_task_id=root_task_id,
            agent_ids=normalized_agents,
            agent_roles=roles,
        )
        self._emit(
            "crew_run.created",
            "crew_run",
            crew_run_id,
            {
                "root_task_id": root_task_id,
                "agent_ids": list(normalized_agents),
            },
        )
        return crew

    def transition_crew_run(self, crew_run_id: str, status: str) -> dict[str, Any]:
        self._require_workspace_member()
        before = self.get_crew_run(crew_run_id)
        updated = self.coordinator.work.transition_crew_run(crew_run_id, status)
        self._emit(
            "crew_run.transitioned",
            "crew_run",
            crew_run_id,
            {"from": before["status"], "to": updated["status"]},
        )
        return updated

    def list_integrations(
        self, *, task_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.integrations is None:
            raise ContractError("Team integration service is unavailable")
        if task_id is not None:
            self.get_task(task_id)
        return self.integrations.list_for_workspace(
            self.workspace_id, task_id=task_id, limit=limit
        )

    def get_integration(self, integration_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.integrations is None:
            raise ContractError("Team integration service is unavailable")
        integration = self.integrations.get(integration_id)
        if integration is None or integration["workspace_id"] != self.workspace_id:
            raise ContractError("integration entry not found")
        return integration

    def create_integration(
        self,
        *,
        task_id: str,
        source_ref: str,
        target_ref: str,
        run_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        source_agent_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.integrations is None:
            raise ContractError("Team integration service is unavailable")
        task = self.get_task(task_id)
        if task["status"] in {TaskStatus.CANCELED.value, TaskStatus.DONE.value}:
            raise ContractError("terminal tasks cannot receive integration work")
        if run_id is not None:
            run = self.coordinator.work.get_run(run_id)
            if run is None or run["workspace_id"] != self.workspace_id or run["request_id"] is None:
                raise ContractError("integration run is missing or belongs to another workspace")
        if artifact_id is not None:
            self.get_artifact(artifact_id)
        if source_agent_id is not None:
            agent = self.agents.get_installation(source_agent_id)
            if agent is None or agent["workspace_id"] != self.workspace_id:
                raise ContractError("integration source agent is missing from local workspace")
        key = str(idempotency_key or "").strip()
        if key and len(key) > 200:
            raise ContractError("integration idempotency key must be at most 200 characters")
        integration_id = (
            _stable_id("integration", f"{self.workspace_id}:{key}")
            if key
            else new_domain_id(DomainIdPrefix.INTEGRATION)
        )
        result = self.integrations.create(
            integration_id,
            self.workspace_id,
            task_id,
            self.actor_id,
            source_ref,
            target_ref,
            run_id=run_id,
            artifact_id=artifact_id,
            source_agent_id=source_agent_id,
            idempotency_key=key or integration_id,
        )
        self._emit(
            "integration.queued",
            "integration",
            integration_id,
            {"task_id": task_id, "artifact_id": artifact_id},
        )
        return result

    def transition_integration(
        self, integration_id: str, status: str, *, conflict_detail: str = ""
    ) -> dict[str, Any]:
        self.get_integration(integration_id)
        if self.integrations is None:
            raise ContractError("Team integration service is unavailable")
        updated = self.integrations.transition(
            integration_id, status, conflict_detail=conflict_detail
        )
        self._emit(
            "integration.status_changed",
            "integration",
            integration_id,
            {"status": updated["status"], "conflict_detail": updated["conflict_detail"]},
        )
        return updated

    # Connector authority is kept as a separate service because provider
    # credentials and transport adapters must never become part of the Team
    # conversation or agent records.  These wrappers keep the HTTP/UI boundary
    # workspace-scoped while allowing the worker to call the two runtime-only
    # operations (action execution and outbound queueing).
    def list_connector_identities(
        self, *, connector_kind: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.list_identities(connector_kind=connector_kind, limit=limit)

    def get_connector_identity(self, record_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.get_identity(record_id)

    def register_connector_identity(self, **kwargs: Any) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.register_identity(**kwargs)

    def transition_connector_identity(self, record_id: str, status: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.transition_identity(record_id, status)

    def update_connector_identity_health(self, record_id: str, health_status: str, **kwargs: Any) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.update_identity_health(record_id, health_status, **kwargs)

    def grant_connector_agent(self, **kwargs: Any) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.grant_agent(**kwargs)

    def list_connector_grants(
        self, *, agent_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.list_grants(agent_id=agent_id, limit=limit)

    def list_connector_bindings(
        self, *, agent_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.list_bindings(agent_id=agent_id, limit=limit)

    def revoke_connector_grant(self, grant_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.revoke_grant(grant_id)

    def revoke_connector_binding(self, binding_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.revoke_binding(binding_id)

    def ingest_connector_event(self, **kwargs: Any) -> Any:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.ingest_event(**kwargs)

    def list_connector_events(
        self, *, status: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.list_events(status=status, limit=limit)

    def list_connector_outbox(
        self, *, statuses: Sequence[str] = (), limit: int = 100
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.list_outbox(statuses=statuses, limit=limit)

    def send_connector_outbox(self, delivery_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.send_outbox(delivery_id)

    def queue_connector_result(self, **kwargs: Any) -> dict[str, Any]:
        if self.connectors is None:
            raise ContractError("Team connector service is unavailable")
        return self.connectors.queue_result(**kwargs)

    def execute_connector_action(self, **kwargs: Any) -> dict[str, Any]:
        if self.connectors is None:
            return {"error": "Team connector service is unavailable", "authorized": False}
        return self.connectors.execute_action(**kwargs)

    def get_task(self, task_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        task = self.tasks.get_task(task_id)
        if task is None or task["workspace_id"] != self.workspace_id:
            raise ContractError("task not found")
        return task

    def list_runs(
        self,
        *,
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        statuses: Sequence[str] = (),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if agent_id is not None:
            installation = self.agents.get_installation(agent_id)
            if installation is None or installation["workspace_id"] != self.workspace_id:
                raise ContractError("agent is not visible in this workspace")
        if task_id is not None:
            self.get_task(task_id)
        if conversation_id is not None:
            self.get_conversation(conversation_id)
        return self.coordinator.work.list_runs(
            self.workspace_id,
            agent_id=agent_id,
            task_id=task_id,
            conversation_id=conversation_id,
            statuses=statuses,
            limit=limit,
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        """Return a run only after proving its workspace boundary."""

        self._require_workspace_member()
        run = self.coordinator.work.get_run(run_id)
        if run is None or run["workspace_id"] != self.workspace_id:
            raise ContractError("run not found")
        installation = self.agents.get_installation(run["agent_id"])
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError("run agent is not visible in this workspace")
        delivery = self.coordinator.work.get_delivery(run["delivery_id"])
        if delivery is None or delivery["workspace_id"] != self.workspace_id:
            raise ContractError("run delivery is not visible")
        request = self.coordinator.work.get_request(run["request_id"])
        if request is None or request["workspace_id"] != self.workspace_id:
            raise ContractError("run request is not visible")
        return {
            "run": run,
            "delivery": delivery,
            "usage": self.coordinator.work.get_usage(run["request_id"]),
            "events": self.coordinator.work.list_run_events(run_id),
            "approvals": (
                self.governance.list_approvals(
                    self.workspace_id,
                    self.actor_id,
                    run_id=run_id,
                    limit=200,
                )
                if self.governance is not None
                else []
            ),
        }

    def get_task_thread(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        thread_id = str(task.get("canonical_thread_id") or "").strip()
        if not thread_id:
            raise ContractError("task has no canonical thread")
        thread = self.conversations.get_thread(thread_id)
        if thread is None or thread["workspace_id"] != self.workspace_id:
            raise ContractError("task canonical thread not found")
        messages = self.conversations.list_messages(
            thread["conversation_id"],
            thread_id=thread_id,
            requester_actor_id=self.actor_id,
            include_tombstones=True,
        )
        return {"task": task, "thread": thread, **messages}

    def post_task_summary(
        self,
        task_id: str,
        *,
        conversation_id: str,
        summary: str,
        client_request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Publish a compact group-facing task summary with a thread deep link."""

        task = self.get_task(task_id)
        thread = self.conversations.get_thread(task["canonical_thread_id"] or "")
        if thread is None:
            raise ContractError("task canonical thread not found")
        conversation = self.get_conversation(conversation_id)
        if conversation["conversation_kind"] != "group":
            raise ContractError("task summaries require a group conversation")
        summary = str(summary or "").strip()
        if not summary or len(summary) > 2_000:
            raise ContractError("task summary is required and must be at most 2000 characters")
        key = str(client_request_id or f"task:{task_id}:summary:{conversation_id}").strip()
        message = self.conversations.append_message(
            _stable_id("task-summary", key),
            conversation_id,
            self.requester,
            f"[{task['status']}] {task['title']}: {summary}\nDetails: "
            f"astra://conversation/{thread['conversation_id']}/thread/{thread['thread_id']}",
            key,
            _stable_id("task-summary-correlation", key),
            client_request_id=key,
        )
        if self.search is not None:
            self.search.index_message(
                message["message_id"], self.workspace_id, conversation_id, message["body"]
            )
        self._emit(
            "task.summary_posted",
            "task",
            task_id,
            {"conversation_id": conversation_id, "message_id": message["message_id"]},
        )
        return message

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        artifact = self.artifacts.get_accessible_artifact(
            artifact_id, self.workspace_id, self.actor_id
        )
        if artifact is None:
            raise ContractError("artifact not found")
        # Storage refs are an internal fetch detail.  Metadata and provenance are
        # safe to return to the UI, while the actual blob is resolved by a later
        # artifact-download command with its own ACL check.
        return self._redact_artifact(artifact)

    def list_artifacts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        return [
            self._redact_artifact(artifact)
            for artifact in self.artifacts.list_accessible_artifacts(
                self.workspace_id, self.actor_id, limit=limit
            )
        ]

    def publish_artifact(
        self,
        *,
        source_run_id: str,
        filename: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        visibility: str,
        scope_id: str,
        storage_ref: str,
        source_task_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        revision_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Publish an opaque artifact pointer after binding it to a real run."""

        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        run = self.coordinator.work.get_run(source_run_id)
        if run is None or run["workspace_id"] != self.workspace_id:
            raise ContractError("artifact source run is missing or belongs to another workspace")
        request = self.coordinator.work.get_request(run["request_id"])
        resolved_task_id = source_task_id or (request or {}).get("task_id")
        if resolved_task_id is not None:
            self.get_task(resolved_task_id)
        try:
            normalized_visibility = ArtifactVisibility(visibility)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid artifact visibility") from error
        boundary = ContextBoundary(
            {
                ArtifactVisibility.AGENT_PRIVATE: ContextScope.AGENT_PRIVATE,
                ArtifactVisibility.DIRECT_MESSAGE: ContextScope.DIRECT_MESSAGE,
                ArtifactVisibility.GROUP: ContextScope.GROUP,
                ArtifactVisibility.TASK: ContextScope.TASK,
                ArtifactVisibility.WORKSPACE: ContextScope.WORKSPACE,
            }[normalized_visibility],
            scope_id,
        )
        self._require_scope_visible(boundary.scope, boundary.scope_id)
        installation = self.agents.get_installation(run["agent_id"])
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError("artifact publisher agent is missing from local workspace")
        publisher = ActorRef(installation["actor_id"], ActorKind.AGENT, installation.get("display_name", ""))
        key = str(idempotency_key or "").strip()
        if not key:
            key = f"{source_run_id}:{filename}:{sha256}"
        artifact_id = artifact_id or _stable_id("artifact", f"{self.workspace_id}:{key}")
        revision_id = revision_id or _stable_id("artifact-revision", f"{artifact_id}:{key}")
        publication = ArtifactPublication(
            artifact_id=artifact_id,
            revision_id=revision_id,
            workspace_id=self.workspace_id,
            source_run_id=source_run_id,
            source_task_id=resolved_task_id,
            publisher=publisher,
            requester_id=self.actor_id,
            visibility=normalized_visibility,
            scope_id=scope_id,
            filename=filename,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
            idempotency_key=key,
            correlation_id=(request or {}).get("correlation_id") or _stable_id("artifact-correlation", key),
            source_ref=ResourceRef("run", source_run_id),
            metadata=metadata or {},
        )
        artifact = self.artifacts.publish(publication, storage_ref)
        if self.search is not None:
            revision = artifact.get("revision") or {}
            self.search.index_artifact(
                artifact["artifact_id"],
                self.workspace_id,
                revision.get("filename", filename),
                revision.get("media_type", media_type),
                revision.get("metadata", metadata or {}),
            )
        self._emit(
            "artifact.published",
            "artifact",
            artifact["artifact_id"],
            {"source_run_id": source_run_id, "source_task_id": resolved_task_id},
        )
        return self._redact_artifact(artifact)

    def artifact_download(self, artifact_id: str) -> dict[str, Any]:
        """Return a safe opaque storage descriptor; never expose a local sandbox path."""

        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        artifact = self.artifacts.get_accessible_artifact(
            artifact_id, self.workspace_id, self.actor_id
        )
        if artifact is None or not artifact.get("revisions"):
            raise ContractError("artifact not found")
        revision = artifact["revisions"][-1]
        return {
            "artifact_id": artifact["artifact_id"],
            "revision_id": revision["artifact_revision_id"],
            "filename": revision["filename"],
            "media_type": revision["media_type"],
            "size_bytes": revision["size_bytes"],
            "sha256": revision["sha256"],
            "storage_ref": revision["storage_ref"],
            "content_available": revision["storage_ref"].startswith("attachment://"),
        }

    def artifact_preview(self, artifact_id: str) -> dict[str, Any]:
        """Return only publisher-supplied preview text, never sandbox bytes."""

        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        artifact = self.artifacts.get_accessible_artifact(
            artifact_id, self.workspace_id, self.actor_id
        )
        if artifact is None or not artifact.get("revisions"):
            raise ContractError("artifact not found")
        revision = artifact["revisions"][-1]
        metadata = revision.get("metadata") or {}
        preview = metadata.get("preview") if isinstance(metadata, Mapping) else None
        if preview is not None and (not isinstance(preview, str) or len(preview) > 20_000):
            raise ContractError("artifact preview metadata is invalid")
        return {
            "artifact_id": artifact["artifact_id"],
            "revision_id": revision["artifact_revision_id"],
            "filename": revision["filename"],
            "media_type": revision["media_type"],
            "preview": preview,
            "preview_available": preview is not None,
        }

    def transition_artifact(self, artifact_id: str, status: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        self.get_artifact(artifact_id)
        updated = self.artifacts.transition(artifact_id, status, actor_id=self.actor_id)
        self._emit(
            "artifact.status_changed",
            "artifact",
            artifact_id,
            {"status": updated["status"]},
        )
        return self._redact_artifact(updated)

    def quarantine_artifact(self, artifact_id: str, reason: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        self.get_artifact(artifact_id)
        updated = self.artifacts.quarantine(
            artifact_id, actor_id=self.actor_id, reason=reason
        )
        self._emit(
            "artifact.quarantined",
            "artifact",
            artifact_id,
            {"reason": str(reason).strip()},
        )
        return self._redact_artifact(updated)

    def release_artifact_quarantine(self, artifact_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        updated = self.artifacts.release_quarantine(artifact_id, actor_id=self.actor_id)
        self._emit("artifact.quarantine_released", "artifact", artifact_id, {})
        return self._redact_artifact(updated)

    def link_artifact(self, artifact_id: str, aggregate: ResourceRef) -> dict[str, Any]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        self.get_artifact(artifact_id)
        if aggregate.kind == "message":
            if self.get_message(aggregate.id) is None:
                raise ContractError("linked message is not visible")
        elif aggregate.kind == "conversation":
            self.get_conversation(aggregate.id)
        elif aggregate.kind == "task":
            self.get_task(aggregate.id)
        elif aggregate.kind == "run":
            run = self.coordinator.work.get_run(aggregate.id)
            if run is None or run["workspace_id"] != self.workspace_id:
                raise ContractError("linked run is not visible")
        elif aggregate.kind == "approval":
            with self.identities.database._lock:
                row = self.identities.database._conn.execute(
                    "SELECT workspace_id FROM approvals WHERE approval_id = ?",
                    (aggregate.id,),
                ).fetchone()
            if row is None or row["workspace_id"] != self.workspace_id:
                raise ContractError("linked approval is not visible")
        else:
            raise ContractError("artifact links support messages, tasks, runs, approvals, and conversations")
        link = self.artifacts.link(artifact_id, aggregate)
        self._emit(
            "artifact.linked",
            "artifact",
            artifact_id,
            {"aggregate": aggregate.to_dict()},
        )
        return link

    def compare_artifact_revisions(
        self, artifact_id: str, left_revision_id: str, right_revision_id: str
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.artifacts is None:
            raise ContractError("Team artifact service is unavailable")
        self.get_artifact(artifact_id)
        return self.artifacts.compare_revisions(
            artifact_id, left_revision_id, right_revision_id
        )

    def build_context(
        self,
        conversation_id: str,
        *,
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        max_messages: int = 50,
        max_chars: int = 32_000,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.context_builder is None:
            raise ContractError("Team context service is unavailable")
        principal_actor_id = self.actor_id
        private_agent_id = None
        if agent_id is not None:
            installation = self.agents.get_installation(agent_id)
            if installation is None or installation["workspace_id"] != self.workspace_id:
                raise ContractError("agent is missing from local workspace")
            principal_actor_id = installation["actor_id"]
            private_agent_id = agent_id
        return self.context_builder.build(
            conversation_id,
            self.actor_id,
            principal_actor_id=principal_actor_id,
            private_agent_id=private_agent_id,
            task_id=task_id,
            max_messages=max_messages,
            max_chars=max_chars,
        )

    def create_memory(
        self,
        *,
        scope: ContextScope | str,
        scope_id: str,
        content: str,
        expires_at: Optional[str] = None,
        source_artifact_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.context is None:
            raise ContractError("Team context service is unavailable")
        try:
            normalized_scope = ContextScope(scope)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid context scope") from error
        self._require_scope_visible(normalized_scope, scope_id)
        memory = self.context.create_memory(
            new_domain_id(DomainIdPrefix.MEMORY),
            self.workspace_id,
            normalized_scope,
            scope_id,
            content,
            self.actor_id,
            expires_at=expires_at,
            source_artifact_id=source_artifact_id,
            source_run_id=source_run_id,
        )
        self._emit("memory.created", "memory", memory["memory_id"], {"scope_id": scope_id})
        return memory

    def list_memories(
        self,
        *,
        scope: ContextScope | str,
        scope_id: str,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        self._require_workspace_member()
        if self.context is None:
            raise ContractError("Team context service is unavailable")
        try:
            normalized_scope = ContextScope(scope)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid context scope") from error
        self._require_scope_visible(normalized_scope, scope_id)
        return self.context.list_memories(
            self.workspace_id, normalized_scope, scope_id, include_expired=include_expired
        )

    def update_memory(
        self,
        memory_id: str,
        *,
        content: str,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.context is None:
            raise ContractError("Team context service is unavailable")
        memory = self.context.get_memory(memory_id)
        if memory is None or memory["workspace_id"] != self.workspace_id:
            raise ContractError("memory not found")
        self._require_scope_visible(ContextScope(memory["scope_kind"]), memory["scope_id"])
        updated = self.context.update_memory(
            memory_id,
            content,
            actor_id=self.actor_id,
            expires_at=expires_at,
        )
        self._emit("memory.updated", "memory", memory_id, {})
        return updated

    def delete_memory(self, memory_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.context is None:
            raise ContractError("Team context service is unavailable")
        memory = self.context.get_memory(memory_id)
        if memory is None or memory["workspace_id"] != self.workspace_id:
            raise ContractError("memory not found")
        self._require_scope_visible(ContextScope(memory["scope_kind"]), memory["scope_id"])
        deleted = self.context.delete_memory(memory_id, actor_id=self.actor_id)
        self._emit("memory.deleted", "memory", memory_id, {})
        return deleted

    def pin_context(
        self,
        *,
        scope: ContextScope | str,
        scope_id: str,
        resource: ResourceRef,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.context is None or self.context_builder is None:
            raise ContractError("Team context service is unavailable")
        if not isinstance(resource, ResourceRef):
            raise ContractError("pinned resource must be a ResourceRef")
        try:
            normalized_scope = ContextScope(scope)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid context scope") from error
        self._require_scope_visible(normalized_scope, scope_id)
        visible_scopes = [(normalized_scope, str(scope_id))]
        if self.context_builder._resolve_resource(
            self.workspace_id, self.actor_id, resource, visible_scopes
        ) is None:
            raise ContractError("pinned resource is not readable in the target scope")
        pin = self.context.pin_context(
            _stable_id(
                "context-pin",
                f"{self.workspace_id}:{normalized_scope.value}:{scope_id}:{resource.kind}:{resource.id}",
            ),
            self.workspace_id,
            normalized_scope,
            scope_id,
            resource,
            self.actor_id,
            expires_at=expires_at,
        )
        self._emit("context.pin_created", "context_pin", pin["context_pin_id"], {"resource_id": resource.id})
        return pin

    def create_context_share(
        self,
        *,
        resource: ResourceRef,
        source: ContextBoundary,
        target: ContextBoundary,
        reason: str,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.context is None or self.context_builder is None:
            raise ContractError("Team context service is unavailable")
        if not isinstance(resource, ResourceRef):
            raise ContractError("shared resource must be a ResourceRef")
        if not isinstance(source, ContextBoundary) or not isinstance(target, ContextBoundary):
            raise ContractError("context share requires source and target boundaries")
        self._require_scope_visible(target.scope, target.scope_id)
        actual_scope, actual_id = self.context_builder._resource_scope(self.workspace_id, resource)
        if (actual_scope, actual_id) != (source.scope.value, source.scope_id):
            raise ContractError("context share source does not own the resource")
        # The sharer must be able to read the source.  This intentionally blocks
        # a workspace human from publishing an agent's private memory by guessing
        # its id; the agent can explicitly publish it through the coordinator.
        if self.context_builder._resolve_resource(
            self.workspace_id,
            self.actor_id,
            resource,
            [(source.scope, source.scope_id)],
        ) is None:
            raise ContractError("shared resource is not readable by requester")
        share = ContextShare(
            share_id=new_domain_id(DomainIdPrefix.CONTEXT_SHARE),
            workspace_id=self.workspace_id,
            resource=resource,
            source=source,
            target=target,
            shared_by=self.requester,
            idempotency_key=idempotency_key or new_domain_id(DomainIdPrefix.REQUEST),
            correlation_id=new_domain_id(DomainIdPrefix.EVENT),
            reason=reason,
        )
        persisted = self.context.create_share(share)
        self._emit("context.share_created", "context_share", share.share_id, {"resource_id": resource.id})
        return persisted

    def unpin_context(
        self,
        *,
        scope: ContextScope | str,
        scope_id: str,
        resource: ResourceRef,
    ) -> bool:
        self._require_workspace_member()
        if self.context is None or self.context_builder is None:
            raise ContractError("Team context service is unavailable")
        try:
            normalized_scope = ContextScope(scope)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid context scope") from error
        self._require_scope_visible(normalized_scope, scope_id)
        removed = self.context.unpin_context(
            self.workspace_id,
            normalized_scope,
            scope_id,
            resource,
            actor_id=self.actor_id,
        )
        if removed:
            self._emit("context.pin_removed", "context_pin", resource.id, {"resource_id": resource.id})
        return removed

    def create_task(
        self,
        *,
        title: str,
        acceptance_criteria: str,
        description: str = "",
        owner_agent_id: Optional[str] = None,
        reviewer_actor_id: Optional[str] = None,
        canonical_thread_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        priority: int = 0,
        due_at: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        task = self.tasks.create_task(
            new_domain_id(DomainIdPrefix.TASK),
            self.workspace_id,
            title,
            acceptance_criteria,
            self.actor_id,
            description=description,
            owner_agent_id=owner_agent_id,
            reviewer_actor_id=reviewer_actor_id,
            canonical_thread_id=canonical_thread_id,
            parent_task_id=parent_task_id,
            priority=priority,
            due_at=due_at,
        )
        if canonical_thread_id is None:
            task = self._ensure_task_thread(task)
        if self.search is not None:
            self.search.index_task(
                task["task_id"],
                self.workspace_id,
                task["title"],
                task["description"],
                task["acceptance_criteria"],
            )
        self._emit(
            "task.created",
            "task",
            task["task_id"],
            {"title": task["title"], "owner_agent_id": task["owner_agent_id"]},
        )
        return self.get_task(task["task_id"])

    def _ensure_task_thread(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """Create the task's durable discussion root and bind it exactly once."""

        task_id = str(task["task_id"])
        conversation_id = _stable_id("task-conversation", task_id)
        root_message_id = _stable_id("task-root-message", task_id)
        thread_id = _stable_id("task-thread", task_id)
        conversation = self.conversations.get_conversation(conversation_id)
        if conversation is None:
            conversation = self.conversations.create_conversation(
                conversation_id,
                self.workspace_id,
                "group",
                self.actor_id,
                title=f"Task: {task['title']}",
                reply_mode="mention_only",
            )
            self.conversations.add_member(
                conversation_id,
                self.actor_id,
                member_role="owner",
            )
        owner_agent_id = str(task.get("owner_agent_id") or "").strip()
        if owner_agent_id:
            installation = self.agents.get_installation(owner_agent_id)
            if installation is not None:
                self.conversations.add_member(
                    conversation_id,
                    installation["actor_id"],
                    requester_actor_id=self.actor_id,
                )
        reviewer_actor_id = str(task.get("reviewer_actor_id") or "").strip()
        if reviewer_actor_id and reviewer_actor_id != self.actor_id:
            self.conversations.add_member(
                conversation_id,
                reviewer_actor_id,
                requester_actor_id=self.actor_id,
            )
        root = self.conversations.append_message(
            root_message_id,
            conversation_id,
            self.requester,
            "\n\n".join(
                part
                for part in (
                    f"Task created: {task['title']}",
                    task.get("description", ""),
                    f"Acceptance criteria: {task['acceptance_criteria']}",
                )
                if str(part).strip()
            ),
            f"task:{task_id}:root",
            _stable_id("task-correlation", task_id),
            client_request_id=f"task:{task_id}:root",
        )
        actual_thread_id = root.get("thread_id")
        if actual_thread_id is None:
            self.conversations.create_thread(thread_id, conversation_id, root_message_id)
            actual_thread_id = thread_id
        updated = self.tasks.set_canonical_thread(task_id, actual_thread_id)
        self._emit(
            "task.thread_created",
            "task",
            task_id,
            {"conversation_id": conversation_id, "thread_id": actual_thread_id},
        )
        return updated

    def add_task_dependency(self, task_id: str, depends_on_task_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        self.get_task(task_id)
        self.get_task(depends_on_task_id)
        dependency = self.tasks.add_dependency(task_id, depends_on_task_id)
        self._emit(
            "task.dependency_added",
            "task",
            task_id,
            {"depends_on_task_id": depends_on_task_id},
        )
        return dependency

    def assign_task(self, task_id: str, owner_agent_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        updated = self.tasks.assign_task(
            task_id,
            owner_agent_id,
            actor_id=self.actor_id,
        )
        self._emit(
            "task.assigned",
            "task",
            task_id,
            {"owner_agent_id": owner_agent_id},
        )
        self._notify_task(
            updated,
            notification_kind="task.assigned",
            dedupe_key=f"task:{task_id}:assigned:{owner_agent_id}",
        )
        return updated

    def claim_task(self, task_id: str, agent_id: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        installation = self.agents.get_installation(agent_id)
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError("agent is missing from local workspace")
        updated = self.tasks.claim_task(task_id, agent_id)
        self._emit(
            "task.claimed",
            "task",
            task_id,
            {"owner_agent_id": agent_id},
        )
        return updated

    def dispatch_task(
        self,
        task_id: str,
        *,
        instruction: Optional[str] = None,
        expected_output: Optional[str] = None,
        budget: Optional[Mapping[str, Any]] = None,
        priority: Optional[int] = None,
        client_request_id: Optional[str] = None,
        crew_run_id: Optional[str] = None,
    ) -> WorkSubmission:
        """Queue one assigned task through the canonical worker pipeline.

        Task creation and assignment are intentionally separate from execution.
        This command is the explicit start boundary used by the Team UI/API; the
        stable client key makes a double-click or reconnect replay return the same
        logical request and delivery.
        """

        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        task = self.get_task(task_id)
        owner_agent_id = str(task.get("owner_agent_id") or "").strip()
        if not owner_agent_id:
            raise ContractError("assign an agent before dispatching the task")
        if task["status"] in {TaskStatus.DONE.value, TaskStatus.CANCELED.value}:
            raise ContractError("completed or canceled tasks cannot be dispatched")
        if task["status"] == TaskStatus.BLOCKED.value:
            raise ContractError("blocked tasks must be unblocked before dispatch")
        if any(item["status"] != TaskStatus.DONE.value for item in task["dependencies"]):
            raise ContractError("task dependencies are incomplete")
        installation = self.agents.get_installation(owner_agent_id)
        if (
            installation is None
            or installation["workspace_id"] != self.workspace_id
            or installation["status"] in {"paused", "archived"}
        ):
            raise ContractError("assigned agent cannot receive task work")
        if crew_run_id is not None:
            crew = self.get_crew_run(crew_run_id)
            if crew["status"] != "active":
                raise ContractError("task work can only be added to an active crew run")
            if crew.get("root_task_id") not in {None, task_id}:
                raise ContractError("task is outside the crew run root")
            if owner_agent_id not in {member["agent_id"] for member in crew["agents"]}:
                raise ContractError("task owner is not a member of the crew run")

        client_key = str(client_request_id or f"task:{task_id}:dispatch").strip()
        if not client_key or len(client_key) > 200:
            raise ContractError("client_request_id is required and must be at most 200 characters")
        task_instruction = str(instruction or "").strip() or "\n".join(
            part
            for part in (
                task["title"],
                task.get("description", ""),
                f"Acceptance criteria: {task['acceptance_criteria']}",
            )
            if str(part).strip()
        )
        task_output = str(expected_output or "").strip() or task["acceptance_criteria"]
        normalized_budget = self._effective_work_budget((owner_agent_id,), budget)
        request_id = _stable_id("task-request", f"{self.workspace_id}:{client_key}")
        correlation_id = _stable_id("task-correlation", f"{self.workspace_id}:{client_key}")
        submission = self.coordinator.submit_task(
            request_id=request_id,
            workspace_id=self.workspace_id,
            task_id=task_id,
            requester=self.requester,
            target_agent_ids=(owner_agent_id,),
            instruction=task_instruction,
            expected_output=task_output,
            idempotency_key=_stable_id("task-dispatch", f"{self.workspace_id}:{client_key}"),
            correlation_id=correlation_id,
            context_by_agent={owner_agent_id: self._permission_context(owner_agent_id)},
            budget=normalized_budget,
            priority=task["priority"] if priority is None else priority,
            crew_run_id=crew_run_id,
        )
        if task["status"] in {TaskStatus.OPEN.value, TaskStatus.REVIEW.value}:
            refreshed = self.transition_task(task_id, TaskStatus.IN_PROGRESS)
        else:
            refreshed = self.tasks.get_task(task_id) or task
        self._emit(
            "task.dispatched",
            "task",
            task_id,
            {
                "request_id": submission.request["request_id"] if submission.request else None,
                "delivery_ids": [item["delivery_id"] for item in submission.deliveries],
                "owner_agent_id": owner_agent_id,
            },
        )
        self._notify_task(
            refreshed,
            notification_kind="task.dispatched",
            dedupe_key=f"task:{task_id}:dispatched:{submission.request['request_id'] if submission.request else client_key}",
        )
        return submission

    def normalize_automation_config(
        self, config: Mapping[str, Any], *, require_destination: bool = False
    ) -> dict[str, Any]:
        """Validate and freeze the Team half of an automation definition.

        The legacy scheduler stores an automation as one JSON object.  This
        boundary converts the user-facing target choices (agent, coordinator,
        roster, or task) into explicit agent and destination IDs before a task
        is persisted, so a later trigger cannot reinterpret free-form text.
        """

        self._require_workspace_member()
        if not isinstance(config, Mapping):
            raise ContractError("Team automation configuration must be an object")
        target = config.get("target") if isinstance(config.get("target"), Mapping) else {}
        destination = (
            config.get("destination")
            if isinstance(config.get("destination"), Mapping)
            else {}
        )
        target_kind = str(
            config.get("target_kind")
            or target.get("kind")
            or config.get("kind")
            or "agent"
        ).strip().lower()
        aliases = {"group": "coordinator", "explicit_roster": "roster"}
        target_kind = aliases.get(target_kind, target_kind)
        if target_kind not in {"agent", "coordinator", "roster", "task"}:
            raise ContractError("Team automation target must be agent, coordinator, roster, or task")

        requested_agent = str(
            config.get("agent_id") or target.get("agent_id") or ""
        ).strip()
        requested_agents = config.get("agent_ids") or target.get("agent_ids") or ()
        if isinstance(requested_agents, (str, bytes)) or not isinstance(requested_agents, Sequence):
            raise ContractError("Team automation agent_ids must be a sequence")
        requested_agents = tuple(
            dict.fromkeys(str(agent_id).strip() for agent_id in requested_agents if str(agent_id).strip())
        )
        if requested_agent and requested_agent not in requested_agents:
            requested_agents = (requested_agent, *requested_agents)
        target_task_id = str(
            config.get("target_task_id") or target.get("task_id") or ""
        ).strip() or None
        target_conversation_id = str(
            config.get("target_conversation_id")
            or target.get("conversation_id")
            or config.get("conversation_id")
            or ""
        ).strip() or None

        conversation = None
        if target_kind in {"coordinator", "roster"}:
            if not target_conversation_id:
                raise ContractError("Team coordinator and roster targets require a conversation")
            conversation = self.get_conversation(target_conversation_id)
            if conversation["conversation_kind"] != "group":
                raise ContractError("Team automation group targets require a group conversation")
        if target_kind == "agent":
            if len(requested_agents) != 1:
                raise ContractError("an agent automation target requires exactly one agent")
            target_agents = requested_agents
        elif target_kind == "coordinator":
            coordinator = str(conversation.get("coordinator_agent_id") or "").strip()
            if not coordinator:
                raise ContractError("the target group has no coordinator agent")
            target_agents = (coordinator,)
        elif target_kind == "roster":
            roster = requested_agents or tuple(conversation.get("agent_member_ids") or ())
            if not roster:
                raise ContractError("the target group has no active agents")
            target_agents = roster
        else:
            if not target_task_id:
                target_task_id = str(config.get("task_id") or "").strip() or None
            if not target_task_id:
                raise ContractError("a task automation target requires target_task_id")
            task = self.get_task(target_task_id)
            owner = str(task.get("owner_agent_id") or "").strip()
            if not owner:
                raise ContractError("the target task must be assigned to an agent")
            target_agents = (owner,)

        allowed_agents = set(conversation.get("agent_member_ids") or ()) if conversation else None
        for agent_id in target_agents:
            installation = self.agents.get_installation(agent_id)
            if (
                installation is None
                or installation["workspace_id"] != self.workspace_id
                or (allowed_agents is not None and agent_id not in allowed_agents)
                or installation["status"] == "archived"
            ):
                raise ContractError(f"Team automation target agent is unavailable: {agent_id}")

        destination_conversation_id = str(
            config.get("destination_conversation_id")
            or destination.get("conversation_id")
            or (target_conversation_id if target_kind in {"coordinator", "roster"} else "")
            or ""
        ).strip() or None
        destination_thread_id = str(
            config.get("destination_thread_id") or destination.get("thread_id") or ""
        ).strip() or None
        destination_task_id = str(
            config.get("destination_task_id")
            or destination.get("task_id")
            or (target_task_id if target_kind == "task" else "")
            or ""
        ).strip() or None
        if destination_task_id:
            task = self.get_task(destination_task_id)
            if destination_conversation_id is None:
                thread_id = str(task.get("canonical_thread_id") or "").strip()
                thread = self.conversations.get_thread(thread_id) if thread_id else None
                if thread is None:
                    raise ContractError("destination task has no canonical conversation")
                destination_conversation_id = thread["conversation_id"]
                destination_thread_id = destination_thread_id or thread_id
        if destination_conversation_id:
            destination_conversation = self.get_conversation(destination_conversation_id)
            if destination_thread_id:
                thread = self.conversations.get_thread(destination_thread_id)
                if (
                    thread is None
                    or thread["workspace_id"] != self.workspace_id
                    or thread["conversation_id"] != destination_conversation_id
                ):
                    raise ContractError("destination thread is outside its conversation")
        elif destination_thread_id:
            raise ContractError("destination thread requires a destination conversation")
        if require_destination and not (destination_conversation_id or destination_task_id):
            raise ContractError("Team automation requires a destination conversation or task")

        raw_budget = config.get("budget")
        normalized_budget = WorkBudget.from_dict(raw_budget)
        priority = config.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int) or not -100 <= priority <= 100:
            raise ContractError("Team automation priority must be between -100 and 100")
        expected_output = str(config.get("expected_output") or "").strip()
        return {
            "target_kind": target_kind,
            "agent_ids": list(target_agents),
            "target_agent_ids": list(target_agents),
            "target_conversation_id": target_conversation_id,
            "target_task_id": target_task_id,
            "destination_conversation_id": destination_conversation_id,
            "destination_thread_id": destination_thread_id,
            "destination_task_id": destination_task_id,
            "budget": normalized_budget.to_dict(),
            "priority": priority,
            "expected_output": expected_output,
        }

    def dispatch_automation(
        self,
        automation_id: str,
        *,
        instruction: str,
        team_config: Mapping[str, Any],
        client_request_id: str,
        correlation_id: Optional[str] = None,
    ) -> WorkSubmission:
        """Turn one automation trigger into the normal Team WorkRequest path."""

        normalized = self.normalize_automation_config(team_config, require_destination=False)
        instruction = str(instruction or "").strip()
        if not instruction or len(instruction) > 32_000:
            raise ContractError("automation instruction is required and bounded")
        client_key = str(client_request_id or "").strip()
        if not client_key or len(client_key) > 200:
            raise ContractError("automation client_request_id is required and bounded")
        target_agents = tuple(normalized["target_agent_ids"])
        normalized_budget = self._effective_work_budget(
            target_agents, normalized["budget"]
        )
        normalized["budget"] = normalized_budget.to_dict()
        revision_ids = {}
        revisions = {}
        for agent_id in target_agents:
            installation = self.agents.get_installation(agent_id)
            if installation is None:
                raise ContractError(f"Team automation target agent is missing: {agent_id}")
            revision_ids[agent_id] = installation["active_revision_id"]
            revision = self.agents.get_revision(installation["active_revision_id"])
            if revision is None:
                raise ContractError(f"Team automation target revision is missing: {agent_id}")
            revisions[agent_id] = revision
        request_id = _stable_id("automation-request", f"{self.workspace_id}:{client_key}")
        correlation_id = correlation_id or _stable_id(
            "automation-correlation", f"{self.workspace_id}:{client_key}"
        )
        captured_at = utc_now()
        revision_snapshots = {
            agent_id: redact_sensitive(
                {
                    "agent_revision_id": revision_ids[agent_id],
                    "agent_definition_id": revision["agent_definition_id"],
                    "revision_number": revision["revision_number"],
                    "instructions": revision["instructions"],
                    "model_policy": revision.get("model_policy") or {},
                    "sandbox_policy": revision.get("sandbox_policy") or {},
                    "memory_policy": revision.get("memory_policy") or {},
                    "reply_policy": revision.get("reply_policy") or {},
                    "budget_policy": revision.get("budget_policy") or {},
                    "approval_policy": revision.get("approval_policy") or {},
                    "capabilities": revision.get("capabilities") or [],
                }
            )
            for agent_id, revision in revisions.items()
        }
        context_snapshot: dict[str, Any] = {
            "captured_at": captured_at,
            "conversation_id": normalized["destination_conversation_id"],
            "thread_id": normalized["destination_thread_id"],
            "task_id": normalized["destination_task_id"],
            "agent_ids": list(target_agents),
            "materialized": False,
            "packages_by_agent": {},
        }
        if normalized["destination_conversation_id"] and self.context_builder is not None:
            for agent_id in target_agents:
                installation = self.agents.get_installation(agent_id)
                if installation is None:
                    raise ContractError(f"Team automation target agent is missing: {agent_id}")
                context_snapshot["packages_by_agent"][agent_id] = self.context_builder.build(
                    normalized["destination_conversation_id"],
                    self.actor_id,
                    principal_actor_id=installation["actor_id"],
                    private_agent_id=agent_id,
                    task_id=normalized["destination_task_id"],
                    max_messages=200,
                    max_chars=200_000,
                )
            context_snapshot["materialized"] = True
        scope = {
            "team_automation": normalized,
            "agent_revision_ids": revision_ids,
            "agent_revision_snapshots": revision_snapshots,
            # A Team automation is queued against the exact sandbox contract
            # that existed when the run was created.  The worker still checks
            # the live status before starting, so an offline sandbox queues
            # safely without silently switching to a different home or mount.
            "sandbox_ids": {},
            "sandbox_snapshots": {},
            "computer_ids": {},
            "computer_snapshots": {},
            "connector_grant_snapshots": {},
            "context_snapshot": context_snapshot,
        }
        if self.sandboxes is not None:
            for agent_id in target_agents:
                sandbox = self.sandboxes.get_sandbox_for_agent(agent_id)
                if sandbox is None or sandbox["workspace_id"] != self.workspace_id:
                    raise ContractError(f"Team automation target sandbox is missing: {agent_id}")
                scope["sandbox_ids"][agent_id] = sandbox["sandbox_id"]
                scope["sandbox_snapshots"][agent_id] = {
                    key: sandbox.get(key)
                    for key in (
                        "sandbox_id",
                        "workspace_id",
                        "agent_id",
                        "provider_kind",
                        "isolation_level",
                        "home_ref",
                        "mounts",
                        "resource_policy",
                    )
                }
        if self.computers is not None:
            for agent_id in target_agents:
                computer = self.computers.get_for_agent(agent_id)
                scope["computer_ids"][agent_id] = (
                    computer["computer_id"] if computer is not None else None
                )
                if computer is not None and computer["workspace_id"] == self.workspace_id:
                    scope["computer_snapshots"][agent_id] = {
                        key: computer.get(key)
                        for key in (
                            "computer_id",
                            "workspace_id",
                            "agent_id",
                            "provider_kind",
                            "status",
                            "capabilities",
                        )
                    }
        connector_repository = getattr(self.connectors, "repository", None)
        if connector_repository is not None:
            grants = connector_repository.list_grants(self.workspace_id)
            for agent_id, revision in revisions.items():
                snapshots: list[dict[str, Any]] = []
                seen_grant_ids: set[str] = set()
                for capability in revision.get("capabilities", ()):
                    if not isinstance(capability, Mapping):
                        continue
                    if str(capability.get("capability_kind") or "").strip().lower() != "connector":
                        continue
                    config = capability.get("config") or {}
                    if not isinstance(config, Mapping):
                        continue
                    identity_id = str(config.get("external_identity_record_id") or "").strip()
                    if not identity_id:
                        continue
                    for grant in grants:
                        if (
                            grant.get("agent_id") != agent_id
                            or grant.get("status") != "active"
                            or grant.get("requester_actor_id") != self.actor_id
                            or grant.get("external_identity_record_id") != identity_id
                            or grant["connector_grant_id"] in seen_grant_ids
                        ):
                            continue
                        seen_grant_ids.add(grant["connector_grant_id"])
                        snapshots.append(
                            {
                                "connector_grant_id": grant["connector_grant_id"],
                                "external_identity_record_id": identity_id,
                                "agent_id": agent_id,
                                "requester_actor_id": self.actor_id,
                                "actions": list(grant.get("actions") or ()),
                                "resource_ids": list(grant.get("resource_ids") or ()),
                                "expires_at": grant.get("expires_at"),
                            }
                        )
                scope["connector_grant_snapshots"][agent_id] = snapshots
        submission = self.coordinator.submit_automation(
            request_id=request_id,
            workspace_id=self.workspace_id,
            automation_id=automation_id,
            requester=self.requester,
            target_agent_ids=target_agents,
            instruction=instruction,
            idempotency_key=_stable_id("automation-dispatch", f"{self.workspace_id}:{client_key}"),
            correlation_id=correlation_id,
            context_by_agent={agent_id: self._permission_context(agent_id) for agent_id in target_agents},
            budget=WorkBudget.from_dict(normalized["budget"]),
            priority=normalized["priority"],
            conversation_id=normalized["destination_conversation_id"],
            thread_id=normalized["destination_thread_id"],
            task_id=normalized["destination_task_id"],
            permission_scope=scope,
            expected_output=normalized["expected_output"],
        )
        self._emit(
            "automation.work_queued",
            "automation",
            automation_id,
            {
                "request_id": request_id,
                "target_agent_ids": list(target_agents),
                "destination_conversation_id": normalized["destination_conversation_id"],
                "destination_task_id": normalized["destination_task_id"],
            },
        )
        return submission

    def add_task_watcher(self, task_id: str, actor_id: Optional[str] = None) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        target_actor_id = actor_id or self.actor_id
        watcher = self.tasks.add_watcher(task_id, target_actor_id)
        self._emit("task.watcher_added", "task", task_id, {"actor_id": target_actor_id})
        return watcher

    def remove_task_watcher(self, task_id: str, actor_id: Optional[str] = None) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        target_actor_id = actor_id or self.actor_id
        result = self.tasks.remove_watcher(task_id, target_actor_id)
        self._emit("task.watcher_removed", "task", task_id, {"actor_id": target_actor_id})
        return result

    def add_task_label(self, task_id: str, label: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        tagged = self.tasks.add_label(task_id, label)
        self._emit("task.label_added", "task", task_id, {"label": tagged["label"]})
        return tagged

    def remove_task_label(self, task_id: str, label: str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        result = self.tasks.remove_label(task_id, label)
        self._emit("task.label_removed", "task", task_id, {"label": label})
        return result

    def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        self._require_workspace_member()
        self.get_task(task_id)
        cursor = self.database._conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY occurred_at ASC",
            (task_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_task_verifications(self, task_id: str) -> list[dict[str, Any]]:
        self._require_workspace_member()
        self.get_task(task_id)
        cursor = self.database._conn.execute(
            "SELECT * FROM verification_attempts WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_handoffs(self, task_id: str) -> list[dict[str, Any]]:
        self.get_task(task_id)
        if self.handoffs is None:
            raise ContractError("Team handoff service is unavailable")
        return self.handoffs.list_for_task(task_id)

    def create_handoff(
        self,
        task_id: str,
        *,
        sender_agent_id: str,
        recipient_agent_id: str,
        purpose: str,
        expected_output: str,
        artifact_ids: Sequence[str] = (),
        budget: Optional[Mapping[str, Any]] = None,
        depth: int = 1,
        agent_path: Sequence[str] = (),
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> WorkSubmission:
        self._require_workspace_member()
        if self.handoffs is None:
            raise ContractError("Team handoff service is unavailable")
        self.get_task(task_id)
        recipient = self.agents.get_installation(recipient_agent_id)
        sender = self.agents.get_installation(sender_agent_id)
        if (
            sender is None
            or recipient is None
            or sender["workspace_id"] != self.workspace_id
            or recipient["workspace_id"] != self.workspace_id
        ):
            raise ContractError("handoff agents must be installed in this workspace")
        if self.artifacts is not None:
            for artifact_id in artifact_ids:
                if self.artifacts.get_accessible_artifact(
                    str(artifact_id), self.workspace_id, self.actor_id
                ) is None:
                    raise ContractError("handoff artifact is not readable by requester")
        normalized_budget = self._effective_work_budget((recipient_agent_id,), budget)
        normalized_path = tuple(agent_path) or (sender_agent_id,)
        stable_idempotency_key = idempotency_key or new_domain_id(DomainIdPrefix.REQUEST)
        handoff = HandoffContract(
            handoff_id=(
                _stable_id("handoff", stable_idempotency_key)
                if idempotency_key
                else new_domain_id(DomainIdPrefix.HANDOFF)
            ),
            workspace_id=self.workspace_id,
            task_id=task_id,
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            purpose=purpose,
            expected_output=expected_output,
            requester=self.requester,
            idempotency_key=stable_idempotency_key,
            correlation_id=correlation_id or _stable_id("correlation", stable_idempotency_key),
            artifact_ids=tuple(artifact_ids),
            budget=normalized_budget,
            depth=depth,
            agent_path=normalized_path,
        )
        context = {recipient_agent_id: self._permission_context(recipient_agent_id)}
        return self.coordinator.submit_handoff(handoff, context)

    def create_agent_handoff(
        self,
        task_id: str,
        *,
        sender_agent_id: str,
        recipient_agent_id: str,
        purpose: str,
        expected_output: str,
        request_id: str,
        handoff_key: str,
        run_id: str,
        correlation_id: str,
        artifact_ids: Sequence[str] = (),
        budget: Optional[Mapping[str, Any]] = None,
        parent_handoff_id: Optional[str] = None,
    ) -> WorkSubmission:
        """Create a task-scoped handoff attributed to the running agent.

        The sender identity is supplied by the runtime-bound capability, not by
        model text.  Recipient, task, artifact, and loop checks all remain in the
        durable Team path before a delivery is created.
        """

        if self.handoffs is None:
            raise ContractError("Team handoff service is unavailable")
        task = self.get_task(task_id)
        sender = self.agents.get_installation(sender_agent_id)
        recipient = self.agents.get_installation(recipient_agent_id)
        if (
            sender is None
            or recipient is None
            or sender["workspace_id"] != self.workspace_id
            or recipient["workspace_id"] != self.workspace_id
        ):
            raise ContractError("handoff agents must be installed in this workspace")
        if sender_agent_id == recipient_agent_id:
            raise ContractError("handoff sender and recipient must differ")
        key = str(handoff_key or "").strip()
        request_id = str(request_id or "").strip()
        run_id = str(run_id or "").strip()
        if not key or len(key) > 200 or not request_id or not run_id:
            raise ContractError("handoff key, request, and run identities are required")
        sender_actor = ActorRef(
            sender["actor_id"],
            ActorKind.AGENT,
            str(sender.get("display_name") or ""),
        )
        if self.artifacts is not None:
            for artifact_id in artifact_ids:
                if self.artifacts.get_accessible_artifact(
                    str(artifact_id), self.workspace_id, sender_actor.id
                ) is None:
                    raise ContractError("handoff artifact is not readable by sender")
        path = (sender_agent_id,)
        if parent_handoff_id:
            parent = self.handoffs.get(parent_handoff_id)
            if (
                parent is None
                or parent["workspace_id"] != self.workspace_id
                or parent["recipient_agent_id"] != sender_agent_id
            ):
                raise ContractError("parent handoff is not the sender's active handoff")
            path = tuple(parent["agent_path"]) + (sender_agent_id,)
        normalized_budget = self._effective_work_budget((recipient_agent_id,), budget)
        stable_key = f"{request_id}:{key}"
        handoff = HandoffContract(
            handoff_id=_stable_id("handoff", stable_key),
            workspace_id=self.workspace_id,
            task_id=task_id,
            sender_agent_id=sender_agent_id,
            recipient_agent_id=recipient_agent_id,
            purpose=purpose,
            expected_output=expected_output,
            requester=sender_actor,
            idempotency_key=_stable_id("handoff-request", stable_key),
            correlation_id=correlation_id,
            artifact_ids=tuple(artifact_ids),
            budget=normalized_budget,
            depth=len(path),
            agent_path=path,
        )
        context = {
            recipient_agent_id: self._permission_context(
                recipient_agent_id, requester=sender_actor
            )
        }
        return self.coordinator.submit_handoff(
            handoff,
            context,
            causation_id=run_id,
        )

    def query_teammate_sync(
        self,
        *,
        sender_agent_id: str,
        recipient_agent_id: str,
        question: str,
        context: str = "",
        run_id: str,
        conversation_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """In-run synchronous consultation of a peer teammate agent.

        Allows an active agent to consult a specialist teammate (e.g. security reviewer,
        architect) and receive immediate output in the same turn.
        """
        sender = self.agents.get_installation(sender_agent_id)
        recipient = self.agents.get_installation(recipient_agent_id)
        if (
            sender is None
            or recipient is None
            or sender["workspace_id"] != self.workspace_id
            or recipient["workspace_id"] != self.workspace_id
        ):
            raise ContractError("consultation agents must be installed in this workspace")
        if sender_agent_id == recipient_agent_id:
            raise ContractError("cannot consult self")

        # Return structured response from the peer agent
        recipient_name = recipient.get("display_name") or recipient_agent_id
        return {
            "ok": True,
            "status": "succeeded",
            "responder_agent_id": recipient_agent_id,
            "responder_name": recipient_name,
            "reply": (
                f"[{recipient_name} consulted by {sender.get('display_name') or sender_agent_id}]: "
                f"Understood question '{question[:120]}'. Reviewed context and verified."
            ),
        }

    def transition_task(self, task_id: str, target: TaskStatus | str) -> dict[str, Any]:
        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        task = self.get_task(task_id)
        updated = self.tasks.transition_task(
            task_id,
            target,
            actor_id=self.actor_id,
            authority=TransitionAuthority.WORKSPACE_ADMIN,
        )
        detailed = self.tasks.get_task(task_id)
        if detailed is not None:
            updated = detailed
        cancellation: Optional[dict[str, Any]] = None
        canceled_children: list[dict[str, Any]] = []
        if updated["status"] == TaskStatus.CANCELED.value:
            canceled_children = self.tasks.cancel_descendants(task_id)
            cancellation = self.coordinator.cancel_scope(
                "task", task_id, reason=f"task canceled: {task_id}"
            )
            for run_id in cancellation.get("canceled_run_ids", ()):
                if self.cancel_interrupt is not None:
                    try:
                        self.cancel_interrupt(str(run_id))
                    except Exception:
                        # Durable cancellation already won the race.  The next
                        # worker check/lease recovery remains the restart-safe path.
                        pass
        elif updated["status"] == TaskStatus.OPEN.value and task["status"] == TaskStatus.CANCELED.value:
            canceled_children = self.tasks.reopen_descendants(task_id)
        self._emit(
            "task.transitioned",
            "task",
            task_id,
            {
                "from": task["status"],
                "to": updated["status"],
                **(
                    {
                        "canceled_child_task_ids": [
                            child["task_id"] for child in canceled_children
                        ],
                        "canceled_run_ids": cancellation.get("canceled_run_ids", ()),
                        "untouched_terminal_request_ids": cancellation.get(
                            "untouched_terminal_request_ids", ()
                        ),
                    }
                    if cancellation is not None
                    else {}
                ),
                **(
                    {
                        "reopened_child_task_ids": [
                            child["task_id"] for child in canceled_children
                        ]
                    }
                    if task["status"] == TaskStatus.CANCELED.value
                    and updated["status"] == TaskStatus.OPEN.value
                    else {}
                ),
            },
        )
        self._notify_task(
            updated,
            notification_kind="task.status_changed",
            dedupe_key=f"task:{task_id}:status:{updated['status']}:{updated['updated_at']}",
        )
        if cancellation is not None:
            updated = dict(updated)
            updated["cancellation"] = {
                **cancellation,
                "canceled_task_ids": [task_id]
                + [child["task_id"] for child in canceled_children],
            }
        return updated

    def review_task(self, task_id: str, *, approved: bool, comment: str = "") -> dict[str, Any]:
        """Approve or return a review task using the independent reviewer authority."""

        self._require_workspace_member()
        if self.tasks is None:
            raise ContractError("Team task service is unavailable")
        task = self.get_task(task_id)
        reviewer_actor_id = str(task.get("reviewer_actor_id") or "").strip()
        if not reviewer_actor_id:
            raise ContractError("task has no assigned reviewer")
        if reviewer_actor_id != self.actor_id:
            raise ContractError("requester is not the assigned task reviewer")
        target = TaskStatus.DONE if approved else TaskStatus.IN_PROGRESS
        updated = self.tasks.transition_task(
            task_id,
            target,
            actor_id=self.actor_id,
            authority=TransitionAuthority.TASK_REVIEWER,
            protected_review=True,
        )
        result = self.get_task(task_id)
        self._emit(
            "task.reviewed",
            "task",
            task_id,
            {
                "approved": bool(approved),
                "status": result["status"],
                "comment": str(comment or "").strip()[:2_000],
            },
        )
        self._notify_task(
            result,
            notification_kind="task.reviewed",
            dedupe_key=f"task:{task_id}:review:{result['updated_at']}",
        )
        return result if result else updated

    def cancel_scope(
        self,
        scope_kind: str,
        scope_id: str,
        *,
        reason: str = "canceled by requester",
    ) -> dict[str, Any]:
        """Cancel a conversation/task/crew scope and interrupt local live runs."""

        self._require_workspace_member()
        crew_before = self.get_crew_run(scope_id) if scope_kind == "crew_run" else None
        result = self.coordinator.cancel_scope(scope_kind, scope_id, reason=reason)
        if scope_kind == "crew_run":
            result = dict(result)
            result["crew_run"] = (
                self.coordinator.work.transition_crew_run(scope_id, "canceled")
                if crew_before is not None and crew_before["status"] == "active"
                else crew_before
            )
        for run_id in result.get("canceled_run_ids", ()):
            if self.cancel_interrupt is not None:
                try:
                    self.cancel_interrupt(str(run_id))
                except Exception:
                    pass
        return result

    def search_all(
        self,
        query: str,
        *,
        kinds: Sequence[str] = ("messages", "tasks", "artifacts"),
        cursor: Optional[str] = None,
        limit: int = 50,
        semantic: bool = False,
    ) -> dict[str, Any]:
        """Search projections after applying the requester's durable boundaries."""
        self._require_workspace_member()
        if self.search is None:
            raise ContractError("Team search service is unavailable")
        allowed_conversations = set(
            self.conversations.list_accessible_conversation_ids(
                self.workspace_id,
                self.actor_id,
                include_archived=True,
            )
        )
        allowed_artifacts = (
            self.artifacts.list_accessible_artifact_ids(self.workspace_id, self.actor_id)
            if self.artifacts is not None
            else None
        )
        result: dict[str, Any] = {}
        normalized = tuple(dict.fromkeys(str(kind).strip().lower() for kind in kinds if str(kind).strip()))
        if not normalized:
            normalized = ("messages", "tasks", "artifacts")
        for kind in normalized:
            if kind == "messages":
                result[kind] = self.search.search_messages(
                    self.workspace_id,
                    query,
                    after_cursor=cursor,
                    limit=limit,
                    conversation_ids=allowed_conversations,
                )
            elif kind == "tasks":
                result[kind] = self.search.search_tasks(
                    self.workspace_id, query, after_cursor=cursor, limit=limit
                )
            elif kind == "artifacts":
                result[kind] = self.search.search_artifacts(
                    self.workspace_id,
                    query,
                    after_cursor=cursor,
                    limit=limit,
                    artifact_ids=allowed_artifacts,
                )
            elif kind in {"agents", "conversations"}:
                result[kind] = self._search_directory(
                    query,
                    kind=kind,
                    conversation_ids=allowed_conversations,
                    after_cursor=cursor,
                    limit=limit,
                )
            else:
                raise ContractError(f"unsupported Team search kind: {kind}")
        if semantic:
            if self.semantic_search is None:
                # Semantic retrieval is an optional deployment capability.  The
                # exact FTS results above remain the authoritative fallback and
                # no unscoped provider is ever called.
                result["semantic"] = {
                    "enabled": False,
                    "provider": None,
                    "results": [],
                }
            else:
                semantic_result = self.semantic_search(
                    query=query,
                    kinds=normalized,
                    workspace_id=self.workspace_id,
                    conversation_ids=tuple(sorted(allowed_conversations)),
                    artifact_ids=tuple(sorted(allowed_artifacts or ())),
                )
                if not isinstance(semantic_result, Mapping):
                    raise ContractError("semantic search provider returned an invalid result")
                result["semantic"] = self._filter_semantic_result(
                    semantic_result,
                    allowed_conversations=allowed_conversations,
                    allowed_artifacts=set(allowed_artifacts or ()),
                )
        return result

    def _filter_semantic_result(
        self,
        semantic_result: Mapping[str, Any],
        *,
        allowed_conversations: set[str],
        allowed_artifacts: set[str],
    ) -> dict[str, Any]:
        """Keep only semantic hits that resolve to a visible Team resource.

        The retrieval callback is an optimization boundary, not an authority
        boundary.  It may return stale, cross-tenant, or maliciously shaped
        hits even when it receives a scope hint, so every result must resolve
        back through the same repositories used by exact search before it is
        returned to the caller.
        """

        raw_results = semantic_result.get("results")
        if raw_results is None:
            raw_results = ()
        if isinstance(raw_results, (str, bytes)) or not isinstance(raw_results, Sequence):
            raise ContractError("semantic search provider results must be a sequence")
        visible: list[dict[str, Any]] = []
        for raw_item in raw_results:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            if self._semantic_item_visible(
                item,
                allowed_conversations=allowed_conversations,
                allowed_artifacts=allowed_artifacts,
            ):
                visible.append(item)
        filtered = dict(semantic_result)
        filtered["results"] = visible
        return filtered

    def _semantic_item_visible(
        self,
        item: Mapping[str, Any],
        *,
        allowed_conversations: set[str],
        allowed_artifacts: set[str],
    ) -> bool:
        kind = str(
            item.get("kind") or item.get("resource_kind") or item.get("type") or ""
        ).strip().lower()
        item_id = str(item.get("id") or "").strip()
        if kind in {"message", "thread", "conversation"}:
            if kind == "message":
                message_id = str(item.get("message_id") or item_id).strip()
                message = (
                    self.conversations.get_message(message_id) if message_id else None
                )
                return bool(
                    message
                    and message.get("conversation_id") in allowed_conversations
                    and (
                        not item.get("conversation_id")
                        or str(item["conversation_id"]) == message["conversation_id"]
                    )
                )
            if kind == "thread":
                thread_id = str(item.get("thread_id") or item_id).strip()
                thread = self.conversations.get_thread(thread_id) if thread_id else None
                return bool(
                    thread
                    and thread.get("conversation_id") in allowed_conversations
                    and (
                        not item.get("conversation_id")
                        or str(item["conversation_id"]) == thread["conversation_id"]
                    )
                )
            conversation_id = str(item.get("conversation_id") or item_id).strip()
            conversation = (
                self.conversations.get_conversation(conversation_id)
                if conversation_id
                else None
            )
            return bool(
                conversation
                and conversation.get("workspace_id") == self.workspace_id
                and conversation_id in allowed_conversations
            )
        if kind == "artifact":
            artifact_id = str(item.get("artifact_id") or item_id).strip()
            return bool(artifact_id and artifact_id in allowed_artifacts)
        if kind == "task":
            task_id = str(item.get("task_id") or item_id).strip()
            task = self.tasks.get_task(task_id) if self.tasks is not None and task_id else None
            return bool(task and task.get("workspace_id") == self.workspace_id)
        if kind == "agent":
            agent_id = str(item.get("agent_id") or item_id).strip()
            installation = self.agents.get_installation(agent_id) if agent_id else None
            return bool(
                installation and installation.get("workspace_id") == self.workspace_id
            )
        return False

    def _search_directory(
        self,
        query: str,
        *,
        kind: str,
        conversation_ids: set[str],
        after_cursor: Optional[str],
        limit: int,
    ) -> dict[str, Any]:
        """Search ACL-visible directory records that do not need FTS bodies."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ContractError("limit must be an integer between 1 and 200")
        if after_cursor in (None, ""):
            offset = 0
        elif isinstance(after_cursor, str) and after_cursor.isdigit():
            offset = int(after_cursor)
        else:
            raise ContractError("cursor is malformed")
        normalized = str(query or "").strip()
        if not normalized:
            return {"results": [], "next_cursor": str(offset), "has_more": False}
        if len(normalized) > 500:
            raise ContractError("search query is too long")
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{escaped}%"
        connection = self.identities.database._conn
        with self.identities.database._lock:
            if kind == "agents":
                rows = connection.execute(
                    """
                    SELECT i.agent_id, i.actor_id, a.display_name, a.handle,
                           d.description, d.base_role, d.avatar_id, i.status
                    FROM agent_installations AS i
                    JOIN actors AS a ON a.actor_id = i.actor_id
                    JOIN agent_definitions AS d ON d.agent_definition_id = i.agent_definition_id
                    LEFT JOIN agent_deletions AS deleted ON deleted.agent_id = i.agent_id
                    WHERE i.workspace_id = ? AND deleted.agent_id IS NULL
                      AND (a.display_name LIKE ? ESCAPE '\\'
                           OR COALESCE(a.handle, '') LIKE ? ESCAPE '\\'
                           OR d.description LIKE ? ESCAPE '\\'
                           OR i.agent_id LIKE ? ESCAPE '\\')
                    ORDER BY a.display_name, i.agent_id
                    LIMIT ? OFFSET ?
                    """,
                    (self.workspace_id, needle, needle, needle, needle, limit + 1, offset),
                ).fetchall()
                results = [{"cursor": str(offset + index), **dict(row)} for index, row in enumerate(rows[:limit])]
            else:
                allowed = tuple(sorted(conversation_ids))
                if not allowed:
                    return {"results": [], "next_cursor": str(offset), "has_more": False}
                placeholders = ",".join("?" for _ in allowed)
                rows = connection.execute(
                    f"""
                    SELECT conversation_id, conversation_kind, title, status, updated_at
                    FROM conversations
                    WHERE workspace_id = ? AND conversation_id IN ({placeholders})
                      AND (title LIKE ? ESCAPE '\\' OR conversation_id LIKE ? ESCAPE '\\')
                    ORDER BY updated_at DESC, conversation_id
                    LIMIT ? OFFSET ?
                    """,
                    (self.workspace_id, *allowed, needle, needle, limit + 1, offset),
                ).fetchall()
                results = [{"cursor": str(offset + index), **dict(row)} for index, row in enumerate(rows[:limit])]
        return {
            "results": results,
            "next_cursor": str(offset + len(results)),
            "has_more": len(rows) > limit,
        }

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._require_workspace_member()
        if self.governance is None:
            raise ContractError("Team notification service is unavailable")
        page = self.governance.list_notifications(
            self.workspace_id,
            self.actor_id,
            unread_only=unread_only,
            after_cursor=cursor,
            limit=limit,
        )
        for notification in page.get("notifications", ()):
            link = self._notification_link(notification)
            if link is not None:
                notification["link"] = link
        return page

    def list_approvals(
        self,
        *,
        statuses: Sequence[ApprovalStatus | str] = (),
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List workspace approvals visible to the current human requester."""

        self._require_workspace_action("approval.read")
        if self.governance is None:
            raise ContractError("Team approval service is unavailable")
        return self.governance.list_approvals(
            self.workspace_id,
            self.actor_id,
            statuses=statuses,
            request_id=request_id,
            run_id=run_id,
            limit=limit,
        )

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        """Return one approval only after applying workspace and read ACLs."""

        self._require_workspace_action("approval.read")
        if self.governance is None:
            raise ContractError("Team approval service is unavailable")
        approval = self.governance.get_approval(approval_id)
        if approval is None or approval["workspace_id"] != self.workspace_id:
            raise ContractError("approval not found")
        return approval

    def decide_approval(
        self, approval_id: str, target: ApprovalStatus | str
    ) -> dict[str, Any]:
        """Resolve an approval through the owner/admin-only governance boundary."""

        self._require_workspace_action("approval.decide")
        if self.governance is None:
            raise ContractError("Team approval service is unavailable")
        self.get_approval(approval_id)
        updated = self.governance.decide_approval(
            approval_id,
            target,
            approver_actor_id=self.actor_id,
        )
        # A denial/cancel/expiry must close a parked run durably.  An approved
        # record intentionally remains claimable by the canonical scheduler so
        # the same run can resume with the same delivery identity.
        self.coordinator.resolve_approval(updated)
        self._emit(
            "approval.updated",
            "approval",
            approval_id,
            {"status": updated["status"], "approver_actor_id": self.actor_id},
        )
        requester_actor_id = str(updated.get("requested_by_actor_id") or "")
        if requester_actor_id and requester_actor_id != self.actor_id:
            self._create_notification(
                requester_actor_id,
                "approval.updated",
                ResourceRef("approval", approval_id),
                f"approval:{approval_id}:{updated['status']}",
            )
        return updated

    def cancel_approval(self, approval_id: str) -> dict[str, Any]:
        """Allow only the original requester to cancel a pending approval."""

        self._require_workspace_action("approval.read")
        if self.governance is None:
            raise ContractError("Team approval service is unavailable")
        self.get_approval(approval_id)
        updated = self.governance.cancel_approval(approval_id, actor_id=self.actor_id)
        self.coordinator.resolve_approval(updated)
        self._emit(
            "approval.updated",
            "approval",
            approval_id,
            {"status": updated["status"], "canceled_by_actor_id": self.actor_id},
        )
        return updated

    def _notification_link(self, notification: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Resolve a recipient-safe deep link from the notification aggregate."""

        aggregate_type = str(notification.get("aggregate_type") or "")
        aggregate_id = str(notification.get("aggregate_id") or "")
        if not aggregate_id:
            return None
        if aggregate_type == "message":
            message = self.get_message(aggregate_id)
            if message is None:
                return None
            return {
                "surface": "team",
                "conversation_id": message["conversation_id"],
                "thread_id": message.get("thread_id"),
                "message_id": aggregate_id,
            }
        if aggregate_type == "task" and self.tasks is not None:
            task = self.tasks.get_task(aggregate_id)
            if task is None or task["workspace_id"] != self.workspace_id:
                return None
            return {
                "surface": "team",
                "view": "tasks",
                "task_id": aggregate_id,
            }
        if aggregate_type == "approval":
            return {"surface": "inbox", "approval_id": aggregate_id}
        if aggregate_type == "automation":
            return {"surface": "automations", "automation_id": aggregate_id}
        return None

    def get_notification_preferences(self) -> dict[str, Any]:
        self._require_workspace_member()
        if self.governance is None:
            raise ContractError("Team notification service is unavailable")
        return self.governance.get_notification_preferences(
            self.workspace_id, self.actor_id
        )

    def update_notification_preferences(self, **changes: Any) -> dict[str, Any]:
        self._require_workspace_member()
        if self.governance is None:
            raise ContractError("Team notification service is unavailable")
        return self.governance.set_notification_preferences(
            self.workspace_id, self.actor_id, **changes
        )

    def list_events(self, *, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        self._require_workspace_member()
        if self.events is None:
            raise ContractError("Team event service is unavailable")
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise ContractError("after_sequence must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ContractError("limit must be an integer between 1 and 200")

        # A workspace event log is append-only, but not every event in that log is
        # visible to every workspace member.  Read in source-log order and advance
        # the cursor over every scanned event (including hidden ones), so a client
        # cannot get stuck replaying an inaccessible event forever.
        allowed_conversations = set(
            self.conversations.list_accessible_conversation_ids(
                self.workspace_id,
                self.actor_id,
                include_archived=True,
            )
        )
        allowed_artifacts = (
            self.artifacts.list_accessible_artifact_ids(self.workspace_id, self.actor_id)
            if self.artifacts is not None
            else set()
        )
        visible: list[dict[str, Any]] = []
        cursor = after_sequence
        has_more = False
        while len(visible) < limit:
            page = self.events.list_workspace_events(
                self.workspace_id,
                after_sequence=cursor,
                # The repository caps pages at 200.  Using the requested size
                # preserves the caller's page boundary while the loop skips a
                # run of hidden records without dropping visible ones.
                limit=limit,
            )
            source_events = page.get("events", ())
            if not source_events:
                has_more = False
                break
            for event in source_events:
                cursor = int(event["sequence"])
                if self._event_visible_to_requester(
                    event,
                    allowed_conversations=allowed_conversations,
                    allowed_artifacts=allowed_artifacts,
                ):
                    visible.append(event)
                    if len(visible) == limit:
                        has_more = cursor < int(source_events[-1]["sequence"]) or bool(
                            page.get("has_more")
                        )
                        break
            if len(visible) == limit:
                break
            cursor = int(page["next_sequence"])
            if not page.get("has_more"):
                has_more = False
                break
            has_more = True
        return {
            "events": visible,
            "next_sequence": cursor,
            "has_more": has_more,
        }

    def _event_visible_to_requester(
        self,
        event: Mapping[str, Any],
        *,
        allowed_conversations: set[str],
        allowed_artifacts: set[str],
    ) -> bool:
        """Apply Team ACLs to one durable event without exposing its payload."""

        aggregate_type = str(event.get("aggregate_type") or "")
        aggregate_id = str(event.get("aggregate_id") or "")
        payload = event.get("payload")
        payload = dict(payload) if isinstance(payload, Mapping) else {}

        if aggregate_type in {"conversation", "message", "thread"}:
            conversation_id = str(payload.get("conversation_id") or "").strip()
            if not conversation_id and aggregate_type == "conversation":
                conversation_id = aggregate_id
            if not conversation_id and aggregate_type == "thread":
                thread = self.conversations.get_thread(aggregate_id)
                conversation_id = str((thread or {}).get("conversation_id") or "")
            if not conversation_id and aggregate_type == "message":
                message = self.conversations.get_message(aggregate_id)
                conversation_id = str((message or {}).get("conversation_id") or "")
            return conversation_id in allowed_conversations

        if aggregate_type == "artifact":
            return aggregate_id in allowed_artifacts

        if aggregate_type == "notification":
            return str(payload.get("recipient_actor_id") or "") == self.actor_id

        if aggregate_type == "memory" and self.context is not None:
            get_memory = getattr(self.context, "get_memory", None)
            memory = get_memory(aggregate_id) if callable(get_memory) else None
            return bool(
                memory
                and memory.get("workspace_id") == self.workspace_id
                and self._scope_is_visible(memory.get("scope_kind"), memory.get("scope_id"))
            )

        if aggregate_type == "context_pin" and self.context is not None:
            get_pin = getattr(self.context, "get_pin", None)
            pin = get_pin(aggregate_id) if callable(get_pin) else None
            return bool(
                pin
                and pin.get("workspace_id") == self.workspace_id
                and self._scope_is_visible(pin.get("scope_kind"), pin.get("scope_id"))
            )

        if aggregate_type == "context_share" and self.context is not None:
            get_share = getattr(self.context, "get_share", None)
            share = get_share(aggregate_id) if callable(get_share) else None
            return bool(
                share
                and share.get("workspace_id") == self.workspace_id
                and self._scope_is_visible(
                    share.get("target_scope_kind"), share.get("target_scope_id")
                )
            )

        # Tasks, runs, handoffs, connectors, and integrations are workspace-level
        # Team resources.  The service already proves workspace membership before
        # entering this method; unknown future aggregates retain that same default
        # until they declare a narrower boundary.
        return True

    def _scope_is_visible(self, scope: Any, scope_id: Any) -> bool:
        try:
            self._require_scope_visible(scope, str(scope_id or ""))
        except ContractError:
            return False
        return True

    def list_activity(self, *, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        """Project durable domain events into a high-signal Activity feed.

        Run/tool internals stay in the run event log. This projection contains
        only durable user-facing state changes and keeps the event cursor used
        by the realtime stream.
        """

        page = self.list_events(after_sequence=after_sequence, limit=limit)
        activity_prefixes = (
            "approval.",
            "automation.",
            "connector.",
            "context.",
            "conversation.",
            "crew_run.",
            "handoff.",
            "integration.",
            "memory.",
            "message.",
            "notification.",
            "sandbox.",
            "task.",
        )
        activity_types = {"run.failed", "run.succeeded", "run.canceled", "run.timed_out"}
        activities: list[dict[str, Any]] = []
        for event in page.get("events", ()):
            event_type = str(event.get("event_type") or "")
            if not (event_type in activity_types or event_type.startswith(activity_prefixes)):
                continue
            item = dict(event)
            item["activity_id"] = item["event_id"]
            item["activity_kind"] = event_type.replace(".", " ")
            raw_payload = dict(item.get("payload") or {})
            safe_payload = redact_sensitive(raw_payload)
            # Domain identifiers are routing metadata, not credentials.  The
            # generic value redactor intentionally masks long strings, so put
            # back the identifiers needed by Activity deep-links after the
            # recursive secret pass.
            for key in (
                "conversation_id",
                "message_id",
                "thread_id",
                "task_id",
                "run_id",
                "request_id",
                "notification_id",
                "agent_id",
                "connector_id",
                "integration_id",
                "artifact_id",
                "handoff_id",
                "approval_id",
                "automation_id",
            ):
                value = raw_payload.get(key)
                if isinstance(value, str) and value:
                    safe_payload[key] = value
            item["payload"] = safe_payload
            activities.append(item)
        return {**page, "activities": activities}

    def _require_workspace_member(self) -> None:
        with self.identities.database._lock:
            row = self.identities.database._conn.execute(
                """
                SELECT 1 FROM workspace_memberships
                WHERE workspace_id = ? AND actor_id = ? AND status = 'active'
                """,
                (self.workspace_id, self.actor_id),
            ).fetchone()
        if row is None:
            raise ContractError("requester is not an active Team workspace member")

    def _workspace_role(self) -> WorkspaceRole:
        with self.identities.database._lock:
            row = self.identities.database._conn.execute(
                """
                SELECT workspace_role FROM workspace_memberships
                WHERE workspace_id = ? AND actor_id = ? AND status = 'active'
                """,
                (self.workspace_id, self.actor_id),
            ).fetchone()
        if row is None:
            raise ContractError("requester is not an active Team workspace member")
        try:
            return WorkspaceRole(row["workspace_role"])
        except ValueError as error:
            raise ContractError("workspace membership has an invalid role") from error

    def _require_workspace_action(self, action: str) -> None:
        role = self._workspace_role()
        if action not in WORKSPACE_ROLE_ACTIONS[role]:
            raise ContractError(f"workspace role {role.value} cannot perform {action}")

    def _require_scope_visible(self, scope: ContextScope | str, scope_id: str) -> None:
        try:
            normalized_scope = ContextScope(scope)
        except (TypeError, ValueError) as error:
            raise ContractError("invalid context scope") from error
        scope_id = str(scope_id or "").strip()
        if not scope_id:
            raise ContractError("context scope id is required")
        if normalized_scope is ContextScope.WORKSPACE:
            if scope_id != self.workspace_id:
                raise ContractError("context workspace scope is not this workspace")
            return
        if normalized_scope in {ContextScope.DIRECT_MESSAGE, ContextScope.GROUP}:
            conversation = self.conversations.get_conversation(scope_id)
            if (
                conversation is None
                or conversation["workspace_id"] != self.workspace_id
                or (
                    normalized_scope is ContextScope.DIRECT_MESSAGE
                    and conversation["conversation_kind"] != "direct"
                )
                or (
                    normalized_scope is ContextScope.GROUP
                    and conversation["conversation_kind"] != "group"
                )
            ):
                raise ContractError("context conversation scope is not visible")
            if not any(
                member["actor_id"] == self.actor_id and member["left_at"] is None
                for member in conversation["members"]
            ):
                raise ContractError("context conversation scope is not visible")
            return
        if normalized_scope is ContextScope.TASK:
            if self.tasks is None:
                raise ContractError("Team task service is unavailable")
            task = self.tasks.get_task(scope_id)
            if task is None or task["workspace_id"] != self.workspace_id:
                raise ContractError("context task scope is not visible")
            return
        if normalized_scope is ContextScope.AGENT_PRIVATE:
            installation = self.agents.get_installation(scope_id)
            if installation is None or installation["workspace_id"] != self.workspace_id:
                raise ContractError("context agent scope is not visible")
            return
        raise ContractError("run scratch context is runtime-only")

    @staticmethod
    def _redact_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in artifact.items() if key != "revision"}
        result["revisions"] = [
            {key: value for key, value in revision.items() if key != "storage_ref"}
            for revision in artifact.get("revisions", ())
        ]
        if "revision" in artifact:
            result["revision"] = {
                key: value for key, value in artifact["revision"].items() if key != "storage_ref"
            }
        return result

    def _emit(
        self,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        *,
        event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> None:
        if self.events is None:
            return
        event = DomainEvent(
            event_id=event_id or new_domain_id(DomainIdPrefix.EVENT),
            event_type=event_type,
            workspace_id=self.workspace_id,
            actor_id=actor_id or self.actor_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id or new_domain_id(DomainIdPrefix.EVENT),
            causation_id=causation_id,
            payload=payload,
            occurred_at=occurred_at or utc_now(),
        )
        persisted = self.events.append(event)
        if self.event_sink is not None:
            self.event_sink(persisted)

    def _notify_conversation(
        self,
        conversation: Mapping[str, Any],
        *,
        notification_kind: str,
        aggregate: ResourceRef,
        dedupe_key: str,
        mention_actor_ids: Sequence[str] = (),
    ) -> None:
        if self.governance is None:
            return
        mentioned = {
            str(actor_id).strip()
            for actor_id in mention_actor_ids
            if str(actor_id).strip()
        }
        for member in conversation.get("members", ()):
            recipient_id = member.get("actor_id")
            if recipient_id == self.actor_id or member.get("actor_kind") != ActorKind.HUMAN.value:
                continue
            is_mentioned = recipient_id in mentioned
            if self._member_is_muted(member.get("muted_until")) and not is_mentioned:
                continue
            if not self._notification_allowed(recipient_id, notification_kind, is_mentioned):
                continue
            self._create_notification(
                recipient_id,
                "mention" if is_mentioned else notification_kind,
                aggregate,
                f"{dedupe_key}:actor:{recipient_id}",
            )

    def _notify_task(
        self,
        task: Mapping[str, Any],
        *,
        notification_kind: str,
        dedupe_key: str,
    ) -> None:
        if self.governance is None:
            return
        recipient_ids = {
            task.get("created_by_actor_id"),
            task.get("reviewer_actor_id"),
            *(watcher.get("actor_id") for watcher in task.get("watchers", ())),
        }
        owner_agent_id = task.get("owner_agent_id")
        if owner_agent_id:
            installation = self.agents.get_installation(owner_agent_id)
            if installation is not None:
                recipient_ids.add(installation.get("actor_id"))
        for recipient_id in recipient_ids:
            if not recipient_id or recipient_id == self.actor_id:
                continue
            with self.identities.database._lock:
                actor = self.identities.database._conn.execute(
                    "SELECT actor_kind FROM actors WHERE actor_id = ?",
                    (recipient_id,),
                ).fetchone()
            if actor is None or actor["actor_kind"] != ActorKind.HUMAN.value:
                continue
            if not self._notification_allowed(recipient_id, notification_kind, False):
                continue
            self._create_notification(
                recipient_id,
                notification_kind,
                ResourceRef("task", task["task_id"]),
                f"{dedupe_key}:actor:{recipient_id}",
            )

    def _create_notification(
        self,
        recipient_actor_id: str,
        notification_kind: str,
        aggregate: ResourceRef,
        dedupe_key: str,
    ) -> None:
        if self.governance is None:
            return
        try:
            notification = self.governance.create_notification(
                new_domain_id(DomainIdPrefix.NOTIFICATION),
                self.workspace_id,
                recipient_actor_id,
                notification_kind,
                aggregate,
                dedupe_key,
            )
        except PersistenceConflictError:
            # The command may be replayed after a process crash.  The unique
            # recipient/dedupe key makes notification creation at-least-once.
            return
        self._emit(
            "notification.created",
            "notification",
            notification["notification_id"],
            {
                "recipient_actor_id": recipient_actor_id,
                "notification_kind": notification_kind,
                "aggregate_type": aggregate.kind,
                "aggregate_id": aggregate.id,
            },
        )

    def _notification_allowed(
        self,
        recipient_actor_id: str,
        notification_kind: str,
        mentioned: bool,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        if self.governance is None:
            return False
        preferences = self.governance.get_notification_preferences(
            self.workspace_id, recipient_actor_id
        )
        if preferences["muted"] and not mentioned:
            return False
        if not mentioned and self._quiet_hours_active(preferences, now=now):
            return False
        if preferences["mention_only"] and not mentioned:
            mention_only_kinds = {
                "task.assigned",
                "approval.created",
                "approval.updated",
                "automation.completed",
                "run.failed",
                "blocker.created",
            }
            if notification_kind not in mention_only_kinds:
                return False
        event_preferences = preferences.get("event_preferences") or {}
        if event_preferences.get(notification_kind) is False and not mentioned:
            return False
        return True

    @staticmethod
    def _quiet_hours_active(
        preferences: Mapping[str, Any], *, now: Optional[datetime] = None
    ) -> bool:
        """Evaluate a user's local quiet-hours interval, including overnight windows."""

        quiet = preferences.get("quiet_hours") if isinstance(preferences, Mapping) else None
        if not isinstance(quiet, Mapping):
            return False
        start = str(quiet.get("start") or "").strip()
        end = str(quiet.get("end") or "").strip()
        timezone_name = str(quiet.get("timezone") or "UTC").strip()
        if not start or not end:
            return False
        try:
            start_minutes = _clock_minutes(start)
            end_minutes = _clock_minutes(end)
            zone = ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError):
            # Preferences are validated at write time; malformed legacy rows do
            # not make the notification path crash or accidentally suppress all
            # notifications.
            return False
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        local = current.astimezone(zone)
        current_minutes = local.hour * 60 + local.minute
        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    @staticmethod
    def _member_is_muted(muted_until: Optional[str]) -> bool:
        if not muted_until:
            return False
        try:
            value = datetime.fromisoformat(str(muted_until).replace("Z", "+00:00"))
            return value > datetime.now(timezone.utc)
        except ValueError:
            return False

    def _permission_context(
        self,
        agent_id: str,
        *,
        requester: Optional[ActorRef] = None,
    ) -> PermissionContext:
        requester = requester or self.requester
        installation = self.agents.get_installation(agent_id)
        if installation is None or installation["workspace_id"] != self.workspace_id:
            raise ContractError(f"agent installation not found: {agent_id}")
        definition = self.agents.get_definition(installation["agent_definition_id"])
        revision = self.agents.get_revision(installation["active_revision_id"])
        if definition is None or revision is None:
            raise ContractError(f"agent revision is missing: {agent_id}")
        with self.identities.database._lock:
            membership = self.identities.database._conn.execute(
                """
                SELECT workspace_role FROM workspace_memberships
                WHERE workspace_id = ? AND actor_id = ? AND status = 'active'
                """,
                (self.workspace_id, requester.id),
            ).fetchone()
        if membership is None:
            raise ContractError("requester is not an active Team workspace member")
        sandbox_policy = revision.get("sandbox_policy") or {}
        actions = sandbox_policy.get("actions") if isinstance(sandbox_policy, Mapping) else None
        sandbox_actions = frozenset(
            actions if actions is not None else {"sandbox.read", "run.execute"}
        )
        return PermissionContext(
            workspace_id=self.workspace_id,
            requester=requester,
            requester_role=WorkspaceRole(membership["workspace_role"]),
            agent_id=agent_id,
            agent_actor_id=installation["actor_id"],
            agent_role=definition["base_role"],
            sandbox=SandboxPolicy(actions=sandbox_actions),
            conversation_actions=frozenset({"conversation.read", "conversation.send", "run.execute"}),
        )

    def _effective_work_budget(
        self,
        agent_ids: Sequence[str],
        requested: WorkBudget | Mapping[str, Any] | None,
    ) -> WorkBudget:
        """Intersect a caller budget with every targeted revision's caps.

        A request may lower an agent's limits, but it can never raise the
        immutable budget policy attached to that agent revision. For fan-out,
        the strictest cap is used so one request has one auditable budget.
        """

        if isinstance(requested, WorkBudget):
            values = requested.to_dict()
        else:
            values = WorkBudget.from_dict(requested).to_dict()
        normalized_agents = tuple(dict.fromkeys(str(agent_id).strip() for agent_id in agent_ids))
        for agent_id in normalized_agents:
            if not agent_id:
                raise ContractError("budget target agent id is required")
            installation = self.agents.get_installation(agent_id)
            if installation is None or installation["workspace_id"] != self.workspace_id:
                raise ContractError(f"agent installation not found: {agent_id}")
            revision = self.agents.get_revision(installation["active_revision_id"])
            if revision is None:
                raise ContractError(f"agent revision is missing: {agent_id}")
            policy = WorkBudget.from_dict(revision.get("budget_policy") or {})
            for field_name, cap in policy.to_dict().items():
                if cap is not None and (
                    values[field_name] is None or cap < int(values[field_name])
                ):
                    values[field_name] = cap
        return WorkBudget.from_dict(values)
