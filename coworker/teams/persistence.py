"""Durable normalized persistence foundation for the Astra Team domain.

This is deliberately separate from the legacy ``teams.db`` and ``chat.db`` stores.
The migration/cutover task can import those stores into this schema without altering
their event hashes or conversation JSONL files in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
import base64
import binascii
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from .contracts import (
    AGENT_EDGES,
    ARTIFACT_EDGES,
    APPROVAL_EDGES,
    CONVERSATION_EDGES,
    DELIVERY_EDGES,
    SANDBOX_EDGES,
    RUN_EDGES,
    ActorKind,
    ActorRef,
    AgentRole,
    AgentStatus,
    ArtifactStatus,
    ApprovalStatus,
    ContractError,
    ConversationKind,
    ConversationStatus,
    DeliveryStatus,
    DomainEvent,
    DomainIdPrefix,
    HandoffContract,
    ResourceRef,
    ReplyMode,
    RunStatus,
    SandboxStatus,
    WorkRequest,
    WorkBudget,
    _as_enum,
    _optional_id,
    _required_id,
    _required_kind,
    new_domain_id,
    can_transition,
    normalize_agent_avatar_id,
    require_transition,
)
from .lifecycle import TaskStatus, TransitionAuthority, require_task_transition
from .sharing import (
    ArtifactPublication,
    ArtifactVisibility,
    ContextShare,
    ContextScope,
    HistoryPolicy,
)
from .model import ItemState


class MigrationError(RuntimeError):
    """A Team schema migration failed or no longer matches its recorded checksum."""


class PersistenceConflictError(ContractError):
    """A durable record key already exists with a different immutable payload."""


class BudgetExceededError(ContractError):
    """A WorkRequest usage increment would cross one of its explicit budgets."""

    def __init__(self, message: str, *, request_id: Optional[str] = None, fields: Sequence[str] = ()) -> None:
        self.request_id = request_id
        self.fields = tuple(fields)
        super().__init__(message)


@dataclass(frozen=True)
class Migration:
    migration_id: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement boundary --\n".join(statement.strip() for statement in self.statements)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return decoded


DEFAULT_MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        "0001_identity_agents",
        (
            """
            CREATE TABLE workspaces (
                workspace_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                archived_at TEXT
            )
            """,
            """
            CREATE TABLE actors (
                actor_id TEXT PRIMARY KEY,
                actor_kind TEXT NOT NULL CHECK (actor_kind IN ('human', 'agent', 'system')),
                display_name TEXT NOT NULL DEFAULT '',
                handle TEXT,
                created_at TEXT NOT NULL,
                archived_at TEXT
            )
            """,
            """
            CREATE TABLE workspace_memberships (
                membership_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                workspace_role TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'archived')),
                created_at TEXT NOT NULL,
                archived_at TEXT,
                UNIQUE (workspace_id, actor_id)
            )
            """,
            """
            CREATE TABLE workspace_invitations (
                invitation_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                invited_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                invited_email TEXT,
                invited_actor_id TEXT REFERENCES actors(actor_id),
                workspace_role TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
                expires_at TEXT,
                accepted_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE agent_definitions (
                agent_definition_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                owner_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                display_name TEXT NOT NULL,
                handle TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                base_role TEXT NOT NULL CHECK (base_role IN ('supervisor', 'explorer', 'builder', 'verifier', 'operator')),
                current_revision_id TEXT,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted')),
                created_at TEXT NOT NULL,
                archived_at TEXT,
                UNIQUE (workspace_id, handle)
            )
            """,
            """
            CREATE TABLE agent_revisions (
                agent_revision_id TEXT PRIMARY KEY,
                agent_definition_id TEXT NOT NULL REFERENCES agent_definitions(agent_definition_id),
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                instructions TEXT NOT NULL,
                model_policy_json TEXT NOT NULL DEFAULT '{}',
                sandbox_policy_json TEXT NOT NULL DEFAULT '{}',
                memory_policy_json TEXT NOT NULL DEFAULT '{}',
                reply_policy_json TEXT NOT NULL DEFAULT '{}',
                budget_policy_json TEXT NOT NULL DEFAULT '{}',
                approval_policy_json TEXT NOT NULL DEFAULT '{}',
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                UNIQUE (agent_definition_id, revision_number)
            )
            """,
            """
            CREATE TABLE agent_revision_capabilities (
                agent_revision_id TEXT NOT NULL REFERENCES agent_revisions(agent_revision_id),
                capability_kind TEXT NOT NULL CHECK (capability_kind IN ('skill', 'plugin', 'tool', 'connector', 'mcp')),
                capability_ref TEXT NOT NULL,
                required INTEGER NOT NULL DEFAULT 0 CHECK (required IN (0, 1)),
                config_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (agent_revision_id, capability_kind, capability_ref)
            )
            """,
            """
            CREATE TABLE agent_installations (
                agent_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                actor_id TEXT NOT NULL UNIQUE REFERENCES actors(actor_id),
                agent_definition_id TEXT NOT NULL REFERENCES agent_definitions(agent_definition_id),
                active_revision_id TEXT NOT NULL REFERENCES agent_revisions(agent_revision_id),
                status TEXT NOT NULL CHECK (status IN ('provisioning', 'ready', 'working', 'waiting', 'paused', 'offline', 'error', 'archived')),
                max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency > 0),
                created_at TEXT NOT NULL,
                archived_at TEXT,
                UNIQUE (workspace_id, agent_definition_id)
            )
            """,
            "CREATE INDEX idx_memberships_workspace_status ON workspace_memberships (workspace_id, status, actor_id)",
            "CREATE INDEX idx_agent_definitions_workspace_status ON agent_definitions (workspace_id, status, handle)",
            "CREATE INDEX idx_agent_installations_workspace_status ON agent_installations (workspace_id, status, agent_id)",
        ),
    ),
    Migration(
        "0002_conversations",
        (
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                conversation_kind TEXT NOT NULL CHECK (conversation_kind IN ('direct', 'group')),
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived', 'deleted')),
                reply_mode TEXT NOT NULL CHECK (reply_mode IN ('mention_only', 'coordinator', 'all_messages')),
                coordinator_agent_id TEXT REFERENCES agent_installations(agent_id),
                allow_at_all INTEGER NOT NULL DEFAULT 0 CHECK (allow_at_all IN (0, 1)),
                max_fanout INTEGER NOT NULL DEFAULT 8 CHECK (max_fanout > 0),
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                archived_at TEXT
            )
            """,
            """
            CREATE TABLE conversation_members (
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                member_role TEXT NOT NULL DEFAULT 'member',
                history_policy TEXT NOT NULL CHECK (history_policy IN ('all_history', 'from_join', 'selected_context')),
                joined_at TEXT NOT NULL,
                left_at TEXT,
                PRIMARY KEY (conversation_id, actor_id)
            )
            """,
            """
            CREATE TABLE threads (
                thread_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                root_message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id) DEFERRABLE INITIALLY DEFERRED,
                created_at TEXT NOT NULL,
                archived_at TEXT
            )
            """,
            """
            CREATE TABLE messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                thread_id TEXT REFERENCES threads(thread_id) DEFERRABLE INITIALLY DEFERRED,
                parent_message_id TEXT REFERENCES messages(message_id) DEFERRABLE INITIALLY DEFERRED,
                author_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                author_kind TEXT NOT NULL CHECK (author_kind IN ('human', 'agent', 'system')),
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'tombstoned')),
                client_request_id TEXT,
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                created_at TEXT NOT NULL,
                deleted_at TEXT,
                UNIQUE (conversation_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE message_revisions (
                message_revision_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL REFERENCES messages(message_id),
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                editor_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (message_id, revision_number)
            )
            """,
            """
            CREATE TABLE message_mentions (
                message_id TEXT NOT NULL REFERENCES messages(message_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                PRIMARY KEY (message_id, actor_id)
            )
            """,
            """
            CREATE TABLE message_reactions (
                message_id TEXT NOT NULL REFERENCES messages(message_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                emoji TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (message_id, actor_id, emoji)
            )
            """,
            """
            CREATE TABLE conversation_pins (
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                message_id TEXT NOT NULL REFERENCES messages(message_id),
                pinned_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (conversation_id, message_id)
            )
            """,
            """
            CREATE TABLE conversation_read_cursors (
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                last_read_message_id TEXT REFERENCES messages(message_id),
                last_read_at TEXT,
                PRIMARY KEY (conversation_id, actor_id)
            )
            """,
            "CREATE INDEX idx_conversations_workspace_status ON conversations (workspace_id, status, created_at DESC)",
            "CREATE INDEX idx_messages_conversation_created ON messages (conversation_id, created_at, message_id)",
            "CREATE INDEX idx_messages_thread_created ON messages (thread_id, created_at, message_id)",
            "CREATE INDEX idx_mentions_actor ON message_mentions (actor_id, message_id)",
        ),
    ),
    Migration(
        "0003_tasks_work_sandboxes",
        (
            """
            CREATE TABLE sandboxes (
                sandbox_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                agent_id TEXT NOT NULL UNIQUE REFERENCES agent_installations(agent_id),
                provider_kind TEXT NOT NULL,
                isolation_level TEXT NOT NULL CHECK (isolation_level IN ('weak_local', 'container', 'remote', 'external_runtime')),
                status TEXT NOT NULL CHECK (status IN ('provisioning', 'ready', 'busy', 'suspended', 'offline', 'error', 'archived')),
                home_ref TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            )
            """,
            """
            CREATE TABLE sandbox_mounts (
                sandbox_mount_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL REFERENCES sandboxes(sandbox_id),
                source_ref TEXT NOT NULL,
                target_path TEXT NOT NULL,
                access_mode TEXT NOT NULL CHECK (access_mode IN ('read', 'write')),
                granted_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """,
            """
            CREATE TABLE sandbox_snapshots (
                sandbox_snapshot_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL REFERENCES sandboxes(sandbox_id),
                snapshot_ref TEXT NOT NULL,
                created_by_actor_id TEXT REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                UNIQUE (sandbox_id, snapshot_ref)
            )
            """,
            """
            CREATE TABLE sandbox_resource_policies (
                sandbox_id TEXT PRIMARY KEY REFERENCES sandboxes(sandbox_id),
                cpu_millis INTEGER,
                memory_mib INTEGER,
                disk_mib INTEGER,
                max_processes INTEGER,
                max_wall_seconds INTEGER,
                network_policy TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                canonical_thread_id TEXT UNIQUE REFERENCES threads(thread_id),
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                acceptance_criteria TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'blocked', 'review', 'done', 'canceled')),
                owner_agent_id TEXT REFERENCES agent_installations(agent_id),
                reviewer_actor_id TEXT REFERENCES actors(actor_id),
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                priority INTEGER NOT NULL DEFAULT 0,
                due_at TEXT,
                parent_task_id TEXT REFERENCES tasks(task_id) DEFERRABLE INITIALLY DEFERRED,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                canceled_at TEXT
            )
            """,
            """
            CREATE TABLE task_dependencies (
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                depends_on_task_id TEXT NOT NULL REFERENCES tasks(task_id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (task_id, depends_on_task_id),
                CHECK (task_id <> depends_on_task_id)
            )
            """,
            """
            CREATE TABLE task_events (
                task_event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE crew_runs (
                crew_run_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                root_task_id TEXT REFERENCES tasks(task_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                status TEXT NOT NULL CHECK (status IN ('active', 'canceled', 'completed', 'failed')),
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                finished_at TEXT
            )
            """,
            """
            CREATE TABLE work_requests (
                request_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                crew_run_id TEXT REFERENCES crew_runs(crew_run_id),
                source_kind TEXT NOT NULL CHECK (source_kind IN ('direct_message', 'group_message', 'task', 'automation', 'connector', 'handoff')),
                source_ref_kind TEXT NOT NULL,
                source_ref_id TEXT NOT NULL,
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                conversation_id TEXT REFERENCES conversations(conversation_id),
                thread_id TEXT REFERENCES threads(thread_id),
                task_id TEXT REFERENCES tasks(task_id),
                instruction TEXT NOT NULL,
                expected_output TEXT NOT NULL DEFAULT '',
                permission_scope_json TEXT NOT NULL DEFAULT '{}',
                budget_json TEXT NOT NULL DEFAULT '{}',
                priority INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (workspace_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE work_request_targets (
                request_id TEXT NOT NULL REFERENCES work_requests(request_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (request_id, agent_id),
                UNIQUE (request_id, position)
            )
            """,
            """
            CREATE TABLE deliveries (
                delivery_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL REFERENCES work_requests(request_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                target_agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                status TEXT NOT NULL CHECK (status IN ('pending', 'leased', 'dispatched', 'acknowledged', 'retry_wait', 'dead_letter', 'canceled')),
                idempotency_key TEXT NOT NULL,
                next_attempt_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (workspace_id, target_agent_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE delivery_attempts (
                delivery_attempt_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
                attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                outcome TEXT NOT NULL CHECK (outcome IN ('started', 'accepted', 'failed', 'timed_out', 'canceled')),
                error_code TEXT,
                error_detail TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE (delivery_id, attempt_number)
            )
            """,
            """
            CREATE TABLE delivery_leases (
                delivery_id TEXT PRIMARY KEY REFERENCES deliveries(delivery_id),
                lease_token TEXT NOT NULL UNIQUE,
                holder_id TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
                request_id TEXT NOT NULL REFERENCES work_requests(request_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                agent_revision_id TEXT NOT NULL REFERENCES agent_revisions(agent_revision_id),
                sandbox_id TEXT NOT NULL REFERENCES sandboxes(sandbox_id),
                status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_input', 'waiting_approval', 'succeeded', 'failed', 'timed_out', 'canceled')),
                budget_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE (delivery_id)
            )
            """,
            """
            CREATE TABLE run_events (
                run_event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                event_type TEXT NOT NULL,
                actor_id TEXT REFERENCES actors(actor_id),
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE domain_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_sandboxes_workspace_status ON sandboxes (workspace_id, status, sandbox_id)",
            "CREATE INDEX idx_tasks_workspace_status ON tasks (workspace_id, status, updated_at DESC, task_id)",
            "CREATE INDEX idx_task_dependencies_dependency ON task_dependencies (depends_on_task_id, task_id)",
            "CREATE INDEX idx_work_requests_workspace_created ON work_requests (workspace_id, created_at, request_id)",
            "CREATE INDEX idx_deliveries_agent_status ON deliveries (target_agent_id, status, next_attempt_at, created_at)",
            "CREATE INDEX idx_runs_request ON runs (request_id, created_at)",
            "CREATE INDEX idx_domain_events_workspace_sequence ON domain_events (workspace_id, sequence)",
            "CREATE INDEX idx_domain_events_aggregate ON domain_events (aggregate_type, aggregate_id, sequence)",
        ),
    ),
    Migration(
        "0004_artifacts_context",
        (
            """
            CREATE TABLE artifacts (
                artifact_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                status TEXT NOT NULL CHECK (status IN ('published', 'archived', 'deleted')),
                visibility TEXT NOT NULL CHECK (visibility IN ('agent_private', 'direct_message', 'group', 'task', 'workspace')),
                scope_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                archived_at TEXT,
                deleted_at TEXT
            )
            """,
            """
            CREATE TABLE artifact_revisions (
                artifact_revision_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                source_task_id TEXT REFERENCES tasks(task_id),
                source_ref_kind TEXT,
                source_ref_id TEXT,
                publisher_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                sha256 TEXT NOT NULL,
                storage_ref TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (artifact_id, revision_number),
                UNIQUE (artifact_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE artifact_links (
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                artifact_revision_id TEXT NOT NULL REFERENCES artifact_revisions(artifact_revision_id),
                linked_aggregate_type TEXT NOT NULL,
                linked_aggregate_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (artifact_revision_id, linked_aggregate_type, linked_aggregate_id)
            )
            """,
            """
            CREATE TABLE memory_records (
                memory_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent_private', 'direct_message', 'group', 'task', 'workspace', 'run_scratch')),
                scope_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_artifact_id TEXT REFERENCES artifacts(artifact_id),
                source_run_id TEXT REFERENCES runs(run_id),
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                expires_at TEXT,
                deleted_at TEXT
            )
            """,
            """
            CREATE TABLE context_pins (
                context_pin_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent_private', 'direct_message', 'group', 'task', 'workspace', 'run_scratch')),
                scope_id TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                pinned_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                expires_at TEXT,
                UNIQUE (scope_kind, scope_id, resource_kind, resource_id)
            )
            """,
            """
            CREATE TABLE context_shares (
                context_share_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                source_scope_kind TEXT NOT NULL,
                source_scope_id TEXT NOT NULL,
                target_scope_kind TEXT NOT NULL,
                target_scope_id TEXT NOT NULL,
                shared_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (workspace_id, idempotency_key)
            )
            """,
            "CREATE INDEX idx_artifacts_workspace_visibility ON artifacts (workspace_id, visibility, scope_id, created_at DESC)",
            "CREATE INDEX idx_artifact_revisions_run ON artifact_revisions (source_run_id, created_at)",
            "CREATE INDEX idx_memory_scope ON memory_records (workspace_id, scope_kind, scope_id, created_at DESC)",
            "CREATE INDEX idx_context_shares_target ON context_shares (workspace_id, target_scope_kind, target_scope_id, created_at)",
        ),
    ),
    Migration(
        "0005_governance_search",
        (
            """
            CREATE TABLE approvals (
                approval_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                request_id TEXT REFERENCES work_requests(request_id),
                run_id TEXT REFERENCES runs(run_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                approver_actor_id TEXT REFERENCES actors(actor_id),
                action_kind TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'canceled')),
                reason TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                decided_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE grants (
                grant_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                grantor_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                grantee_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                agent_id TEXT REFERENCES agent_installations(agent_id),
                grant_kind TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                action_kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
                expires_at TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """,
            """
            CREATE TABLE connector_bindings (
                connector_binding_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                connector_kind TEXT NOT NULL,
                external_workspace_id TEXT,
                external_identity_id TEXT,
                agent_id TEXT REFERENCES agent_installations(agent_id),
                grant_id TEXT REFERENCES grants(grant_id),
                status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'error')),
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                UNIQUE (workspace_id, connector_kind, external_workspace_id, external_identity_id, agent_id)
            )
            """,
            """
            CREATE TABLE team_audit_events (
                audit_event_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                actor_id TEXT REFERENCES actors(actor_id),
                requester_actor_id TEXT REFERENCES actors(actor_id),
                action_kind TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                decision TEXT NOT NULL CHECK (decision IN ('allowed', 'denied', 'approved', 'revoked', 'failed')),
                reason TEXT NOT NULL DEFAULT '',
                correlation_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE notifications (
                notification_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                recipient_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                notification_kind TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                read_at TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                UNIQUE (recipient_actor_id, dedupe_key)
            )
            """,
            """
            CREATE TABLE legacy_id_mappings (
                source_system TEXT NOT NULL,
                legacy_kind TEXT NOT NULL,
                legacy_id TEXT NOT NULL,
                aggregate_kind TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (source_system, legacy_kind, legacy_id),
                UNIQUE (aggregate_kind, aggregate_id)
            )
            """,
            """
            CREATE VIRTUAL TABLE team_message_fts USING fts5(
                message_id UNINDEXED,
                workspace_id UNINDEXED,
                conversation_id UNINDEXED,
                body
            )
            """,
            """
            CREATE VIRTUAL TABLE team_task_fts USING fts5(
                task_id UNINDEXED,
                workspace_id UNINDEXED,
                title,
                description,
                acceptance_criteria
            )
            """,
            """
            CREATE VIRTUAL TABLE team_artifact_fts USING fts5(
                artifact_id UNINDEXED,
                workspace_id UNINDEXED,
                filename,
                media_type,
                metadata
            )
            """,
            "CREATE INDEX idx_approvals_workspace_status ON approvals (workspace_id, status, created_at DESC)",
            "CREATE INDEX idx_grants_effective ON grants (workspace_id, grantee_actor_id, agent_id, status, expires_at)",
            "CREATE INDEX idx_connector_bindings_lookup ON connector_bindings (workspace_id, connector_kind, external_workspace_id, status)",
            "CREATE INDEX idx_notifications_recipient ON notifications (recipient_actor_id, read_at, created_at DESC)",
        ),
    ),
    Migration(
        "0006_work_request_context_relations",
        (
            """
            CREATE TABLE work_request_context_refs (
                request_id TEXT NOT NULL REFERENCES work_requests(request_id),
                position INTEGER NOT NULL CHECK (position >= 0),
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                PRIMARY KEY (request_id, position),
                UNIQUE (request_id, resource_kind, resource_id)
            )
            """,
            """
            CREATE TABLE work_request_input_artifacts (
                request_id TEXT NOT NULL REFERENCES work_requests(request_id),
                position INTEGER NOT NULL CHECK (position >= 0),
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                PRIMARY KEY (request_id, position),
                UNIQUE (request_id, artifact_id)
            )
            """,
            "CREATE INDEX idx_work_request_context_refs_resource ON work_request_context_refs (resource_kind, resource_id)",
            "CREATE INDEX idx_work_request_input_artifacts_artifact ON work_request_input_artifacts (artifact_id, request_id)",
        ),
    ),
    Migration(
        "0007_work_coordinator_state",
        (
            "ALTER TABLE work_requests ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'canceled', 'completed'))",
            "ALTER TABLE work_requests ADD COLUMN canceled_at TEXT",
            "ALTER TABLE work_requests ADD COLUMN cancel_reason TEXT",
            """
            CREATE TABLE work_usage (
                request_id TEXT PRIMARY KEY REFERENCES work_requests(request_id),
                tokens INTEGER NOT NULL DEFAULT 0 CHECK (tokens >= 0),
                cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),
                model_calls INTEGER NOT NULL DEFAULT 0 CHECK (model_calls >= 0),
                tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
                wall_seconds INTEGER NOT NULL DEFAULT 0 CHECK (wall_seconds >= 0),
                wakes INTEGER NOT NULL DEFAULT 0 CHECK (wakes >= 0),
                fanout INTEGER NOT NULL DEFAULT 0 CHECK (fanout >= 0),
                handoffs INTEGER NOT NULL DEFAULT 0 CHECK (handoffs >= 0),
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_work_requests_status_priority ON work_requests (workspace_id, status, priority DESC, created_at, request_id)",
        ),
    ),
    Migration(
        "0008_allow_multiple_run_attempts",
        (
            "ALTER TABLE artifact_links RENAME TO artifact_links_before_run_rebuild",
            "ALTER TABLE artifact_revisions RENAME TO artifact_revisions_before_run_rebuild",
            "DROP INDEX idx_artifact_revisions_run",
            "ALTER TABLE memory_records RENAME TO memory_records_before_run_rebuild",
            "DROP INDEX idx_memory_scope",
            "ALTER TABLE approvals RENAME TO approvals_before_run_rebuild",
            "DROP INDEX idx_approvals_workspace_status",
            "ALTER TABLE run_events RENAME TO run_events_before_run_rebuild",
            "ALTER TABLE runs RENAME TO runs_before_run_rebuild",
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id),
                request_id TEXT NOT NULL REFERENCES work_requests(request_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                agent_revision_id TEXT NOT NULL REFERENCES agent_revisions(agent_revision_id),
                sandbox_id TEXT NOT NULL REFERENCES sandboxes(sandbox_id),
                status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'waiting_input', 'waiting_approval', 'succeeded', 'failed', 'timed_out', 'canceled')),
                budget_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """,
            """
            INSERT INTO runs
                (run_id, delivery_id, request_id, workspace_id, agent_id,
                 agent_revision_id, sandbox_id, status, budget_json, created_at,
                 started_at, finished_at)
            SELECT run_id, delivery_id, request_id, workspace_id, agent_id,
                   agent_revision_id, sandbox_id, status, budget_json, created_at,
                   started_at, finished_at
            FROM runs_before_run_rebuild
            """,
            """
            CREATE TABLE artifact_revisions_after_run_rebuild (
                artifact_revision_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                source_task_id TEXT REFERENCES tasks(task_id),
                source_ref_kind TEXT,
                source_ref_id TEXT,
                publisher_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                sha256 TEXT NOT NULL,
                storage_ref TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (artifact_id, revision_number),
                UNIQUE (artifact_id, idempotency_key)
            )
            """,
            """
            INSERT INTO artifact_revisions_after_run_rebuild
            SELECT * FROM artifact_revisions_before_run_rebuild
            """,
            """
            CREATE TABLE artifact_links_after_run_rebuild (
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                artifact_revision_id TEXT NOT NULL REFERENCES artifact_revisions_after_run_rebuild(artifact_revision_id),
                linked_aggregate_type TEXT NOT NULL,
                linked_aggregate_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (artifact_revision_id, linked_aggregate_type, linked_aggregate_id)
            )
            """,
            """
            INSERT INTO artifact_links_after_run_rebuild
            SELECT * FROM artifact_links_before_run_rebuild
            """,
            """
            CREATE TABLE memory_records_after_run_rebuild (
                memory_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent_private', 'direct_message', 'group', 'task', 'workspace', 'run_scratch')),
                scope_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_artifact_id TEXT REFERENCES artifacts(artifact_id),
                source_run_id TEXT REFERENCES runs(run_id),
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                expires_at TEXT,
                deleted_at TEXT
            )
            """,
            """
            INSERT INTO memory_records_after_run_rebuild
            SELECT * FROM memory_records_before_run_rebuild
            """,
            """
            CREATE TABLE approvals_after_run_rebuild (
                approval_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                request_id TEXT REFERENCES work_requests(request_id),
                run_id TEXT REFERENCES runs(run_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                approver_actor_id TEXT REFERENCES actors(actor_id),
                action_kind TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'canceled')),
                reason TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                decided_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO approvals_after_run_rebuild
            SELECT * FROM approvals_before_run_rebuild
            """,
            """
            CREATE TABLE run_events_after_run_rebuild (
                run_event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                event_type TEXT NOT NULL,
                actor_id TEXT REFERENCES actors(actor_id),
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO run_events_after_run_rebuild
                (run_event_id, run_id, event_type, actor_id, payload_json, occurred_at)
            SELECT run_event_id, run_id, event_type, actor_id, payload_json, occurred_at
            FROM run_events_before_run_rebuild
            """,
            "DROP TABLE artifact_links_before_run_rebuild",
            "DROP TABLE artifact_revisions_before_run_rebuild",
            "DROP TABLE memory_records_before_run_rebuild",
            "DROP TABLE approvals_before_run_rebuild",
            "DROP TABLE run_events_before_run_rebuild",
            "DROP TABLE runs_before_run_rebuild",
            "ALTER TABLE artifact_revisions_after_run_rebuild RENAME TO artifact_revisions",
            "ALTER TABLE artifact_links_after_run_rebuild RENAME TO artifact_links",
            "ALTER TABLE memory_records_after_run_rebuild RENAME TO memory_records",
            "ALTER TABLE approvals_after_run_rebuild RENAME TO approvals",
            "ALTER TABLE run_events_after_run_rebuild RENAME TO run_events",
            "CREATE INDEX idx_artifact_revisions_run ON artifact_revisions (source_run_id, created_at)",
            "CREATE INDEX idx_memory_scope ON memory_records (workspace_id, scope_kind, scope_id, created_at DESC)",
            "CREATE INDEX idx_approvals_workspace_status ON approvals (workspace_id, status, created_at DESC)",
            "CREATE INDEX idx_runs_delivery_created ON runs (delivery_id, created_at, run_id)",
        ),
    ),
    Migration(
        "0009_repair_run_foreign_keys",
        (
            "ALTER TABLE artifact_links RENAME TO artifact_links_before_fk_repair",
            "ALTER TABLE artifact_revisions RENAME TO artifact_revisions_before_fk_repair",
            "DROP INDEX idx_artifact_revisions_run",
            "ALTER TABLE memory_records RENAME TO memory_records_before_fk_repair",
            "DROP INDEX idx_memory_scope",
            "ALTER TABLE approvals RENAME TO approvals_before_fk_repair",
            "DROP INDEX idx_approvals_workspace_status",
            """
            CREATE TABLE artifact_revisions (
                artifact_revision_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                source_task_id TEXT REFERENCES tasks(task_id),
                source_ref_kind TEXT,
                source_ref_id TEXT,
                publisher_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                sha256 TEXT NOT NULL,
                storage_ref TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (artifact_id, revision_number),
                UNIQUE (artifact_id, idempotency_key)
            )
            """,
            """
            INSERT INTO artifact_revisions
            SELECT * FROM artifact_revisions_before_fk_repair
            """,
            """
            CREATE TABLE artifact_links (
                artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
                artifact_revision_id TEXT NOT NULL REFERENCES artifact_revisions(artifact_revision_id),
                linked_aggregate_type TEXT NOT NULL,
                linked_aggregate_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (artifact_revision_id, linked_aggregate_type, linked_aggregate_id)
            )
            """,
            """
            INSERT INTO artifact_links
            SELECT * FROM artifact_links_before_fk_repair
            """,
            "DROP TABLE artifact_links_before_fk_repair",
            "DROP TABLE artifact_revisions_before_fk_repair",
            """
            CREATE TABLE memory_records (
                memory_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent_private', 'direct_message', 'group', 'task', 'workspace', 'run_scratch')),
                scope_id TEXT NOT NULL,
                content TEXT NOT NULL,
                source_artifact_id TEXT REFERENCES artifacts(artifact_id),
                source_run_id TEXT REFERENCES runs(run_id),
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                created_at TEXT NOT NULL,
                expires_at TEXT,
                deleted_at TEXT
            )
            """,
            """
            INSERT INTO memory_records
            SELECT * FROM memory_records_before_fk_repair
            """,
            "DROP TABLE memory_records_before_fk_repair",
            """
            CREATE TABLE approvals (
                approval_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                request_id TEXT REFERENCES work_requests(request_id),
                run_id TEXT REFERENCES runs(run_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                approver_actor_id TEXT REFERENCES actors(actor_id),
                action_kind TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'expired', 'canceled')),
                reason TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                decided_at TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            INSERT INTO approvals
            SELECT * FROM approvals_before_fk_repair
            """,
            "DROP TABLE approvals_before_fk_repair",
            "CREATE INDEX idx_artifact_revisions_run ON artifact_revisions (source_run_id, created_at)",
            "CREATE INDEX idx_memory_scope ON memory_records (workspace_id, scope_kind, scope_id, created_at DESC)",
            "CREATE INDEX idx_approvals_workspace_status ON approvals (workspace_id, status, created_at DESC)",
        ),
    ),
    Migration(
        "0010_budget_pause_state",
        (
            "ALTER TABLE work_requests ADD COLUMN budget_paused_at TEXT",
            "ALTER TABLE work_requests ADD COLUMN budget_pause_reason TEXT",
            "CREATE INDEX idx_work_requests_dispatchable ON work_requests (workspace_id, status, budget_paused_at, priority DESC, created_at, request_id)",
        ),
    ),
    Migration(
        "0011_task_collaboration",
        (
            """
            CREATE TABLE task_watchers (
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                added_at TEXT NOT NULL,
                removed_at TEXT,
                PRIMARY KEY (task_id, actor_id)
            )
            """,
            """
            CREATE TABLE task_labels (
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (task_id, label)
            )
            """,
            """
            CREATE TABLE handoffs (
                handoff_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                sender_agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                recipient_agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                purpose TEXT NOT NULL,
                expected_output TEXT NOT NULL,
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                budget_json TEXT NOT NULL DEFAULT '{}',
                depth INTEGER NOT NULL CHECK (depth > 0),
                agent_path_json TEXT NOT NULL DEFAULT '[]',
                idempotency_key TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'completed', 'failed', 'canceled')),
                created_at TEXT NOT NULL,
                UNIQUE (workspace_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE crew_run_agents (
                crew_run_id TEXT NOT NULL REFERENCES crew_runs(crew_run_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                crew_role TEXT NOT NULL DEFAULT 'worker',
                position INTEGER NOT NULL CHECK (position >= 0),
                PRIMARY KEY (crew_run_id, agent_id),
                UNIQUE (crew_run_id, position)
            )
            """,
            "CREATE INDEX idx_task_watchers_actor ON task_watchers (actor_id, task_id)",
            "CREATE INDEX idx_task_labels_label ON task_labels (label, task_id)",
            "CREATE INDEX idx_handoffs_task_created ON handoffs (task_id, created_at, handoff_id)",
            "CREATE INDEX idx_crew_run_agents_agent ON crew_run_agents (agent_id, crew_run_id)",
        ),
    ),
    Migration(
        "0012_conversation_actions",
        (
            "ALTER TABLE conversations ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE conversations ADD COLUMN deleted_at TEXT",
            "UPDATE conversations SET updated_at = created_at WHERE updated_at = ''",
            "ALTER TABLE conversation_members ADD COLUMN muted_until TEXT",
            "ALTER TABLE messages ADD COLUMN edited_at TEXT",
            "ALTER TABLE messages ADD COLUMN deleted_by_actor_id TEXT REFERENCES actors(actor_id)",
            "ALTER TABLE messages ADD COLUMN quote_message_id TEXT REFERENCES messages(message_id)",
            """
            CREATE TABLE message_attachments (
                attachment_id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL REFERENCES messages(message_id),
                storage_ref TEXT NOT NULL,
                filename TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE (message_id, storage_ref)
            )
            """,
            """
            CREATE TABLE message_translations (
                message_id TEXT NOT NULL REFERENCES messages(message_id),
                language TEXT NOT NULL,
                body TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (message_id, language)
            )
            """,
            """
            CREATE TABLE thread_followers (
                thread_id TEXT NOT NULL REFERENCES threads(thread_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                followed_at TEXT NOT NULL,
                unfollowed_at TEXT,
                PRIMARY KEY (thread_id, actor_id)
            )
            """,
            """
            CREATE TABLE thread_read_cursors (
                thread_id TEXT NOT NULL REFERENCES threads(thread_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                last_read_message_id TEXT REFERENCES messages(message_id),
                last_read_at TEXT,
                PRIMARY KEY (thread_id, actor_id)
            )
            """,
            """
            CREATE TABLE message_drafts (
                draft_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                thread_id TEXT REFERENCES threads(thread_id),
                client_request_id TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                UNIQUE (conversation_id, actor_id, client_request_id)
            )
            """,
            "CREATE INDEX idx_message_attachments_message ON message_attachments (message_id, created_at)",
            "CREATE INDEX idx_thread_followers_actor ON thread_followers (actor_id, thread_id)",
            "CREATE INDEX idx_thread_read_cursors_actor ON thread_read_cursors (actor_id, thread_id)",
            "CREATE INDEX idx_message_drafts_actor ON message_drafts (actor_id, updated_at DESC)",
        ),
    ),
    Migration(
        "0012_team_artifact_workflows",
        (
            "ALTER TABLE artifacts ADD COLUMN quarantined_at TEXT",
            "ALTER TABLE artifacts ADD COLUMN quarantine_reason TEXT",
            """
            CREATE TABLE integration_queue (
                integration_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                run_id TEXT REFERENCES runs(run_id),
                artifact_id TEXT REFERENCES artifacts(artifact_id),
                source_agent_id TEXT REFERENCES agent_installations(agent_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                source_ref TEXT NOT NULL,
                target_ref TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'approved', 'rejected', 'applied', 'conflict', 'canceled')),
                conflict_detail TEXT,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                UNIQUE (workspace_id, idempotency_key)
            )
            """,
            "CREATE INDEX idx_integration_queue_task ON integration_queue (task_id, created_at)",
            "CREATE INDEX idx_integration_queue_workspace_status ON integration_queue (workspace_id, status, created_at)",
        ),
    ),
    Migration(
        "0013_connector_team_bridge",
        (
            """
            CREATE TABLE team_external_identities (
                external_identity_record_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                owner_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                connector_kind TEXT NOT NULL,
                external_workspace_id TEXT NOT NULL,
                external_identity_id TEXT NOT NULL,
                account_ref TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('active', 'reauth_required', 'revoked', 'error')),
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revoked_at TEXT,
                UNIQUE (workspace_id, connector_kind, external_workspace_id, external_identity_id)
            )
            """,
            """
            CREATE TABLE team_connector_grants (
                connector_grant_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                external_identity_record_id TEXT NOT NULL REFERENCES team_external_identities(external_identity_record_id),
                grantor_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                actions_json TEXT NOT NULL,
                resource_ids_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
                expires_at TEXT,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                UNIQUE (workspace_id, external_identity_record_id, requester_actor_id, agent_id)
            )
            """,
            """
            CREATE TABLE team_connector_bindings (
                connector_binding_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                external_identity_record_id TEXT NOT NULL REFERENCES team_external_identities(external_identity_record_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                connector_grant_id TEXT NOT NULL REFERENCES team_connector_grants(connector_grant_id),
                destination_conversation_id TEXT REFERENCES conversations(conversation_id),
                destination_thread_id TEXT REFERENCES threads(thread_id),
                status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'error')),
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                UNIQUE (workspace_id, external_identity_record_id, agent_id)
            )
            """,
            """
            CREATE TABLE team_connector_events (
                connector_event_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                external_identity_record_id TEXT NOT NULL REFERENCES team_external_identities(external_identity_record_id),
                external_event_id TEXT NOT NULL,
                external_thread_id TEXT NOT NULL DEFAULT '',
                external_resource_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                instruction TEXT NOT NULL,
                requester_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                target_agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                destination_conversation_id TEXT REFERENCES conversations(conversation_id),
                destination_thread_id TEXT REFERENCES threads(thread_id),
                payload_json TEXT NOT NULL DEFAULT '{}',
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('received', 'routed', 'failed', 'ignored', 'completed')),
                request_id TEXT REFERENCES work_requests(request_id),
                error_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (workspace_id, external_identity_record_id, external_event_id),
                UNIQUE (workspace_id, idempotency_key)
            )
            """,
            """
            CREATE TABLE team_connector_outbox (
                connector_delivery_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                connector_event_id TEXT NOT NULL REFERENCES team_connector_events(connector_event_id),
                external_identity_record_id TEXT NOT NULL REFERENCES team_external_identities(external_identity_record_id),
                run_id TEXT REFERENCES runs(run_id),
                external_resource_id TEXT NOT NULL,
                external_thread_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('queued', 'sending', 'sent', 'failed', 'canceled')),
                attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                UNIQUE (workspace_id, idempotency_key)
            )
            """,
            "CREATE INDEX idx_team_external_identities_workspace ON team_external_identities (workspace_id, connector_kind, status)",
            "CREATE INDEX idx_team_connector_grants_lookup ON team_connector_grants (workspace_id, external_identity_record_id, requester_actor_id, agent_id, status)",
            "CREATE INDEX idx_team_connector_bindings_agent ON team_connector_bindings (workspace_id, agent_id, status)",
            "CREATE INDEX idx_team_connector_events_workspace ON team_connector_events (workspace_id, status, created_at, connector_event_id)",
            "CREATE INDEX idx_team_connector_outbox_status ON team_connector_outbox (workspace_id, status, created_at, connector_delivery_id)",
        ),
    ),
    Migration(
        "0014_connector_health",
        (
            "ALTER TABLE team_external_identities ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown'",
            "ALTER TABLE team_external_identities ADD COLUMN last_health_error TEXT",
            "ALTER TABLE team_external_identities ADD COLUMN last_health_check_at TEXT",
            "ALTER TABLE team_external_identities ADD COLUMN reconnect_count INTEGER NOT NULL DEFAULT 0 CHECK (reconnect_count >= 0)",
        ),
    ),
    Migration(
        "0015_notification_preferences",
        (
            """
            CREATE TABLE notification_preferences (
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                muted INTEGER NOT NULL DEFAULT 0 CHECK (muted IN (0, 1)),
                mention_only INTEGER NOT NULL DEFAULT 0 CHECK (mention_only IN (0, 1)),
                quiet_hours_json TEXT NOT NULL DEFAULT '{}',
                event_preferences_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, actor_id)
            )
            """,
        ),
    ),
    Migration(
        "0016_conversation_preferences",
        (
            """
            CREATE TABLE conversation_preferences (
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
                actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (conversation_id, actor_id)
            )
            """,
            "CREATE INDEX idx_conversation_preferences_actor ON conversation_preferences (actor_id, pinned, updated_at DESC)",
        ),
    ),
    Migration(
        "0017_agent_lifecycle_deletions",
        (
            """
            CREATE TABLE agent_deletions (
                agent_id TEXT PRIMARY KEY REFERENCES agent_installations(agent_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                deleted_at TEXT NOT NULL,
                recovery_until TEXT
            )
            """,
            "CREATE INDEX idx_agent_deletions_workspace ON agent_deletions (workspace_id, deleted_at, agent_id)",
        ),
    ),
    Migration(
        "0018_agent_avatar_identity",
        ("ALTER TABLE agent_definitions ADD COLUMN avatar_id TEXT",),
    ),
    Migration(
        "0019_normalize_one_agent_groups",
        (
            """
            UPDATE conversations
            SET conversation_kind = 'direct',
                title = '',
                reply_mode = 'coordinator',
                coordinator_agent_id = (
                    SELECT i.agent_id
                    FROM conversation_members AS cm
                    JOIN actors AS a ON a.actor_id = cm.actor_id
                    JOIN agent_installations AS i
                      ON i.actor_id = cm.actor_id
                     AND i.workspace_id = conversations.workspace_id
                    WHERE cm.conversation_id = conversations.conversation_id
                      AND cm.left_at IS NULL
                      AND a.actor_kind = 'agent'
                ),
                allow_at_all = 0
            WHERE conversation_kind = 'group'
              AND conversation_id IN (
                  SELECT cm.conversation_id
                  FROM conversation_members AS cm
                  JOIN actors AS a ON a.actor_id = cm.actor_id
                  WHERE cm.left_at IS NULL
                  GROUP BY cm.conversation_id
                  HAVING COUNT(*) = 2
                     AND SUM(CASE WHEN a.actor_kind = 'agent' THEN 1 ELSE 0 END) = 1
                     AND SUM(CASE WHEN a.actor_kind = 'human' THEN 1 ELSE 0 END) = 1
              )
              AND EXISTS (
                  SELECT 1
                  FROM conversation_members AS cm
                  JOIN actors AS a ON a.actor_id = cm.actor_id
                  JOIN agent_installations AS i
                    ON i.actor_id = cm.actor_id
                   AND i.workspace_id = conversations.workspace_id
                  WHERE cm.conversation_id = conversations.conversation_id
                    AND cm.left_at IS NULL
                    AND a.actor_kind = 'agent'
              )
            """,
        ),
    ),
    Migration(
        "0020_computer_plane",
        (
            """
            CREATE TABLE computers (
                computer_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                agent_id TEXT NOT NULL UNIQUE REFERENCES agent_installations(agent_id),
                provider_kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('provisioning', 'ready', 'suspended', 'offline', 'error', 'destroyed')),
                capabilities_json TEXT NOT NULL DEFAULT '{}',
                provider_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                destroyed_at TEXT
            )
            """,
            """
            CREATE TABLE computer_sessions (
                session_id TEXT PRIMARY KEY,
                computer_id TEXT NOT NULL REFERENCES computers(computer_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                agent_id TEXT NOT NULL REFERENCES agent_installations(agent_id),
                run_id TEXT REFERENCES runs(run_id),
                owner_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                status TEXT NOT NULL CHECK (status IN ('active', 'waiting_human', 'suspended', 'closed', 'lost')),
                provider_session_ref TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT
            )
            """,
            """
            CREATE TABLE screen_leases (
                screen_lease_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES computer_sessions(session_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                holder_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                status TEXT NOT NULL CHECK (status IN ('active', 'released', 'expired')),
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                released_at TEXT
            )
            """,
            """
            CREATE TABLE takeover_requests (
                takeover_request_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES computer_sessions(session_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                run_id TEXT REFERENCES runs(run_id),
                requested_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                reason TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'completed', 'canceled')),
                created_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by_actor_id TEXT REFERENCES actors(actor_id)
            )
            """,
            "CREATE UNIQUE INDEX idx_screen_leases_one_active ON screen_leases (session_id) WHERE status = 'active'",
            "CREATE INDEX idx_computers_workspace_status ON computers (workspace_id, status, updated_at DESC)",
            "CREATE INDEX idx_computer_sessions_run ON computer_sessions (run_id, status, updated_at DESC)",
            "CREATE INDEX idx_takeover_requests_session_status ON takeover_requests (session_id, status, created_at DESC)",
        ),
    ),
    Migration(
        "0021_verification_attempts",
        (
            """
            CREATE TABLE verification_attempts (
                verification_attempt_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                task_id TEXT REFERENCES tasks(task_id),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                verifier_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                contract_json TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
                evidence_json TEXT NOT NULL DEFAULT '[]',
                failure_reason TEXT,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_verification_attempts_run ON verification_attempts (run_id, created_at DESC)",
            "CREATE INDEX idx_verification_attempts_task ON verification_attempts (task_id, created_at DESC)",
        ),
    ),
    Migration(
        "0022_memory_candidates",
        (
            """
            CREATE TABLE memory_candidates (
                candidate_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent_private', 'direct_message', 'group', 'task', 'workspace', 'run_scratch')),
                scope_id TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                conflict_key TEXT,
                importance INTEGER NOT NULL DEFAULT 0 CHECK (importance BETWEEN 0 AND 100),
                retention_seconds INTEGER CHECK (retention_seconds IS NULL OR retention_seconds > 0),
                source_run_id TEXT REFERENCES runs(run_id),
                proposed_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected')),
                memory_id TEXT REFERENCES memory_records(memory_id),
                created_at TEXT NOT NULL,
                decided_at TEXT
            )
            """,
            "CREATE UNIQUE INDEX idx_memory_candidates_dedupe ON memory_candidates (workspace_id, scope_kind, scope_id, content_hash)",
            "CREATE INDEX idx_memory_candidates_scope_status ON memory_candidates (workspace_id, scope_kind, scope_id, status, created_at DESC)",
        ),
    ),
    Migration(
        "0023_team_routines",
        (
            """
            CREATE TABLE team_routines (
                routine_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                created_by_actor_id TEXT NOT NULL REFERENCES actors(actor_id),
                name TEXT NOT NULL,
                trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('schedule', 'connector', 'manual')),
                schedule_json TEXT NOT NULL DEFAULT '{}',
                timezone TEXT NOT NULL DEFAULT 'UTC',
                target_agent_ids_json TEXT NOT NULL,
                conversation_id TEXT REFERENCES conversations(conversation_id),
                task_id TEXT REFERENCES tasks(task_id),
                instruction TEXT NOT NULL,
                expected_output TEXT NOT NULL DEFAULT '',
                budget_json TEXT NOT NULL DEFAULT '{}',
                approval_policy_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'archived')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (workspace_id, name)
            )
            """,
            """
            CREATE TABLE routine_occurrences (
                occurrence_id TEXT PRIMARY KEY,
                routine_id TEXT NOT NULL REFERENCES team_routines(routine_id),
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                occurrence_key TEXT NOT NULL,
                trigger_kind TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('claimed', 'queued', 'failed', 'completed')),
                work_request_id TEXT REFERENCES work_requests(request_id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (routine_id, occurrence_key)
            )
            """,
            "CREATE INDEX idx_team_routines_workspace_status ON team_routines (workspace_id, status, updated_at DESC)",
            "CREATE INDEX idx_routine_occurrences_routine ON routine_occurrences (routine_id, created_at DESC)",
        ),
    ),
    Migration(
        "0024_message_reasoning_metadata",
        (
            "ALTER TABLE messages ADD COLUMN reasoning TEXT",
            "ALTER TABLE messages ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        ),
    ),
    Migration(
        "0025_purge_legacy_reasoning_events",
        (
            "DELETE FROM run_events WHERE event_type = 'agent.reasoning.delta'",
        ),
    ),
)


class TeamDatabase:
    """One Team SQLite database with atomic, checksummed, recoverable migrations."""

    def __init__(
        self,
        path: str | Path,
        *,
        migrations: Sequence[Migration] = DEFAULT_MIGRATIONS,
        backup_dir: str | Path | None = None,
        migration_hook: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.path = ":memory:" if str(path) == ":memory:" else str(Path(path).expanduser())
        self._is_memory = self.path == ":memory:"
        if not self._is_memory:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if not self._is_memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self.backup_dir = (
            Path(backup_dir).expanduser()
            if backup_dir is not None
            else Path(self.path).expanduser().parent / "team-backups"
        )
        self._migrations = tuple(migrations)
        self._migration_hook = migration_hook
        self._validate_migrations()
        self._bootstrap_migration_journal()
        try:
            self.migrate()
        except Exception:
            self.close()
            raise

    def _validate_migrations(self) -> None:
        ids = [migration.migration_id for migration in self._migrations]
        if not ids or len(ids) != len(set(ids)) or ids != sorted(ids):
            raise MigrationError("migration ids must be unique and sorted")
        for migration in self._migrations:
            if not migration.statements or any(not statement.strip() for statement in migration.statements):
                raise MigrationError(f"migration {migration.migration_id} has an empty statement")

    def _bootstrap_migration_journal(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS team_schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def migrate(self) -> list[str]:
        """Apply each missing migration atomically, with a pre-migration backup."""

        applied: list[str] = []
        with self._lock:
            existing = {
                row["migration_id"]: row["checksum"]
                for row in self._conn.execute(
                    "SELECT migration_id, checksum FROM team_schema_migrations"
                ).fetchall()
            }
            for migration in self._migrations:
                recorded = existing.get(migration.migration_id)
                if recorded is not None:
                    if recorded != migration.checksum:
                        raise MigrationError(
                            f"migration {migration.migration_id} checksum changed after application"
                        )
                    continue
                try:
                    self._migration_hook_call(migration.migration_id, "before")
                    self.create_backup(reason=f"before-{migration.migration_id}")
                    self._conn.execute("BEGIN IMMEDIATE")
                    for index, statement in enumerate(migration.statements):
                        self._migration_hook_call(migration.migration_id, f"statement:{index}")
                        self._conn.execute(statement)
                    self._migration_hook_call(migration.migration_id, "before_commit")
                    self._conn.execute(
                        "INSERT INTO team_schema_migrations (migration_id, checksum, applied_at) VALUES (?, ?, ?)",
                        (migration.migration_id, migration.checksum, _utc_now()),
                    )
                    self._conn.execute("COMMIT")
                    self._migration_hook_call(migration.migration_id, "after_commit")
                except Exception as error:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise MigrationError(
                        f"migration {migration.migration_id} failed; previous state is recoverable"
                    ) from error
                applied.append(migration.migration_id)
        return applied

    def _migration_hook_call(self, migration_id: str, phase: str) -> None:
        if self._migration_hook is not None:
            self._migration_hook(migration_id, phase)

    def create_backup(self, *, reason: str = "manual") -> Optional[Path]:
        """Create an atomic SQLite backup, or no-op for an in-memory database."""

        if self._is_memory:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backup_dir / f"team-{reason}-{timestamp}-{uuid.uuid4().hex[:8]}.sqlite"
        temporary = target.with_suffix(".tmp")
        with self._lock:
            destination = sqlite3.connect(temporary)
            try:
                self._conn.backup(destination)
            finally:
                destination.close()
        os.replace(temporary, target)
        return target

    @property
    def schema_version(self) -> str:
        row = self._conn.execute(
            "SELECT migration_id FROM team_schema_migrations ORDER BY migration_id DESC LIMIT 1"
        ).fetchone()
        return str(row["migration_id"]) if row else ""

    def applied_migrations(self) -> list[dict[str, str]]:
        rows = self._conn.execute(
            "SELECT migration_id, checksum, applied_at FROM team_schema_migrations ORDER BY migration_id"
        ).fetchall()
        return [dict(row) for row in rows]

    def table_counts(self) -> dict[str, int]:
        tables = (
            "workspaces",
            "actors",
            "agent_installations",
            "conversations",
            "messages",
            "tasks",
            "work_requests",
            "deliveries",
            "runs",
            "artifacts",
            "approvals",
            "domain_events",
        )
        return {
            table: int(self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def verify_integrity(self) -> dict[str, Any]:
        foreign_key_errors = [tuple(row) for row in self._conn.execute("PRAGMA foreign_key_check")]
        return {
            "schema_version": self.schema_version,
            "foreign_key_errors": foreign_key_errors,
            "counts": self.table_counts(),
        }

    def parity_report(self, legacy_base: str | Path) -> "ParityReport":
        """Compare read-only legacy stores with canonical counts and integrity state."""

        return build_parity_report(self, legacy_base)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class IdentityRepository:
    """Normalized identity writes; only this repository writes identity tables."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_workspace(self, workspace_id: str, display_name: str) -> dict[str, str]:
        _require_text(workspace_id, "workspace id")
        display_name = _require_text(display_name, "workspace display name")
        created_at = _utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO workspaces (workspace_id, display_name, created_at) VALUES (?, ?, ?)",
                    (workspace_id, display_name, created_at),
                )
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError(f"workspace already exists: {workspace_id}") from error
        return {
            "workspace_id": workspace_id,
            "display_name": display_name,
            "created_at": created_at,
        }

    def create_actor(self, actor: ActorRef, *, handle: Optional[str] = None) -> dict[str, str]:
        if not isinstance(actor, ActorRef):
            raise ContractError("actor must be an ActorRef")
        if handle is not None:
            handle = _require_text(handle, "actor handle")
        created_at = _utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO actors (actor_id, actor_kind, display_name, handle, created_at) VALUES (?, ?, ?, ?, ?)",
                    (actor.id, actor.kind.value, actor.display_name, handle, created_at),
                )
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError(f"actor already exists: {actor.id}") from error
        return {
            "actor_id": actor.id,
            "actor_kind": actor.kind.value,
            "display_name": actor.display_name,
            "handle": handle or "",
            "created_at": created_at,
        }

    def add_membership(
        self,
        membership_id: str,
        workspace_id: str,
        actor_id: str,
        workspace_role: str,
    ) -> dict[str, str]:
        for value, label in (
            (membership_id, "membership id"),
            (workspace_id, "workspace id"),
            (actor_id, "actor id"),
            (workspace_role, "workspace role"),
        ):
            _require_text(value, label)
        created_at = _utc_now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO workspace_memberships
                        (membership_id, workspace_id, actor_id, workspace_role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (membership_id, workspace_id, actor_id, workspace_role, created_at),
                )
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("membership conflicts with an existing record") from error
        return {
            "membership_id": membership_id,
            "workspace_id": workspace_id,
            "actor_id": actor_id,
            "workspace_role": workspace_role,
            "created_at": created_at,
        }

    def update_actor(
        self,
        actor_id: str,
        *,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
    ) -> dict[str, str]:
        actor_id = _required_id(actor_id, "actor id")
        if display_name is None and handle is None:
            raise ContractError("actor update requires a field")
        if display_name is not None:
            display_name = _require_text(display_name, "actor display name")
        if handle is not None:
            handle = _require_text(handle, "actor handle")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()
            if row is None:
                raise ContractError(f"actor not found: {actor_id}")
            changes: dict[str, Any] = {}
            if display_name is not None:
                changes["display_name"] = display_name
            if handle is not None:
                changes["handle"] = handle
            connection.execute(
                "UPDATE actors SET " + ", ".join(f"{key} = ?" for key in changes) + " WHERE actor_id = ?",
                (*changes.values(), actor_id),
            )
            updated = connection.execute("SELECT * FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()
        assert updated is not None
        return {
            "actor_id": updated["actor_id"],
            "actor_kind": updated["actor_kind"],
            "display_name": updated["display_name"],
            "handle": updated["handle"] or "",
            "created_at": updated["created_at"],
        }


class AgentRepository:
    """Durable agent definitions, revisions, capabilities, and installations."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_definition(
        self,
        definition_id: str,
        workspace_id: str,
        owner_actor_id: str,
        display_name: str,
        handle: str,
        base_role: AgentRole | str,
        *,
        description: str = "",
        avatar_id: Optional[str] = None,
    ) -> dict[str, Any]:
        values = {
            "definition_id": _required_id(definition_id, "agent definition id"),
            "workspace_id": _required_id(workspace_id, "workspace id"),
            "owner_actor_id": _required_id(owner_actor_id, "owner actor id"),
            "display_name": _require_text(display_name, "agent display name"),
            "handle": _require_handle(handle, "agent handle"),
            "base_role": _as_enum(AgentRole, base_role, "agent base role").value,
            "description": str(description or "").strip(),
            "avatar_id": normalize_agent_avatar_id(avatar_id),
            "created_at": _utc_now(),
        }
        try:
            with self.database.transaction() as connection:
                _require_workspace_actor(connection, values["workspace_id"], values["owner_actor_id"])
                connection.execute(
                    """
                    INSERT INTO agent_definitions
                        (agent_definition_id, workspace_id, owner_actor_id, display_name,
                         handle, description, avatar_id, base_role, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values["definition_id"],
                        values["workspace_id"],
                        values["owner_actor_id"],
                        values["display_name"],
                        values["handle"],
                        values["description"],
                        values["avatar_id"],
                        values["base_role"],
                        values["created_at"],
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM agent_definitions WHERE agent_definition_id = ?",
                    (values["definition_id"],),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("agent definition conflicts with existing identity") from error
        assert row is not None
        return dict(row)

    def create_revision(
        self,
        revision_id: str,
        definition_id: str,
        revision_number: int,
        instructions: str,
        created_by_actor_id: str,
        *,
        model_policy: Mapping[str, Any] | None = None,
        sandbox_policy: Mapping[str, Any] | None = None,
        memory_policy: Mapping[str, Any] | None = None,
        reply_policy: Mapping[str, Any] | None = None,
        budget_policy: Mapping[str, Any] | None = None,
        approval_policy: Mapping[str, Any] | None = None,
        capabilities: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        revision_id = _required_id(revision_id, "agent revision id")
        definition_id = _required_id(definition_id, "agent definition id")
        created_by_actor_id = _required_id(created_by_actor_id, "created by actor id")
        instructions = _require_text(instructions, "agent instructions")
        _require_positive_int(revision_number, "revision number")
        policies = {
            "model_policy_json": _json_object(model_policy, "model policy"),
            "sandbox_policy_json": _json_object(sandbox_policy, "sandbox policy"),
            "memory_policy_json": _json_object(memory_policy, "memory policy"),
            "reply_policy_json": _json_object(reply_policy, "reply policy"),
            "budget_policy_json": _json_object(budget_policy, "budget policy"),
            "approval_policy_json": _json_object(approval_policy, "approval policy"),
        }
        normalized_capabilities: list[tuple[str, str, int, str]] = []
        for capability in capabilities or ():
            if not isinstance(capability, Mapping):
                raise ContractError("agent capability must be an object")
            kind = _required_kind(capability.get("capability_kind"), "capability kind")
            ref = _require_text(capability.get("capability_ref"), "capability reference")
            required = capability.get("required", False)
            if not isinstance(required, bool):
                raise ContractError("capability required must be boolean")
            normalized_capabilities.append(
                (
                    kind,
                    ref,
                    int(required),
                    _json_object(capability.get("config"), "capability config"),
                )
            )
        created_at = _utc_now()
        try:
            with self.database.transaction() as connection:
                definition = connection.execute(
                    "SELECT workspace_id FROM agent_definitions WHERE agent_definition_id = ?",
                    (definition_id,),
                ).fetchone()
                if definition is None:
                    raise ContractError("agent definition does not exist")
                _require_workspace_actor(connection, definition["workspace_id"], created_by_actor_id)
                connection.execute(
                    """
                    INSERT INTO agent_revisions
                        (agent_revision_id, agent_definition_id, revision_number,
                         instructions, model_policy_json, sandbox_policy_json,
                         memory_policy_json, reply_policy_json, budget_policy_json,
                         approval_policy_json, created_by_actor_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        definition_id,
                        revision_number,
                        instructions,
                        policies["model_policy_json"],
                        policies["sandbox_policy_json"],
                        policies["memory_policy_json"],
                        policies["reply_policy_json"],
                        policies["budget_policy_json"],
                        policies["approval_policy_json"],
                        created_by_actor_id,
                        created_at,
                    ),
                )
                for kind, ref, required, config in normalized_capabilities:
                    connection.execute(
                        """
                        INSERT INTO agent_revision_capabilities
                            (agent_revision_id, capability_kind, capability_ref, required, config_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (revision_id, kind, ref, required, config),
                    )
                connection.execute(
                    """
                    UPDATE agent_definitions
                    SET current_revision_id = ?
                    WHERE agent_definition_id = ?
                    """,
                    (revision_id, definition_id),
                )
                row = connection.execute(
                    "SELECT * FROM agent_revisions WHERE agent_revision_id = ?",
                    (revision_id,),
                ).fetchone()
                capability_rows = connection.execute(
                    "SELECT * FROM agent_revision_capabilities WHERE agent_revision_id = ? ORDER BY capability_kind, capability_ref",
                    (revision_id,),
                ).fetchall()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("agent revision conflicts with existing definition") from error
        assert row is not None
        return {
            **dict(row),
            "capabilities": [dict(capability) for capability in capability_rows],
        }

    def install(
        self,
        agent_id: str,
        workspace_id: str,
        actor_id: str,
        definition_id: str,
        revision_id: str,
        *,
        status: AgentStatus | str = AgentStatus.PROVISIONING,
        max_concurrency: int = 1,
    ) -> dict[str, Any]:
        values = (
            _required_id(agent_id, "agent id"),
            _required_id(workspace_id, "workspace id"),
            _required_id(actor_id, "agent actor id"),
            _required_id(definition_id, "agent definition id"),
            _required_id(revision_id, "agent revision id"),
            _as_enum(AgentStatus, status, "agent status").value,
        )
        _require_positive_int(max_concurrency, "max concurrency")
        try:
            with self.database.transaction() as connection:
                identity = connection.execute(
                    """
                    SELECT a.actor_kind, m.status, m.workspace_id
                    FROM actors AS a
                    LEFT JOIN workspace_memberships AS m
                      ON m.actor_id = a.actor_id AND m.workspace_id = ?
                    WHERE a.actor_id = ?
                    """,
                    (values[1], values[2]),
                ).fetchone()
                if identity is None or identity["actor_kind"] != ActorKind.AGENT.value:
                    raise ContractError("agent installation requires an agent actor")
                if identity["status"] != "active":
                    raise ContractError("agent actor is not an active workspace member")
                definition = connection.execute(
                    "SELECT workspace_id FROM agent_definitions WHERE agent_definition_id = ?",
                    (values[3],),
                ).fetchone()
                revision = connection.execute(
                    """
                    SELECT r.agent_definition_id, d.workspace_id
                    FROM agent_revisions AS r JOIN agent_definitions AS d
                      ON d.agent_definition_id = r.agent_definition_id
                    WHERE r.agent_revision_id = ?
                    """,
                    (values[4],),
                ).fetchone()
                if definition is None or revision is None or definition["workspace_id"] != values[1]:
                    raise ContractError("agent definition is missing or belongs to another workspace")
                if revision["agent_definition_id"] != values[3] or revision["workspace_id"] != values[1]:
                    raise ContractError("agent revision does not belong to the installed definition")
                connection.execute(
                    """
                    INSERT INTO agent_installations
                        (agent_id, workspace_id, actor_id, agent_definition_id,
                         active_revision_id, status, max_concurrency, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, max_concurrency, _utc_now()),
                )
                installed = connection.execute(
                    "SELECT * FROM agent_installations WHERE agent_id = ?",
                    (values[0],),
                ).fetchone()
        except ContractError:
            raise
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("agent installation conflicts with existing identity") from error
        assert installed is not None
        return dict(installed)

    def set_status(self, agent_id: str, target: AgentStatus | str) -> dict[str, Any]:
        agent_id = _required_id(agent_id, "agent id")
        target = _as_enum(AgentStatus, target, "agent status")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM agent_installations WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"agent not found: {agent_id}")
            current = AgentStatus(row["status"])
            require_transition(current, target, AGENT_EDGES)
            connection.execute(
                """
                UPDATE agent_installations
                SET status = ?,
                    archived_at = CASE
                        WHEN ? = 'archived' THEN COALESCE(archived_at, ?)
                        ELSE NULL
                    END
                WHERE agent_id = ?
                """,
                (target.value, target.value, _utc_now(), agent_id),
            )
            updated = connection.execute(
                "SELECT * FROM agent_installations WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def get_installation(self, agent_id: str) -> Optional[dict[str, Any]]:
        agent_id = _required_id(agent_id, "agent id")
        with self.database._lock:
            row = self.database._conn.execute(
                """
                SELECT i.*, d.deleted_at AS deleted_at, d.recovery_until AS recovery_until
                FROM agent_installations AS i
                LEFT JOIN agent_deletions AS d ON d.agent_id = i.agent_id
                WHERE i.agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def is_deleted(self, agent_id: str) -> bool:
        agent_id = _required_id(agent_id, "agent id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT 1 FROM agent_deletions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return row is not None

    def mark_deleted(
        self,
        agent_id: str,
        *,
        requested_by_actor_id: str,
        recovery_until: Optional[str] = None,
    ) -> dict[str, Any]:
        """Tombstone an archived installation while retaining audit foreign keys."""

        agent_id = _required_id(agent_id, "agent id")
        requested_by_actor_id = _required_id(requested_by_actor_id, "deletion requester actor id")
        deleted_at = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT i.workspace_id, i.status, d.owner_actor_id
                FROM agent_installations AS i
                JOIN agent_definitions AS d ON d.agent_definition_id = i.agent_definition_id
                WHERE i.agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"agent installation not found: {agent_id}")
            if row["owner_actor_id"] != requested_by_actor_id:
                raise ContractError("only the agent owner can delete it")
            if row["status"] != AgentStatus.ARCHIVED.value:
                raise ContractError("agent must be archived before deletion")
            existing = connection.execute(
                "SELECT * FROM agent_deletions WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO agent_deletions
                        (agent_id, workspace_id, requested_by_actor_id, deleted_at, recovery_until)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (agent_id, row["workspace_id"], requested_by_actor_id, deleted_at, recovery_until),
                )
                existing = connection.execute(
                    "SELECT * FROM agent_deletions WHERE agent_id = ?", (agent_id,)
                ).fetchone()
        assert existing is not None
        return {**dict(existing), "status": "deleted"}

    def get_definition(self, definition_id: str) -> Optional[dict[str, Any]]:
        definition_id = _required_id(definition_id, "agent definition id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM agent_definitions WHERE agent_definition_id = ?",
                (definition_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_definition(
        self,
        definition_id: str,
        *,
        actor_id: str,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
        description: Optional[str] = None,
        avatar_id: Optional[str] = None,
    ) -> dict[str, Any]:
        definition_id = _required_id(definition_id, "agent definition id")
        actor_id = _required_id(actor_id, "agent definition editor actor id")
        if display_name is None and handle is None and description is None and avatar_id is None:
            raise ContractError("agent definition update requires a field")
        if display_name is not None:
            display_name = _require_text(display_name, "agent display name")
        if handle is not None:
            handle = _require_handle(handle, "agent handle")
        if description is not None:
            description = str(description).strip()
        if avatar_id is not None:
            avatar_id = normalize_agent_avatar_id(avatar_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM agent_definitions WHERE agent_definition_id = ?", (definition_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"agent definition not found: {definition_id}")
            if row["owner_actor_id"] != actor_id:
                raise ContractError("only the agent owner can edit its identity")
            changes: dict[str, Any] = {}
            if display_name is not None:
                changes["display_name"] = display_name
            if handle is not None:
                changes["handle"] = handle
            if description is not None:
                changes["description"] = description
            if avatar_id is not None:
                changes["avatar_id"] = avatar_id
            try:
                connection.execute(
                    "UPDATE agent_definitions SET "
                    + ", ".join(f"{key} = ?" for key in changes)
                    + " WHERE agent_definition_id = ?",
                    (*changes.values(), definition_id),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("agent handle already exists in this workspace") from error
            updated = connection.execute(
                "SELECT * FROM agent_definitions WHERE agent_definition_id = ?", (definition_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def update_identity(
        self,
        agent_id: str,
        *,
        owner_actor_id: str,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
        description: Optional[str] = None,
        avatar_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Atomically rename an installed agent's actor and definition."""
        agent_id = _required_id(agent_id, "agent id")
        owner_actor_id = _required_id(owner_actor_id, "agent identity editor actor id")
        if display_name is None and handle is None and description is None and avatar_id is None:
            raise ContractError("agent identity update requires a field")
        if display_name is not None:
            display_name = _require_text(display_name, "agent display name")
        if handle is not None:
            handle = _require_handle(handle, "agent handle")
        if description is not None:
            description = str(description).strip()
        if avatar_id is not None:
            avatar_id = normalize_agent_avatar_id(avatar_id)
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT i.actor_id, i.agent_definition_id, d.owner_actor_id
                FROM agent_installations AS i
                JOIN agent_definitions AS d ON d.agent_definition_id = i.agent_definition_id
                WHERE i.agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"agent installation not found: {agent_id}")
            if row["owner_actor_id"] != owner_actor_id:
                raise ContractError("only the agent owner can edit its identity")
            definition_changes: dict[str, Any] = {}
            if display_name is not None:
                definition_changes["display_name"] = display_name
            if handle is not None:
                definition_changes["handle"] = handle
            if description is not None:
                definition_changes["description"] = description
            if avatar_id is not None:
                definition_changes["avatar_id"] = avatar_id
            try:
                if display_name is not None or handle is not None:
                    actor_changes = {}
                    if display_name is not None:
                        actor_changes["display_name"] = display_name
                    if handle is not None:
                        actor_changes["handle"] = handle
                    connection.execute(
                        "UPDATE actors SET "
                        + ", ".join(f"{key} = ?" for key in actor_changes)
                        + " WHERE actor_id = ?",
                        (*actor_changes.values(), row["actor_id"]),
                    )
                connection.execute(
                    "UPDATE agent_definitions SET "
                    + ", ".join(f"{key} = ?" for key in definition_changes)
                    + " WHERE agent_definition_id = ?",
                    (*definition_changes.values(), row["agent_definition_id"]),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("agent handle already exists in this workspace") from error
            updated = connection.execute(
                "SELECT * FROM agent_definitions WHERE agent_definition_id = ?",
                (row["agent_definition_id"],),
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def get_revision(self, revision_id: str) -> Optional[dict[str, Any]]:
        revision_id = _required_id(revision_id, "agent revision id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM agent_revisions WHERE agent_revision_id = ?",
                (revision_id,),
            ).fetchone()
            if row is None:
                return None
            capabilities = self.database._conn.execute(
                "SELECT * FROM agent_revision_capabilities WHERE agent_revision_id = ? ORDER BY capability_kind, capability_ref",
                (revision_id,),
            ).fetchall()
        result = dict(row)
        for key in (
            "model_policy_json",
            "sandbox_policy_json",
            "memory_policy_json",
            "reply_policy_json",
            "budget_policy_json",
            "approval_policy_json",
        ):
            result[key.removesuffix("_json")] = _json_load(result.pop(key), {})
        result["capabilities"] = [
            {
                **dict(capability),
                "config": _json_load(capability["config_json"], {}),
            }
            for capability in capabilities
        ]
        return result

    def activate_revision(self, agent_id: str, revision_id: str) -> dict[str, Any]:
        agent_id = _required_id(agent_id, "agent id")
        revision_id = _required_id(revision_id, "agent revision id")
        with self.database.transaction() as connection:
            installation = connection.execute(
                "SELECT workspace_id, agent_definition_id FROM agent_installations WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            revision = connection.execute(
                """
                SELECT r.agent_definition_id, d.workspace_id
                FROM agent_revisions AS r JOIN agent_definitions AS d
                  ON d.agent_definition_id = r.agent_definition_id
                WHERE r.agent_revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
            if installation is None or revision is None:
                raise ContractError("agent installation or revision does not exist")
            if (
                installation["agent_definition_id"] != revision["agent_definition_id"]
                or installation["workspace_id"] != revision["workspace_id"]
            ):
                raise ContractError("agent revision does not belong to this installation")
            connection.execute(
                "UPDATE agent_installations SET active_revision_id = ? WHERE agent_id = ?",
                (revision_id, agent_id),
            )
            row = connection.execute(
                "SELECT * FROM agent_installations WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        assert row is not None
        return dict(row)


class ConversationRepository:
    """Normalized conversation, membership, thread, and message commands/queries."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_conversation(
        self,
        conversation_id: str,
        workspace_id: str,
        kind: ConversationKind | str,
        created_by_actor_id: str,
        *,
        title: str = "",
        reply_mode: str = "mention_only",
        coordinator_agent_id: Optional[str] = None,
        allow_at_all: bool = False,
        max_fanout: int = 8,
    ) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        workspace_id = _required_id(workspace_id, "workspace id")
        created_by_actor_id = _required_id(created_by_actor_id, "created by actor id")
        kind = _as_enum(ConversationKind, kind, "conversation kind")
        reply_mode = _required_kind(reply_mode, "reply mode")
        if reply_mode not in {"mention_only", "coordinator", "all_messages"}:
            raise ContractError("reply mode is not supported")
        if kind is ConversationKind.DIRECT and reply_mode != ReplyMode.COORDINATOR.value:
            raise ContractError("direct conversations use coordinator routing semantics")
        if not isinstance(allow_at_all, bool):
            raise ContractError("allow_at_all must be boolean")
        _require_positive_int(max_fanout, "max fanout")
        coordinator_agent_id = _optional_id(coordinator_agent_id, "coordinator agent id")
        created_at = _utc_now()
        try:
            with self.database.transaction() as connection:
                _require_workspace_actor(
                    connection,
                    workspace_id,
                    created_by_actor_id,
                    allow_system=True,
                )
                if coordinator_agent_id is not None:
                    _require_agent_workspace(connection, workspace_id, coordinator_agent_id)
                connection.execute(
                    """
                    INSERT INTO conversations
                        (conversation_id, workspace_id, conversation_kind, title, reply_mode,
                         coordinator_agent_id, allow_at_all, max_fanout, created_by_actor_id,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        workspace_id,
                        kind.value,
                        str(title or "").strip(),
                        reply_mode,
                        coordinator_agent_id,
                        int(allow_at_all),
                        max_fanout,
                        created_by_actor_id,
                        created_at,
                        created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM conversations WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("conversation conflicts with existing identity") from error
        assert row is not None
        return _conversation_row(row)

    def get_conversation(self, conversation_id: str) -> Optional[dict[str, Any]]:
        """Return a conversation together with its active members.

        Routing is derived from this snapshot by the coordinator.  Keeping the
        policy read next to the membership rows prevents a caller from routing
        against a stale or caller-invented member list.
        """

        conversation_id = _required_id(conversation_id, "conversation id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            members = self.database._conn.execute(
                """
                SELECT m.*, a.actor_kind, a.display_name
                FROM conversation_members AS m
                JOIN actors AS a ON a.actor_id = m.actor_id
                WHERE m.conversation_id = ? AND m.left_at IS NULL
                ORDER BY m.joined_at, m.actor_id
                """,
                (conversation_id,),
            ).fetchall()
            agent_members = self.database._conn.execute(
                """
                SELECT i.agent_id, m.actor_id, i.status
                FROM conversation_members AS m
                JOIN actors AS a ON a.actor_id = m.actor_id
                JOIN agent_installations AS i
                  ON i.actor_id = m.actor_id
                 AND i.workspace_id = (SELECT workspace_id FROM conversations WHERE conversation_id = ?)
                WHERE m.conversation_id = ?
                  AND m.left_at IS NULL
                  AND a.actor_kind = 'agent'
                ORDER BY m.joined_at, m.actor_id
                """,
                (conversation_id, conversation_id),
            ).fetchall()
        result = _conversation_row(row)
        result["members"] = [dict(member) for member in members]
        _attach_conversation_agent_projection(result, agent_members)
        return result

    def list_members(self, conversation_id: str) -> list[dict[str, Any]]:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise ContractError(f"conversation not found: {conversation_id}")
        return list(conversation["members"])

    def list_conversations(
        self,
        workspace_id: str,
        requester_actor_id: str,
        *,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List conversations visible to the requester through membership ACL."""

        workspace_id = _required_id(workspace_id, "workspace id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        _require_page_limit(limit)
        with self.database._lock:
            _require_workspace_actor(self.database._conn, workspace_id, requester_actor_id)
            statuses = ("active", "archived") if include_archived else ("active",)
            placeholders = ",".join("?" for _ in statuses)
            rows = self.database._conn.execute(
                f"""
                SELECT c.*, COALESCE(p.pinned, 0) AS pinned
                    , (
                        SELECT COUNT(*)
                        FROM conversation_members AS cm2
                        WHERE cm2.conversation_id = c.conversation_id
                          AND cm2.left_at IS NULL
                    ) AS member_count
                FROM conversations AS c
                JOIN conversation_members AS m ON m.conversation_id = c.conversation_id
                LEFT JOIN conversation_preferences AS p
                  ON p.conversation_id = c.conversation_id AND p.actor_id = ?
                WHERE c.workspace_id = ? AND m.actor_id = ? AND m.left_at IS NULL
                  AND c.status IN ({placeholders})
                ORDER BY pinned DESC, c.updated_at DESC, c.conversation_id
                LIMIT ?
                """,
                (requester_actor_id, workspace_id, requester_actor_id, *statuses, limit),
            ).fetchall()
            agent_members_by_conversation: dict[str, list[sqlite3.Row]] = {
                str(row["conversation_id"]): [] for row in rows
            }
            if agent_members_by_conversation:
                conversation_placeholders = ",".join("?" for _ in agent_members_by_conversation)
                agent_rows = self.database._conn.execute(
                    f"""
                    SELECT cm.conversation_id, i.agent_id, cm.actor_id, i.status
                    FROM conversation_members AS cm
                    JOIN actors AS a ON a.actor_id = cm.actor_id
                    JOIN agent_installations AS i
                      ON i.actor_id = cm.actor_id
                     AND i.workspace_id = ?
                    WHERE cm.conversation_id IN ({conversation_placeholders})
                      AND cm.left_at IS NULL
                      AND a.actor_kind = 'agent'
                    ORDER BY cm.conversation_id, cm.joined_at, cm.actor_id
                    """,
                    (workspace_id, *agent_members_by_conversation),
                ).fetchall()
                for agent_row in agent_rows:
                    agent_members_by_conversation[str(agent_row["conversation_id"])].append(agent_row)

        result = []
        for row in rows:
            conversation = _conversation_row(row)
            _attach_conversation_agent_projection(
                conversation,
                agent_members_by_conversation[str(row["conversation_id"])],
            )
            result.append(conversation)
        return result

    def list_accessible_conversation_ids(
        self,
        workspace_id: str,
        requester_actor_id: str,
        *,
        include_archived: bool = False,
    ) -> list[str]:
        """Return every conversation id visible to a member without page truncation.

        Search uses this allow-list before querying the FTS projection.  Keeping the
        membership predicate here avoids using the normal UI page size as an ACL
        boundary, which could otherwise hide or accidentally expose results when a
        workspace has more conversations than the first page.
        """

        workspace_id = _required_id(workspace_id, "workspace id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        with self.database._lock:
            _require_workspace_actor(self.database._conn, workspace_id, requester_actor_id)
            statuses = ("active", "archived") if include_archived else ("active",)
            placeholders = ",".join("?" for _ in statuses)
            rows = self.database._conn.execute(
                f"""
                SELECT c.conversation_id
                FROM conversations AS c
                JOIN conversation_members AS m ON m.conversation_id = c.conversation_id
                WHERE c.workspace_id = ? AND m.actor_id = ? AND m.left_at IS NULL
                  AND c.status IN ({placeholders})
                ORDER BY c.conversation_id
                """,
                (workspace_id, requester_actor_id, *statuses),
            ).fetchall()
        return [str(row["conversation_id"]) for row in rows]

    def set_pinned(
        self, conversation_id: str, actor_id: str, *, pinned: bool
    ) -> dict[str, Any]:
        """Set a member's local conversation pin without changing shared state."""

        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "conversation pin actor id")
        if not isinstance(pinned, bool):
            raise ContractError("conversation pinned must be boolean")
        now = _utc_now()
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            _require_conversation_member(connection, conversation_id, actor_id)
            existing = connection.execute(
                "SELECT pinned FROM conversation_preferences WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
            changed = existing is None or bool(existing["pinned"]) != pinned
            connection.execute(
                """
                INSERT INTO conversation_preferences (conversation_id, actor_id, pinned, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id, actor_id) DO UPDATE SET
                    pinned = excluded.pinned,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, actor_id, int(pinned), now),
            )
            row = connection.execute(
                """
                SELECT conversation_id, actor_id, pinned, updated_at
                FROM conversation_preferences
                WHERE conversation_id = ? AND actor_id = ?
                """,
                (conversation_id, actor_id),
            ).fetchone()
        assert row is not None
        result = dict(row)
        result["pinned"] = bool(result["pinned"])
        result["changed"] = changed
        return result

    def update_conversation(
        self,
        conversation_id: str,
        requester_actor_id: str,
        *,
        title: Optional[str] = None,
        reply_mode: Optional[str] = None,
        coordinator_agent_id: Optional[str] = None,
        allow_at_all: Optional[bool] = None,
        max_fanout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Change conversation policy through one admin-authorized command."""

        conversation_id = _required_id(conversation_id, "conversation id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        if title is not None:
            title = str(title).strip()
        if reply_mode is not None:
            reply_mode = _required_kind(reply_mode, "reply mode")
            if reply_mode not in {"mention_only", "coordinator", "all_messages"}:
                raise ContractError("reply mode is not supported")
        if allow_at_all is not None and not isinstance(allow_at_all, bool):
            raise ContractError("allow_at_all must be boolean")
        if max_fanout is not None:
            _require_positive_int(max_fanout, "max fanout")
        if coordinator_agent_id is not None:
            coordinator_agent_id = _optional_id(coordinator_agent_id, "coordinator agent id")
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            member = _require_conversation_member(connection, conversation_id, requester_actor_id)
            if not _is_conversation_admin(member, conversation, requester_actor_id):
                raise ContractError("conversation admin authority is required")
            if coordinator_agent_id is not None:
                _require_agent_workspace(connection, conversation["workspace_id"], coordinator_agent_id)
                active_coordinator = connection.execute(
                    """
                    SELECT 1 FROM conversation_members AS cm
                    JOIN agent_installations AS i ON i.actor_id = cm.actor_id
                    WHERE cm.conversation_id = ? AND cm.left_at IS NULL
                      AND i.agent_id = ?
                    """,
                    (conversation_id, coordinator_agent_id),
                ).fetchone()
                if active_coordinator is None:
                    raise ContractError("coordinator must be an active conversation agent member")
            if conversation["conversation_kind"] == ConversationKind.DIRECT.value:
                if reply_mode is not None and reply_mode != ReplyMode.COORDINATOR.value:
                    raise ContractError("direct conversations use coordinator routing semantics")
                if coordinator_agent_id is not None:
                    current_agents = connection.execute(
                        """
                        SELECT i.agent_id FROM conversation_members AS cm
                        JOIN agent_installations AS i ON i.actor_id = cm.actor_id
                        WHERE cm.conversation_id = ? AND cm.left_at IS NULL
                        """,
                        (conversation_id,),
                    ).fetchall()
                    if len(current_agents) != 1 or current_agents[0]["agent_id"] != coordinator_agent_id:
                        raise ContractError("direct conversation coordinator must be its only agent member")
            changes: dict[str, Any] = {}
            if title is not None:
                changes["title"] = title
            if reply_mode is not None:
                changes["reply_mode"] = reply_mode
            if coordinator_agent_id is not None:
                changes["coordinator_agent_id"] = coordinator_agent_id
            if allow_at_all is not None:
                changes["allow_at_all"] = int(allow_at_all)
            if max_fanout is not None:
                changes["max_fanout"] = max_fanout
            if changes:
                changes["updated_at"] = _utc_now()
                assignments = ", ".join(f"{key} = ?" for key in changes)
                connection.execute(
                    f"UPDATE conversations SET {assignments} WHERE conversation_id = ?",
                    (*changes.values(), conversation_id),
                )
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        assert row is not None
        return _conversation_row(row)

    def transition_conversation(
        self,
        conversation_id: str,
        requester_actor_id: str,
        target_status: ConversationStatus | str,
        *,
        reason: str = "",
    ) -> dict[str, Any]:
        """Archive/restore/delete by tombstone; never hard-delete message history."""

        conversation_id = _required_id(conversation_id, "conversation id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        target = _as_enum(ConversationStatus, target_status, "conversation status")
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            member = _require_conversation_member(connection, conversation_id, requester_actor_id)
            if not _is_conversation_admin(member, conversation, requester_actor_id):
                raise ContractError("conversation admin authority is required")
            current = ConversationStatus(conversation["status"])
            require_transition(current, target, CONVERSATION_EDGES)
            now = _utc_now()
            connection.execute(
                """
                UPDATE conversations
                SET status = ?, archived_at = CASE WHEN ? = 'archived' THEN ? ELSE archived_at END,
                    deleted_at = CASE WHEN ? = 'deleted' THEN ? ELSE deleted_at END,
                    updated_at = ?
                WHERE conversation_id = ?
                """,
                (target.value, target.value, now, target.value, now, now, conversation_id),
            )
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        assert row is not None
        return _conversation_row(row) | {"transition_reason": str(reason or "").strip()}

    def mute_conversation(
        self,
        conversation_id: str,
        actor_id: str,
        *,
        muted_until: Optional[str] = None,
    ) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        if muted_until is not None:
            _require_timestamp(muted_until, "muted_until")
        with self.database.transaction() as connection:
            _require_conversation_member(connection, conversation_id, actor_id)
            connection.execute(
                "UPDATE conversation_members SET muted_until = ? WHERE conversation_id = ? AND actor_id = ?",
                (muted_until, conversation_id, actor_id),
            )
            row = connection.execute(
                "SELECT * FROM conversation_members WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def add_member(
        self,
        conversation_id: str,
        actor_id: str,
        *,
        member_role: str = "member",
        history_policy: HistoryPolicy | str = HistoryPolicy.FROM_JOIN,
        requester_actor_id: Optional[str] = None,
    ) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        member_role = _require_text(member_role, "conversation member role")
        history_policy = _as_enum(HistoryPolicy, history_policy, "history policy")
        joined_at = _utc_now()
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            if requester_actor_id is not None:
                requester_actor_id = _required_id(requester_actor_id, "requester actor id")
                requester = _require_conversation_member(
                    connection, conversation_id, requester_actor_id
                )
                if not _is_conversation_admin(requester, conversation, requester_actor_id):
                    raise ContractError("conversation admin authority is required")
            if conversation["status"] == ConversationStatus.DELETED.value:
                raise ContractError("deleted conversations cannot accept members")
            _require_workspace_actor(connection, conversation["workspace_id"], actor_id, allow_system=True)
            existing = connection.execute(
                "SELECT * FROM conversation_members WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
            if existing is not None:
                if existing["left_at"] is not None:
                    connection.execute(
                        """
                        UPDATE conversation_members
                        SET member_role = ?, history_policy = ?, joined_at = ?, left_at = NULL
                        WHERE conversation_id = ? AND actor_id = ?
                        """,
                        (member_role, history_policy.value, joined_at, conversation_id, actor_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM conversation_members WHERE conversation_id = ? AND actor_id = ?",
                        (conversation_id, actor_id),
                    ).fetchone()
                    assert row is not None
                    return dict(row)
                if (
                    existing["member_role"] != member_role
                    or existing["history_policy"] != history_policy.value
                ):
                    raise PersistenceConflictError("conversation membership conflicts with existing record")
                return dict(existing)
            if conversation["conversation_kind"] == ConversationKind.DIRECT.value:
                actor_kind = connection.execute(
                    "SELECT actor_kind FROM actors WHERE actor_id = ?", (actor_id,)
                ).fetchone()
                active_counts = connection.execute(
                    """
                    SELECT a.actor_kind, COUNT(*) AS count
                    FROM conversation_members AS cm JOIN actors AS a ON a.actor_id = cm.actor_id
                    WHERE cm.conversation_id = ? AND cm.left_at IS NULL
                    GROUP BY a.actor_kind
                    """,
                    (conversation_id,),
                ).fetchall()
                counts = {row["actor_kind"]: int(row["count"]) for row in active_counts}
                if actor_kind is None:
                    raise ContractError("conversation member actor is missing")
                if actor_kind["actor_kind"] == ActorKind.AGENT.value and counts.get(ActorKind.AGENT.value, 0) >= 1:
                    raise ContractError("direct conversations accept exactly one agent")
                if actor_kind["actor_kind"] == ActorKind.HUMAN.value and counts.get(ActorKind.HUMAN.value, 0) >= 1:
                    raise ContractError("direct conversations accept exactly one human")
            try:
                connection.execute(
                    """
                    INSERT INTO conversation_members
                        (conversation_id, actor_id, member_role, history_policy, joined_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (conversation_id, actor_id, member_role, history_policy.value, joined_at),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("conversation member references missing identity") from error
            row = connection.execute(
                "SELECT * FROM conversation_members WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def remove_member(
        self,
        conversation_id: str,
        actor_id: str,
        requester_actor_id: str,
    ) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        left_at = _utc_now()
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            requester = _require_conversation_member(connection, conversation_id, requester_actor_id)
            if not _is_conversation_admin(requester, conversation, requester_actor_id):
                raise ContractError("conversation admin authority is required")
            target = _require_conversation_member(connection, conversation_id, actor_id)
            connection.execute(
                "UPDATE conversation_members SET left_at = ? WHERE conversation_id = ? AND actor_id = ?",
                (left_at, conversation_id, actor_id),
            )
            row = connection.execute(
                "SELECT * FROM conversation_members WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def leave_conversation(self, conversation_id: str, actor_id: str) -> dict[str, Any]:
        """Leave a conversation while retaining the immutable membership record."""

        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        with self.database.transaction() as connection:
            _require_conversation_member(connection, conversation_id, actor_id)
            connection.execute(
                "UPDATE conversation_members SET left_at = ? WHERE conversation_id = ? AND actor_id = ?",
                (_utc_now(), conversation_id, actor_id),
            )
            row = connection.execute(
                "SELECT * FROM conversation_members WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def create_thread(
        self,
        thread_id: str,
        conversation_id: str,
        root_message_id: str,
    ) -> dict[str, Any]:
        thread_id = _required_id(thread_id, "thread id")
        conversation_id = _required_id(conversation_id, "conversation id")
        root_message_id = _required_id(root_message_id, "root message id")
        created_at = _utc_now()
        try:
            with self.database.transaction() as connection:
                root = connection.execute(
                    "SELECT conversation_id, thread_id FROM messages WHERE message_id = ?",
                    (root_message_id,),
                ).fetchone()
                if root is None or root["conversation_id"] != conversation_id:
                    raise ContractError("thread root message is missing or belongs to another conversation")
                if root["thread_id"] is not None:
                    raise PersistenceConflictError("root message already belongs to a thread")
                connection.execute(
                    "INSERT INTO threads (thread_id, conversation_id, root_message_id, created_at) VALUES (?, ?, ?, ?)",
                    (thread_id, conversation_id, root_message_id, created_at),
                )
                connection.execute(
                    "UPDATE messages SET thread_id = ? WHERE message_id = ?",
                    (thread_id, root_message_id),
                )
                row = connection.execute(
                    "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
                ).fetchone()
        except ContractError:
            raise
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("thread conflicts with existing identity") from error
        assert row is not None
        return dict(row)

    def get_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        thread_id = _required_id(thread_id, "thread id")
        with self.database._lock:
            row = self.database._conn.execute(
                """
                SELECT t.*, c.workspace_id, c.conversation_kind, c.title AS conversation_title,
                       c.status AS conversation_status
                FROM threads AS t
                JOIN conversations AS c ON c.conversation_id = t.conversation_id
                WHERE t.thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def append_message(
        self,
        message_id: str,
        conversation_id: str,
        author: ActorRef,
        body: str,
        idempotency_key: str,
        correlation_id: str,
        *,
        thread_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        quote_message_id: Optional[str] = None,
        client_request_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        mentions: Sequence[str] = (),
        created_at: Optional[str] = None,
        reasoning: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        if not isinstance(author, ActorRef):
            raise ContractError("message author must be an ActorRef")
        message_id = _required_id(message_id, "message id")
        conversation_id = _required_id(conversation_id, "conversation id")
        idempotency_key = _required_id(idempotency_key, "message idempotency key")
        correlation_id = _required_id(correlation_id, "message correlation id")
        body = _require_text(body, "message body")
        thread_id = _optional_id(thread_id, "thread id")
        parent_message_id = _optional_id(parent_message_id, "parent message id")
        quote_message_id = _optional_id(quote_message_id, "quote message id")
        client_request_id = _optional_id(client_request_id, "client request id")
        causation_id = _optional_id(causation_id, "causation id")
        created_at = created_at or _utc_now()
        _require_timestamp(created_at, "message created_at")
        mention_ids = tuple(sorted(_unique_id_list(mentions, "mentioned actor id")))
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT workspace_id, status FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            if conversation["status"] != ConversationStatus.ACTIVE.value:
                raise ContractError("messages can only be appended to active conversations")
            actor_row = connection.execute(
                "SELECT actor_kind FROM actors WHERE actor_id = ?", (author.id,)
            ).fetchone()
            if actor_row is None or actor_row["actor_kind"] != author.kind.value:
                raise ContractError("message author identity is missing or has a different kind")
            member = connection.execute(
                """
                SELECT 1 FROM conversation_members
                WHERE conversation_id = ? AND actor_id = ? AND left_at IS NULL
                """,
                (conversation_id, author.id),
            ).fetchone()
            if member is None and author.kind is not ActorKind.SYSTEM:
                raise ContractError("message author is not an active conversation member")
            for relation_id, label in (
                (parent_message_id, "parent message"),
                (quote_message_id, "quoted message"),
            ):
                if relation_id is None:
                    continue
                related = connection.execute(
                    "SELECT conversation_id, status FROM messages WHERE message_id = ?",
                    (relation_id,),
                ).fetchone()
                if related is None or related["conversation_id"] != conversation_id:
                    raise ContractError(f"{label} is missing or belongs to another conversation")
            if thread_id is not None:
                thread = connection.execute(
                    "SELECT conversation_id, archived_at FROM threads WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
                if thread is None or thread["conversation_id"] != conversation_id:
                    raise ContractError("thread is missing or belongs to another conversation")
                if thread["archived_at"] is not None:
                    raise ContractError("archived threads cannot receive messages")
            existing = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND idempotency_key = ?",
                (conversation_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if not _message_matches(
                    existing,
                    message_id=message_id,
                    author=author,
                    body=body,
                    thread_id=thread_id,
                    parent_message_id=parent_message_id,
                    quote_message_id=quote_message_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    mentions=mention_ids,
                    connection=connection,
                ):
                    raise PersistenceConflictError("message idempotency key has a different payload")
                return _message_row(existing, connection=connection)
            for mention_id in mention_ids:
                mentioned = connection.execute(
                    "SELECT 1 FROM conversation_members WHERE conversation_id = ? AND actor_id = ? AND left_at IS NULL",
                    (conversation_id, mention_id),
                ).fetchone()
                if mentioned is None:
                    raise ContractError(f"mentioned actor is not an active member: {mention_id}")
            try:
                has_reasoning = _table_has_column(connection, "messages", "reasoning")
                if has_reasoning:
                    metadata_json = _json(dict(metadata or {}))
                    connection.execute(
                        """
                        INSERT INTO messages
                            (message_id, conversation_id, thread_id, parent_message_id,
                             author_actor_id, author_kind, body, client_request_id,
                             idempotency_key, correlation_id, causation_id, quote_message_id, created_at,
                             reasoning, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            conversation_id,
                            thread_id,
                            parent_message_id,
                            author.id,
                            author.kind.value,
                            body,
                            client_request_id,
                            idempotency_key,
                            correlation_id,
                            causation_id,
                            quote_message_id,
                            created_at,
                            reasoning,
                            metadata_json,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO messages
                            (message_id, conversation_id, thread_id, parent_message_id,
                             author_actor_id, author_kind, body, client_request_id,
                             idempotency_key, correlation_id, causation_id, quote_message_id, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            conversation_id,
                            thread_id,
                            parent_message_id,
                            author.id,
                            author.kind.value,
                            body,
                            client_request_id,
                            idempotency_key,
                            correlation_id,
                            causation_id,
                            quote_message_id,
                            created_at,
                        ),
                    )
                for mention_id in mention_ids:
                    connection.execute(
                        "INSERT INTO message_mentions (message_id, actor_id) VALUES (?, ?)",
                        (message_id, mention_id),
                    )
                connection.execute(
                    """
                    INSERT INTO message_revisions
                        (message_revision_id, message_id, revision_number, editor_actor_id, body, created_at)
                    VALUES (?, ?, 1, ?, ?, ?)
                    """,
                    (f"{message_id}:revision:1", message_id, author.id, body, created_at),
                )
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                    (created_at, conversation_id),
                )
                row = connection.execute(
                    "SELECT * FROM messages WHERE message_id = ?", (message_id,)
                ).fetchone()
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("message references a missing relation") from error
        assert row is not None
        return _message_row(row, connection=self.database._conn)

    def get_message(
        self, message_id: str, requester_actor_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        message_id = _required_id(message_id, "message id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if row is not None and requester_actor_id is not None:
                _require_conversation_member(
                    self.database._conn, row["conversation_id"], requester_actor_id
                )
            return _message_row(row, connection=self.database._conn) if row is not None else None

    def list_messages(
        self,
        conversation_id: str,
        *,
        thread_id: Optional[str] = None,
        after_cursor: Optional[str] = None,
        limit: int = 50,
        requester_actor_id: Optional[str] = None,
        include_tombstones: bool = True,
    ) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        thread_id = _optional_id(thread_id, "thread id")
        _require_page_limit(limit)
        cursor_created_at, cursor_message_id = _message_cursor(after_cursor)
        where = ["conversation_id = ?"]
        params: list[Any] = [conversation_id]
        with self.database._lock:
            if requester_actor_id is not None:
                _require_conversation_member(
                    self.database._conn, conversation_id, requester_actor_id
                )
            if not include_tombstones:
                where.append("status = 'active'")
            if thread_id is not None:
                thread = self.database._conn.execute(
                    "SELECT conversation_id FROM threads WHERE thread_id = ?", (thread_id,)
                ).fetchone()
                if thread is None or thread["conversation_id"] != conversation_id:
                    raise ContractError("thread is missing or belongs to another conversation")
        if thread_id is not None:
            where.append("thread_id = ?")
            params.append(thread_id)
        if cursor_created_at is not None:
            where.append("(created_at > ? OR (created_at = ? AND message_id > ?))")
            params.extend((cursor_created_at, cursor_created_at, cursor_message_id))
        params.append(limit + 1)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM messages WHERE {' AND '.join(where)} ORDER BY created_at, message_id LIMIT ?",
                params,
            ).fetchall()
            messages = [_message_row(row, connection=self.database._conn) for row in rows[:limit]]
        has_more = len(rows) > limit
        next_cursor = (
            _message_cursor_encode(messages[-1]["created_at"], messages[-1]["message_id"])
            if messages
            else after_cursor
        )
        return {"messages": messages, "next_cursor": next_cursor, "has_more": has_more}

    def set_read_cursor(
        self, conversation_id: str, actor_id: str, last_read_message_id: Optional[str]
    ) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        last_read_message_id = _optional_id(last_read_message_id, "last read message id")
        read_at = _utc_now()
        with self.database.transaction() as connection:
            _require_conversation_member(connection, conversation_id, actor_id)
            if last_read_message_id is not None:
                message = connection.execute(
                    "SELECT conversation_id FROM messages WHERE message_id = ?",
                    (last_read_message_id,),
                ).fetchone()
                if message is None or message["conversation_id"] != conversation_id:
                    raise ContractError("read cursor message belongs to another conversation")
            try:
                connection.execute(
                    """
                    INSERT INTO conversation_read_cursors
                        (conversation_id, actor_id, last_read_message_id, last_read_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(conversation_id, actor_id) DO UPDATE SET
                        last_read_message_id = excluded.last_read_message_id,
                        last_read_at = excluded.last_read_at
                    """,
                    (conversation_id, actor_id, last_read_message_id, read_at),
                )
            except sqlite3.IntegrityError as error:
                raise ContractError("read cursor references a missing relation") from error
            row = connection.execute(
                "SELECT * FROM conversation_read_cursors WHERE conversation_id = ? AND actor_id = ?",
                (conversation_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def set_thread_read_cursor(
        self, thread_id: str, actor_id: str, last_read_message_id: Optional[str]
    ) -> dict[str, Any]:
        thread_id = _required_id(thread_id, "thread id")
        actor_id = _required_id(actor_id, "actor id")
        last_read_message_id = _optional_id(last_read_message_id, "last read message id")
        read_at = _utc_now()
        with self.database.transaction() as connection:
            thread = connection.execute(
                "SELECT conversation_id FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                raise ContractError(f"thread not found: {thread_id}")
            _require_conversation_member(connection, thread["conversation_id"], actor_id)
            if last_read_message_id is not None:
                message = connection.execute(
                    "SELECT conversation_id, thread_id FROM messages WHERE message_id = ?",
                    (last_read_message_id,),
                ).fetchone()
                if (
                    message is None
                    or message["conversation_id"] != thread["conversation_id"]
                    or message["thread_id"] != thread_id
                ):
                    raise ContractError("thread read cursor message belongs to another thread")
            connection.execute(
                """
                INSERT INTO thread_read_cursors
                    (thread_id, actor_id, last_read_message_id, last_read_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_id, actor_id) DO UPDATE SET
                    last_read_message_id = excluded.last_read_message_id,
                    last_read_at = excluded.last_read_at
                """,
                (thread_id, actor_id, last_read_message_id, read_at),
            )
            row = connection.execute(
                "SELECT * FROM thread_read_cursors WHERE thread_id = ? AND actor_id = ?",
                (thread_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def unread_state(self, conversation_id: str, actor_id: str) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        with self.database._lock:
            _require_conversation_member(self.database._conn, conversation_id, actor_id)
            cursor = self.database._conn.execute(
                """
                SELECT m.created_at, m.message_id
                FROM conversation_read_cursors AS c
                JOIN messages AS m ON m.message_id = c.last_read_message_id
                WHERE c.conversation_id = ? AND c.actor_id = ?
                """,
                (conversation_id, actor_id),
            ).fetchone()
            if cursor is None:
                unread = self.database._conn.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE conversation_id = ? AND author_actor_id != ?",
                    (conversation_id, actor_id),
                ).fetchone()["count"]
            else:
                unread = self.database._conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM messages
                    WHERE conversation_id = ?
                      AND author_actor_id != ?
                      AND (created_at > ? OR (created_at = ? AND message_id > ?))
                    """,
                    (conversation_id, actor_id, cursor["created_at"], cursor["created_at"], cursor["message_id"]),
                ).fetchone()["count"]
            threads = self.database._conn.execute(
                """
                SELECT t.thread_id, COUNT(m.message_id) AS unread_count
                FROM threads AS t
                JOIN messages AS m ON m.thread_id = t.thread_id
                LEFT JOIN thread_read_cursors AS c
                  ON c.thread_id = t.thread_id AND c.actor_id = ?
                LEFT JOIN messages AS last_read ON last_read.message_id = c.last_read_message_id
                WHERE t.conversation_id = ?
                  AND m.author_actor_id != ?
                  AND (last_read.message_id IS NULL OR m.created_at > last_read.created_at
                       OR (m.created_at = last_read.created_at AND m.message_id > last_read.message_id))
                GROUP BY t.thread_id
                HAVING COUNT(m.message_id) > 0
                ORDER BY t.thread_id
                """,
                (actor_id, conversation_id, actor_id),
            ).fetchall()
        return {
            "conversation_id": conversation_id,
            "actor_id": actor_id,
            "unread_count": int(unread),
            "thread_unread": {row["thread_id"]: int(row["unread_count"]) for row in threads},
        }

    def edit_message(
        self,
        message_id: str,
        editor_actor_id: str,
        body: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> dict[str, Any]:
        message_id = _required_id(message_id, "message id")
        editor_actor_id = _required_id(editor_actor_id, "editor actor id")
        body = _require_text(body, "message body")
        with self.database.transaction() as connection:
            message = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message is None:
                raise ContractError(f"message not found: {message_id}")
            member = _require_conversation_member(
                connection, message["conversation_id"], editor_actor_id
            )
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (message["conversation_id"],),
            ).fetchone()
            if message["author_actor_id"] != editor_actor_id and not _is_conversation_admin(
                member, conversation, editor_actor_id
            ):
                raise ContractError("only the author or a conversation admin may edit a message")
            if message["status"] != "active":
                raise ContractError("deleted messages cannot be edited")
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision_number), 0) + 1 AS next_revision FROM message_revisions WHERE message_id = ?",
                (message_id,),
            ).fetchone()["next_revision"]
            if expected_revision is not None:
                if (
                    isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision != revision - 1
                ):
                    raise PersistenceConflictError("message revision is stale")
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO message_revisions
                    (message_revision_id, message_id, revision_number, editor_actor_id, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"{message_id}:revision:{revision}", message_id, revision, editor_actor_id, body, now),
            )
            connection.execute(
                "UPDATE messages SET body = ?, edited_at = ? WHERE message_id = ?",
                (body, now, message_id),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, message["conversation_id"]),
            )
            row = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        assert row is not None
        return _message_row(row, connection=self.database._conn)

    def list_message_revisions(self, message_id: str, requester_actor_id: Optional[str] = None) -> list[dict[str, Any]]:
        message = self.get_message(message_id, requester_actor_id)
        if message is None:
            raise ContractError(f"message not found: {message_id}")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM message_revisions WHERE message_id = ? ORDER BY revision_number",
                (message_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_message(self, message_id: str, requester_actor_id: str) -> dict[str, Any]:
        message_id = _required_id(message_id, "message id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        with self.database.transaction() as connection:
            message = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message is None:
                raise ContractError(f"message not found: {message_id}")
            member = _require_conversation_member(
                connection, message["conversation_id"], requester_actor_id
            )
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (message["conversation_id"],),
            ).fetchone()
            if message["author_actor_id"] != requester_actor_id and not _is_conversation_admin(
                member, conversation, requester_actor_id
            ):
                raise ContractError("only the author or a conversation admin may delete a message")
            if message["status"] == "tombstoned":
                return _message_row(message, connection=connection)
            now = _utc_now()
            connection.execute(
                """
                UPDATE messages
                SET status = 'tombstoned', body = '', deleted_at = ?, deleted_by_actor_id = ?
                WHERE message_id = ?
                """,
                (now, requester_actor_id, message_id),
            )
            connection.execute(
                "DELETE FROM conversation_pins WHERE message_id = ?", (message_id,)
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, message["conversation_id"]),
            )
            row = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        assert row is not None
        return _message_row(row, connection=self.database._conn)

    def set_reaction(
        self, message_id: str, actor_id: str, emoji: str, *, active: bool = True
    ) -> dict[str, Any]:
        message_id = _required_id(message_id, "message id")
        actor_id = _required_id(actor_id, "actor id")
        emoji = _require_text(emoji, "reaction emoji")
        if len(emoji) > 32:
            raise ContractError("reaction emoji is too long")
        if not isinstance(active, bool):
            raise ContractError("reaction active must be boolean")
        with self.database.transaction() as connection:
            message = connection.execute(
                "SELECT conversation_id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message is None:
                raise ContractError(f"message not found: {message_id}")
            _require_conversation_member(connection, message["conversation_id"], actor_id)
            if active:
                connection.execute(
                    """
                    INSERT INTO message_reactions (message_id, actor_id, emoji, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(message_id, actor_id, emoji) DO NOTHING
                    """,
                    (message_id, actor_id, emoji, _utc_now()),
                )
            else:
                connection.execute(
                    "DELETE FROM message_reactions WHERE message_id = ? AND actor_id = ? AND emoji = ?",
                    (message_id, actor_id, emoji),
                )
        return {"message_id": message_id, "actor_id": actor_id, "emoji": emoji, "active": active}

    def list_reactions(self, message_id: str, requester_actor_id: Optional[str] = None) -> list[dict[str, Any]]:
        message = self.get_message(message_id, requester_actor_id)
        if message is None:
            raise ContractError(f"message not found: {message_id}")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT actor_id, emoji, created_at FROM message_reactions WHERE message_id = ? ORDER BY emoji, actor_id",
                (message_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pin_message(self, conversation_id: str, message_id: str, actor_id: str) -> dict[str, Any]:
        conversation_id = _required_id(conversation_id, "conversation id")
        message_id = _required_id(message_id, "message id")
        actor_id = _required_id(actor_id, "actor id")
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            member = _require_conversation_member(connection, conversation_id, actor_id)
            if not _is_conversation_admin(member, conversation, actor_id):
                raise ContractError("conversation admin authority is required to pin messages")
            message = connection.execute(
                "SELECT conversation_id, status FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message is None or message["conversation_id"] != conversation_id:
                raise ContractError("message does not belong to conversation")
            if message["status"] != "active":
                raise ContractError("deleted messages cannot be pinned")
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO conversation_pins (conversation_id, message_id, pinned_by_actor_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(conversation_id, message_id) DO NOTHING
                """,
                (conversation_id, message_id, actor_id, now),
            )
            row = connection.execute(
                "SELECT * FROM conversation_pins WHERE conversation_id = ? AND message_id = ?",
                (conversation_id, message_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def unpin_message(self, conversation_id: str, message_id: str, actor_id: str) -> bool:
        conversation_id = _required_id(conversation_id, "conversation id")
        message_id = _required_id(message_id, "message id")
        actor_id = _required_id(actor_id, "actor id")
        with self.database.transaction() as connection:
            conversation = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if conversation is None:
                raise ContractError(f"conversation not found: {conversation_id}")
            member = _require_conversation_member(connection, conversation_id, actor_id)
            if not _is_conversation_admin(member, conversation, actor_id):
                raise ContractError("conversation admin authority is required to unpin messages")
            cursor = connection.execute(
                "DELETE FROM conversation_pins WHERE conversation_id = ? AND message_id = ?",
                (conversation_id, message_id),
            )
        return cursor.rowcount > 0

    def list_pinned_messages(self, conversation_id: str, requester_actor_id: str) -> list[dict[str, Any]]:
        conversation_id = _required_id(conversation_id, "conversation id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        with self.database._lock:
            _require_conversation_member(self.database._conn, conversation_id, requester_actor_id)
            rows = self.database._conn.execute(
                """
                SELECT p.*, m.body, m.author_actor_id, m.status
                FROM conversation_pins AS p JOIN messages AS m ON m.message_id = p.message_id
                WHERE p.conversation_id = ? ORDER BY p.created_at DESC, p.message_id
                """,
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_translation(
        self,
        message_id: str,
        actor_id: str,
        language: str,
        body: str,
        *,
        model: str = "",
    ) -> dict[str, Any]:
        message_id = _required_id(message_id, "message id")
        actor_id = _required_id(actor_id, "actor id")
        language = _required_kind(language, "translation language")
        body = _require_text(body, "translation body")
        model = str(model or "").strip()
        with self.database.transaction() as connection:
            message = connection.execute(
                "SELECT conversation_id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message is None:
                raise ContractError(f"message not found: {message_id}")
            _require_conversation_member(connection, message["conversation_id"], actor_id)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO message_translations (message_id, language, body, model, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id, language) DO UPDATE SET
                    body = excluded.body, model = excluded.model, created_at = excluded.created_at
                """,
                (message_id, language, body, model, now),
            )
            row = connection.execute(
                "SELECT language, body, model, created_at FROM message_translations WHERE message_id = ? AND language = ?",
                (message_id, language),
            ).fetchone()
        assert row is not None
        return dict(row)

    def add_attachment(
        self,
        message_id: str,
        actor_id: str,
        attachment: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(attachment, Mapping):
            raise ContractError("attachment must be an object")
        message_id = _required_id(message_id, "message id")
        actor_id = _required_id(actor_id, "actor id")
        attachment_id = _required_id(attachment.get("attachment_id", ""), "attachment id")
        storage_ref = _require_text(attachment.get("storage_ref"), "attachment storage ref")
        filename = _require_text(attachment.get("filename"), "attachment filename")
        media_type = _require_text(attachment.get("media_type"), "attachment media type")
        sha256 = _require_text(attachment.get("sha256"), "attachment sha256")
        size_bytes = attachment.get("size_bytes")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise ContractError("attachment size_bytes must be a non-negative integer")
        metadata = attachment.get("metadata") or {}
        metadata_json = _json_object(metadata, "attachment metadata")
        with self.database.transaction() as connection:
            message = connection.execute(
                "SELECT conversation_id, status FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message is None:
                raise ContractError(f"message not found: {message_id}")
            if message["status"] != "active":
                raise ContractError("deleted messages cannot receive attachments")
            _require_conversation_member(connection, message["conversation_id"], actor_id)
            now = _utc_now()
            existing = connection.execute(
                "SELECT * FROM message_attachments WHERE attachment_id = ?",
                (attachment_id,),
            ).fetchone()
            if existing is not None:
                if any(
                    existing[key] != expected
                    for key, expected in {
                        "message_id": message_id,
                        "storage_ref": storage_ref,
                        "filename": filename,
                        "media_type": media_type,
                        "size_bytes": size_bytes,
                        "sha256": sha256,
                        "metadata_json": metadata_json,
                    }.items()
                ):
                    raise PersistenceConflictError(
                        "attachment idempotency key has a different payload"
                    )
                row = existing
            else:
                try:
                    connection.execute(
                        """
                        INSERT INTO message_attachments
                            (attachment_id, message_id, storage_ref, filename, media_type,
                             size_bytes, sha256, metadata_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (attachment_id, message_id, storage_ref, filename, media_type, size_bytes, sha256, metadata_json, now),
                    )
                except sqlite3.IntegrityError as error:
                    raise PersistenceConflictError("attachment conflicts with existing identity") from error
                row = connection.execute(
                    "SELECT * FROM message_attachments WHERE attachment_id = ?", (attachment_id,)
                ).fetchone()
        assert row is not None
        result = dict(row)
        result["metadata"] = _json_load(result.pop("metadata_json"), {})
        return result

    def follow_thread(self, thread_id: str, actor_id: str, *, followed: bool = True) -> dict[str, Any]:
        thread_id = _required_id(thread_id, "thread id")
        actor_id = _required_id(actor_id, "actor id")
        if not isinstance(followed, bool):
            raise ContractError("followed must be boolean")
        with self.database.transaction() as connection:
            thread = connection.execute(
                "SELECT conversation_id FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                raise ContractError(f"thread not found: {thread_id}")
            _require_conversation_member(connection, thread["conversation_id"], actor_id)
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO thread_followers (thread_id, actor_id, followed_at, unfollowed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(thread_id, actor_id) DO UPDATE SET
                    followed_at = CASE WHEN ? THEN excluded.followed_at ELSE thread_followers.followed_at END,
                    unfollowed_at = CASE WHEN ? THEN NULL ELSE excluded.followed_at END
                """,
                (thread_id, actor_id, now, None if followed else now, followed, followed),
            )
            row = connection.execute(
                "SELECT * FROM thread_followers WHERE thread_id = ? AND actor_id = ?",
                (thread_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def list_thread_followers(self, thread_id: str, requester_actor_id: str) -> list[dict[str, Any]]:
        thread_id = _required_id(thread_id, "thread id")
        requester_actor_id = _required_id(requester_actor_id, "requester actor id")
        with self.database._lock:
            thread = self.database._conn.execute(
                "SELECT conversation_id FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                raise ContractError(f"thread not found: {thread_id}")
            _require_conversation_member(self.database._conn, thread["conversation_id"], requester_actor_id)
            rows = self.database._conn.execute(
                "SELECT * FROM thread_followers WHERE thread_id = ? AND unfollowed_at IS NULL ORDER BY actor_id",
                (thread_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_draft(
        self,
        draft_id: str,
        conversation_id: str,
        actor_id: str,
        client_request_id: str,
        body: str,
        *,
        thread_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        draft_id = _required_id(draft_id, "draft id")
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        client_request_id = _required_id(client_request_id, "draft client request id")
        body = str(body or "")
        thread_id = _optional_id(thread_id, "thread id")
        if expires_at is not None:
            _require_timestamp(expires_at, "draft expires_at")
        with self.database.transaction() as connection:
            _require_conversation_member(connection, conversation_id, actor_id)
            if thread_id is not None:
                thread = connection.execute(
                    "SELECT conversation_id FROM threads WHERE thread_id = ?", (thread_id,)
                ).fetchone()
                if thread is None or thread["conversation_id"] != conversation_id:
                    raise ContractError("draft thread belongs to another conversation")
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO message_drafts
                    (draft_id, conversation_id, actor_id, thread_id, client_request_id, body, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, actor_id, client_request_id) DO UPDATE SET
                    draft_id = excluded.draft_id, thread_id = excluded.thread_id, body = excluded.body,
                    updated_at = excluded.updated_at, expires_at = excluded.expires_at
                """,
                (draft_id, conversation_id, actor_id, thread_id, client_request_id, body, now, expires_at),
            )
            row = connection.execute(
                "SELECT * FROM message_drafts WHERE conversation_id = ? AND actor_id = ? AND client_request_id = ?",
                (conversation_id, actor_id, client_request_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def get_draft(self, conversation_id: str, actor_id: str, client_request_id: str) -> Optional[dict[str, Any]]:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        client_request_id = _required_id(client_request_id, "draft client request id")
        with self.database._lock:
            _require_conversation_member(self.database._conn, conversation_id, actor_id)
            row = self.database._conn.execute(
                "SELECT * FROM message_drafts WHERE conversation_id = ? AND actor_id = ? AND client_request_id = ?",
                (conversation_id, actor_id, client_request_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_draft(self, conversation_id: str, actor_id: str, client_request_id: str) -> bool:
        conversation_id = _required_id(conversation_id, "conversation id")
        actor_id = _required_id(actor_id, "actor id")
        client_request_id = _required_id(client_request_id, "draft client request id")
        with self.database.transaction() as connection:
            _require_conversation_member(connection, conversation_id, actor_id)
            result = connection.execute(
                "DELETE FROM message_drafts WHERE conversation_id = ? AND actor_id = ? AND client_request_id = ?",
                (conversation_id, actor_id, client_request_id),
            )
        return result.rowcount > 0

    def stable_message_link(self, conversation_id: str, message_id: str) -> str:
        conversation_id = _required_id(conversation_id, "conversation id")
        message_id = _required_id(message_id, "message id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT conversation_id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        if row is None or row["conversation_id"] != conversation_id:
            raise ContractError("message does not belong to conversation")
        return f"astra://conversation/{conversation_id}/message/{message_id}"


class EventRepository:
    """Append-only domain events plus stable cursor pagination."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def append(self, event: DomainEvent) -> dict[str, Any]:
        if not isinstance(event, DomainEvent):
            raise ContractError("event must be a DomainEvent")
        values = event.to_dict()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM domain_events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            if existing is not None:
                persisted = _event_row(existing)
                if _event_matches(persisted, values):
                    return persisted
                raise PersistenceConflictError(
                    f"event idempotency conflict: {event.event_id} has a different payload"
                )
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO domain_events
                        (event_id, event_type, workspace_id, actor_id, aggregate_type,
                         aggregate_id, correlation_id, causation_id, schema_version,
                         payload_json, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.event_type,
                        event.workspace_id,
                        event.actor_id,
                        event.aggregate_type,
                        event.aggregate_id,
                        event.correlation_id,
                        event.causation_id,
                        event.schema_version,
                        _json(values["payload"]),
                        event.occurred_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ContractError("event references a missing workspace or actor") from error
            row = connection.execute(
                "SELECT * FROM domain_events WHERE sequence = ?", (cursor.lastrowid,)
            ).fetchone()
            assert row is not None
            return _event_row(row)

    def list_workspace_events(
        self,
        workspace_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        _require_text(workspace_id, "workspace id")
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ContractError("after_sequence must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ContractError("limit must be an integer between 1 and 200")
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT * FROM domain_events
                WHERE workspace_id = ? AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (workspace_id, after_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        events = [_event_row(row) for row in rows[:limit]]
        return {
            "events": events,
            "next_sequence": events[-1]["sequence"] if events else after_sequence,
            "has_more": has_more,
        }


class TaskRepository:
    """Normalized task commands with dependency and transition safeguards."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_task(
        self,
        task_id: str,
        workspace_id: str,
        title: str,
        acceptance_criteria: str,
        created_by_actor_id: str,
        *,
        description: str = "",
        owner_agent_id: Optional[str] = None,
        reviewer_actor_id: Optional[str] = None,
        canonical_thread_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        priority: int = 0,
        due_at: Optional[str] = None,
        status: TaskStatus | str = TaskStatus.OPEN,
    ) -> dict[str, Any]:
        task_id = _required_id(task_id, "task id")
        workspace_id = _required_id(workspace_id, "workspace id")
        title = _require_text(title, "task title")
        acceptance_criteria = _require_text(acceptance_criteria, "acceptance criteria")
        created_by_actor_id = _required_id(created_by_actor_id, "created by actor id")
        owner_agent_id = _optional_id(owner_agent_id, "owner agent id")
        reviewer_actor_id = _optional_id(reviewer_actor_id, "reviewer actor id")
        canonical_thread_id = _optional_id(canonical_thread_id, "canonical thread id")
        parent_task_id = _optional_id(parent_task_id, "parent task id")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ContractError("task priority must be an integer")
        status = _as_enum(TaskStatus, status, "task status")
        now = _utc_now()
        try:
            with self.database.transaction() as connection:
                _require_workspace_actor(connection, workspace_id, created_by_actor_id, allow_system=True)
                if owner_agent_id is not None:
                    _require_agent_workspace(connection, workspace_id, owner_agent_id)
                if reviewer_actor_id is not None:
                    _require_workspace_actor(connection, workspace_id, reviewer_actor_id)
                if canonical_thread_id is not None:
                    thread = connection.execute(
                        """
                        SELECT c.workspace_id FROM threads AS t
                        JOIN conversations AS c ON c.conversation_id = t.conversation_id
                        WHERE t.thread_id = ?
                        """,
                        (canonical_thread_id,),
                    ).fetchone()
                    if thread is None or thread["workspace_id"] != workspace_id:
                        raise ContractError("canonical thread is missing or belongs to another workspace")
                if parent_task_id is not None:
                    parent = connection.execute(
                        "SELECT workspace_id FROM tasks WHERE task_id = ?", (parent_task_id,)
                    ).fetchone()
                    if parent is None or parent["workspace_id"] != workspace_id:
                        raise ContractError("parent task is missing or belongs to another workspace")
                connection.execute(
                    """
                    INSERT INTO tasks
                        (task_id, workspace_id, canonical_thread_id, title, description,
                         acceptance_criteria, status, owner_agent_id, reviewer_actor_id,
                         created_by_actor_id, priority, due_at, parent_task_id,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        workspace_id,
                        canonical_thread_id,
                        title,
                        str(description or "").strip(),
                        acceptance_criteria,
                        status.value,
                        owner_agent_id,
                        reviewer_actor_id,
                        created_by_actor_id,
                        priority,
                        due_at,
                        parent_task_id,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("task conflicts with an existing relation") from error
        assert row is not None
        return dict(row) | {"dependencies": []}

    def add_dependency(self, task_id: str, depends_on_task_id: str) -> dict[str, Any]:
        task_id = _required_id(task_id, "task id")
        depends_on_task_id = _required_id(depends_on_task_id, "dependency task id")
        if task_id == depends_on_task_id:
            raise ContractError("a task cannot depend on itself")
        with self.database.transaction() as connection:
            pair = connection.execute(
                """
                SELECT t.workspace_id AS task_workspace, d.workspace_id AS dependency_workspace
                FROM tasks AS t JOIN tasks AS d ON d.task_id = ?
                WHERE t.task_id = ?
                """,
                (depends_on_task_id, task_id),
            ).fetchone()
            if pair is None:
                raise ContractError("task or dependency does not exist")
            if pair["task_workspace"] != pair["dependency_workspace"]:
                raise ContractError("task dependencies must stay inside one workspace")
            cycle = connection.execute(
                """
                WITH RECURSIVE dependency_chain(task_id) AS (
                    SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?
                    UNION
                    SELECT d.depends_on_task_id
                    FROM task_dependencies AS d
                    JOIN dependency_chain AS c ON c.task_id = d.task_id
                )
                SELECT 1 FROM dependency_chain WHERE task_id = ? LIMIT 1
                """,
                (depends_on_task_id, task_id),
            ).fetchone()
            if cycle is not None:
                raise ContractError("task dependency would create a cycle")
            try:
                connection.execute(
                    "INSERT INTO task_dependencies (task_id, depends_on_task_id, created_at) VALUES (?, ?, ?)",
                    (task_id, depends_on_task_id, _utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("task dependency already exists") from error
            row = connection.execute(
                "SELECT * FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
                (task_id, depends_on_task_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        task_id = _required_id(task_id, "task id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            task = dict(row)
            dependencies = self.database._conn.execute(
                """
                SELECT d.task_id, d.depends_on_task_id, t.status
                FROM task_dependencies AS d
                JOIN tasks AS t ON t.task_id = d.depends_on_task_id
                WHERE d.task_id = ? ORDER BY d.depends_on_task_id
                """,
                (task_id,),
            ).fetchall()
            watchers = self.database._conn.execute(
                """
                SELECT tw.actor_id, a.display_name, a.handle, tw.added_at
                FROM task_watchers AS tw JOIN actors AS a ON a.actor_id = tw.actor_id
                WHERE tw.task_id = ? AND tw.removed_at IS NULL
                ORDER BY tw.added_at, tw.actor_id
                """,
                (task_id,),
            ).fetchall()
            labels = self.database._conn.execute(
                "SELECT label FROM task_labels WHERE task_id = ? ORDER BY label", (task_id,)
            ).fetchall()
            runs = self.database._conn.execute(
                """
                SELECT r.*, wr.task_id
                FROM runs AS r
                JOIN work_requests AS wr ON wr.request_id = r.request_id
                WHERE wr.task_id = ?
                ORDER BY r.created_at DESC, r.run_id DESC
                """,
                (task_id,),
            ).fetchall()
        task["dependencies"] = [dict(dependency) for dependency in dependencies]
        task["watchers"] = [dict(watcher) for watcher in watchers]
        task["labels"] = [row["label"] for row in labels]
        task["runs"] = []
        for run in runs:
            item = _run_row(run)
            usage = self.database._conn.execute(
                "SELECT * FROM work_usage WHERE request_id = ?", (run["request_id"],)
            ).fetchone()
            item["usage"] = dict(usage) if usage is not None else {
                "request_id": run["request_id"],
                "tokens": 0,
                "cost_micros": 0,
                "model_calls": 0,
                "tool_calls": 0,
                "wall_seconds": 0,
                "wakes": 0,
                "fanout": 0,
                "handoffs": 0,
            }
            task["runs"].append(item)
        return task

    def set_canonical_thread(self, task_id: str, thread_id: str) -> dict[str, Any]:
        """Bind a task to one immutable discussion thread exactly once."""

        task_id = _required_id(task_id, "task id")
        thread_id = _required_id(thread_id, "canonical thread id")
        with self.database.transaction() as connection:
            task = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            thread = connection.execute(
                """
                SELECT c.workspace_id
                FROM threads AS t
                JOIN conversations AS c ON c.conversation_id = t.conversation_id
                WHERE t.thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
            if task is None:
                raise ContractError(f"task not found: {task_id}")
            if thread is None or thread["workspace_id"] != task["workspace_id"]:
                raise ContractError("canonical thread is missing or belongs to another workspace")
            if task["canonical_thread_id"] not in {None, thread_id}:
                raise PersistenceConflictError("task already has another canonical thread")
            connection.execute(
                "UPDATE tasks SET canonical_thread_id = ?, updated_at = ? WHERE task_id = ?",
                (thread_id, _utc_now(), task_id),
            )
        return self.get_task(task_id) or {}

    def list_tasks(
        self,
        workspace_id: str,
        *,
        statuses: Sequence[TaskStatus | str] = (),
        after_cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        workspace_id = _required_id(workspace_id, "workspace id")
        _require_page_limit(limit)
        cursor_updated_at, cursor_task_id = _task_cursor(after_cursor)
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        normalized_statuses = [_as_enum(TaskStatus, status, "task status").value for status in statuses]
        if normalized_statuses:
            where.append("status IN (" + ",".join("?" for _ in normalized_statuses) + ")")
            params.extend(normalized_statuses)
        if cursor_updated_at is not None:
            where.append("(updated_at > ? OR (updated_at = ? AND task_id > ?))")
            params.extend((cursor_updated_at, cursor_updated_at, cursor_task_id))
        params.append(limit + 1)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY updated_at, task_id LIMIT ?",
                params,
            ).fetchall()
        tasks = [dict(row) for row in rows[:limit]]
        for task in tasks:
            detailed = self.get_task(task["task_id"])
            if detailed is not None:
                task.update(
                    dependencies=detailed["dependencies"],
                    watchers=detailed["watchers"],
                    labels=detailed["labels"],
                    runs=detailed["runs"],
                )
        has_more = len(rows) > limit
        next_cursor = (
            _task_cursor_encode(tasks[-1]["updated_at"], tasks[-1]["task_id"])
            if tasks
            else after_cursor
        )
        return {"tasks": tasks, "next_cursor": next_cursor, "has_more": has_more}

    def assign_task(
        self,
        task_id: str,
        owner_agent_id: str,
        *,
        actor_id: str,
        expected_owner_agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Atomically assign one task; a second owner cannot win the race."""
        task_id = _required_id(task_id, "task id")
        owner_agent_id = _required_id(owner_agent_id, "task owner agent id")
        actor_id = _required_id(actor_id, "task assignment actor id")
        expected_owner_agent_id = _optional_id(expected_owner_agent_id, "expected task owner agent id")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise ContractError(f"task not found: {task_id}")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            _require_agent_workspace(connection, row["workspace_id"], owner_agent_id)
            current = row["owner_agent_id"]
            if expected_owner_agent_id is not None and current != expected_owner_agent_id:
                raise PersistenceConflictError("task assignment version is stale")
            if current is not None and current != owner_agent_id:
                raise PersistenceConflictError("task already has another active owner")
            connection.execute(
                "UPDATE tasks SET owner_agent_id = ?, updated_at = ? WHERE task_id = ?",
                (owner_agent_id, _utc_now(), task_id),
            )
        return self.get_task(task_id) or {}

    def claim_task(self, task_id: str, agent_id: str) -> dict[str, Any]:
        """Claim an unassigned open task with one compare-and-set update."""
        task_id = _required_id(task_id, "task id")
        agent_id = _required_id(agent_id, "task agent id")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT workspace_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise ContractError(f"task not found: {task_id}")
            _require_agent_workspace(connection, row["workspace_id"], agent_id)
            cursor = connection.execute(
                """
                UPDATE tasks SET owner_agent_id = ?, updated_at = ?
                WHERE task_id = ? AND status = 'open' AND owner_agent_id IS NULL
                """,
                (agent_id, _utc_now(), task_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceConflictError("task is already assigned or not claimable")
        return self.get_task(task_id) or {}

    def add_watcher(self, task_id: str, actor_id: str) -> dict[str, Any]:
        task_id = _required_id(task_id, "task id")
        actor_id = _required_id(actor_id, "task watcher actor id")
        with self.database.transaction() as connection:
            task = connection.execute("SELECT workspace_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise ContractError(f"task not found: {task_id}")
            _require_workspace_actor(connection, task["workspace_id"], actor_id)
            connection.execute(
                """
                INSERT INTO task_watchers (task_id, actor_id, added_at, removed_at)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(task_id, actor_id) DO UPDATE SET removed_at = NULL
                """,
                (task_id, actor_id, _utc_now()),
            )
            row = connection.execute(
                "SELECT * FROM task_watchers WHERE task_id = ? AND actor_id = ?",
                (task_id, actor_id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def remove_watcher(self, task_id: str, actor_id: str) -> bool:
        task_id = _required_id(task_id, "task id")
        actor_id = _required_id(actor_id, "task watcher actor id")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_watchers SET removed_at = ? WHERE task_id = ? AND actor_id = ? AND removed_at IS NULL",
                (_utc_now(), task_id, actor_id),
            )
        return cursor.rowcount == 1

    def add_label(self, task_id: str, label: str) -> dict[str, Any]:
        task_id = _required_id(task_id, "task id")
        label = _required_kind(label, "task label")
        with self.database.transaction() as connection:
            task = connection.execute("SELECT workspace_id FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise ContractError(f"task not found: {task_id}")
            try:
                connection.execute(
                    "INSERT INTO task_labels (task_id, label, created_at) VALUES (?, ?, ?)",
                    (task_id, label, _utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("task label already exists") from error
            row = connection.execute(
                "SELECT * FROM task_labels WHERE task_id = ? AND label = ?", (task_id, label)
            ).fetchone()
        assert row is not None
        return dict(row)

    def remove_label(self, task_id: str, label: str) -> bool:
        task_id = _required_id(task_id, "task id")
        label = _required_kind(label, "task label")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM task_labels WHERE task_id = ? AND label = ?", (task_id, label)
            )
        return cursor.rowcount == 1

    def transition_task(
        self,
        task_id: str,
        target: TaskStatus | str,
        *,
        actor_id: str,
        authority: TransitionAuthority | str,
        protected_review: bool = True,
    ) -> dict[str, Any]:
        task_id = _required_id(task_id, "task id")
        actor_id = _required_id(actor_id, "task transition actor id")
        target = _as_enum(TaskStatus, target, "task status")
        authority = _as_enum(TransitionAuthority, authority, "transition authority")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise ContractError(f"task not found: {task_id}")
            dependency_rows = connection.execute(
                """
                SELECT d.depends_on_task_id, t.status
                FROM task_dependencies AS d JOIN tasks AS t ON t.task_id = d.depends_on_task_id
                WHERE d.task_id = ?
                """,
                (task_id,),
            ).fetchall()
            owner_actor = None
            if row["owner_agent_id"] is not None:
                owner_actor = connection.execute(
                    "SELECT actor_id FROM agent_installations WHERE agent_id = ?",
                    (row["owner_agent_id"],),
                ).fetchone()
            require_task_transition(
                TaskStatus(row["status"]),
                target,
                authority,
                actor_id=actor_id,
                owner_actor_id=owner_actor["actor_id"] if owner_actor is not None else None,
                reviewer_actor_id=row["reviewer_actor_id"],
                dependency_states={item["depends_on_task_id"]: TaskStatus(item["status"]) for item in dependency_rows},
                protected_review=protected_review,
            )
            now = _utc_now()
            connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    updated_at = ?,
                    canceled_at = CASE
                        WHEN ? = 'canceled' THEN ?
                        WHEN ? = 'open' THEN NULL
                        ELSE canceled_at
                    END
                WHERE task_id = ?
                """,
                (target.value, now, target.value, now, target.value, task_id),
            )
            updated = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def cancel_descendants(self, task_id: str) -> list[dict[str, Any]]:
        """Cancel active child tasks atomically, leaving done history untouched."""

        task_id = _required_id(task_id, "task id")
        now = _utc_now()
        canceled: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            root = connection.execute(
                "SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if root is None:
                raise ContractError(f"task not found: {task_id}")
            rows = connection.execute(
                """
                WITH RECURSIVE descendants(task_id) AS (
                    SELECT task_id FROM tasks WHERE parent_task_id = ?
                    UNION ALL
                    SELECT child.task_id
                    FROM tasks AS child
                    JOIN descendants AS parent ON child.parent_task_id = parent.task_id
                )
                SELECT * FROM tasks
                WHERE task_id IN (SELECT task_id FROM descendants)
                ORDER BY created_at, task_id
                """,
                (task_id,),
            ).fetchall()
            for row in rows:
                current = TaskStatus(row["status"])
                if current in {TaskStatus.DONE, TaskStatus.CANCELED}:
                    continue
                require_task_transition(
                    current,
                    TaskStatus.CANCELED,
                    TransitionAuthority.TASK_SERVICE,
                    actor_id=row["created_by_actor_id"],
                    owner_actor_id=None,
                    reviewer_actor_id=row["reviewer_actor_id"],
                    dependency_states={},
                    protected_review=False,
                )
                connection.execute(
                    """
                    UPDATE tasks
                    SET status = 'canceled', updated_at = ?, canceled_at = ?
                    WHERE task_id = ?
                    """,
                    (now, now, row["task_id"]),
                )
                updated = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (row["task_id"],)
                ).fetchone()
                assert updated is not None
                canceled.append(dict(updated))
        return canceled

    def reopen_descendants(self, task_id: str) -> list[dict[str, Any]]:
        """Reopen canceled descendants when a parent task is explicitly reopened."""

        task_id = _required_id(task_id, "task id")
        now = _utc_now()
        reopened: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            root = connection.execute(
                "SELECT task_id FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if root is None:
                raise ContractError(f"task not found: {task_id}")
            rows = connection.execute(
                """
                WITH RECURSIVE descendants(task_id) AS (
                    SELECT task_id FROM tasks WHERE parent_task_id = ?
                    UNION ALL
                    SELECT child.task_id
                    FROM tasks AS child
                    JOIN descendants AS parent ON child.parent_task_id = parent.task_id
                )
                SELECT * FROM tasks
                WHERE task_id IN (SELECT task_id FROM descendants)
                  AND status = 'canceled'
                ORDER BY created_at, task_id
                """,
                (task_id,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE tasks SET status = 'open', canceled_at = NULL, updated_at = ? WHERE task_id = ?",
                    (now, row["task_id"]),
                )
                updated = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (row["task_id"],)
                ).fetchone()
                assert updated is not None
                reopened.append(dict(updated))
        return reopened


class HandoffRepository:
    """Durable structured handoffs; free-form agent chat cannot substitute for one."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create(self, handoff: HandoffContract) -> dict[str, Any]:
        if not isinstance(handoff, HandoffContract):
            raise ContractError("handoff must be a HandoffContract")
        values = handoff.to_dict()
        with self.database.transaction() as connection:
            _require_workspace_actor(
                connection, handoff.workspace_id, handoff.requester.id, allow_system=True
            )
            task = connection.execute(
                "SELECT workspace_id FROM tasks WHERE task_id = ?", (handoff.task_id,)
            ).fetchone()
            if task is None or task["workspace_id"] != handoff.workspace_id:
                raise ContractError("handoff task is missing or belongs to another workspace")
            _require_agent_workspace(connection, handoff.workspace_id, handoff.sender_agent_id)
            _require_agent_workspace(connection, handoff.workspace_id, handoff.recipient_agent_id)
            for artifact_id in handoff.artifact_ids:
                artifact = connection.execute(
                    "SELECT workspace_id FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                if artifact is None or artifact["workspace_id"] != handoff.workspace_id:
                    raise ContractError("handoff artifact is missing or belongs to another workspace")
            existing = connection.execute(
                "SELECT * FROM handoffs WHERE workspace_id = ? AND idempotency_key = ?",
                (handoff.workspace_id, handoff.idempotency_key),
            ).fetchone()
            if existing is not None:
                persisted = _handoff_row(existing)
                if _handoff_matches(persisted, handoff):
                    return persisted
                raise PersistenceConflictError("handoff idempotency key has a different payload")
            try:
                connection.execute(
                    """
                    INSERT INTO handoffs
                        (handoff_id, workspace_id, task_id, sender_agent_id,
                         recipient_agent_id, purpose, expected_output, requester_actor_id,
                         artifact_ids_json, budget_json, depth, agent_path_json,
                         idempotency_key, correlation_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        handoff.handoff_id,
                        handoff.workspace_id,
                        handoff.task_id,
                        handoff.sender_agent_id,
                        handoff.recipient_agent_id,
                        handoff.purpose,
                        handoff.expected_output,
                        handoff.requester.id,
                        _json(values["artifact_ids"]),
                        _json(values["budget"]),
                        handoff.depth,
                        _json(values["agent_path"]),
                        handoff.idempotency_key,
                        handoff.correlation_id,
                        handoff.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("handoff conflicts with an existing relation") from error
            row = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff.handoff_id,)
            ).fetchone()
        assert row is not None
        return _handoff_row(row)

    def get(self, handoff_id: str) -> Optional[dict[str, Any]]:
        handoff_id = _required_id(handoff_id, "handoff id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
        return _handoff_row(row) if row is not None else None

    def list_for_task(self, task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        task_id = _required_id(task_id, "task id")
        _require_page_limit(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM handoffs WHERE task_id = ? ORDER BY created_at, handoff_id LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [_handoff_row(row) for row in rows]

    def transition(self, handoff_id: str, status: str) -> dict[str, Any]:
        handoff_id = _required_id(handoff_id, "handoff id")
        status = _required_kind(status, "handoff status")
        if status not in {"pending", "accepted", "completed", "failed", "canceled"}:
            raise ContractError("handoff status is unsupported")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"handoff not found: {handoff_id}")
            if row["status"] in {"completed", "failed", "canceled"} and row["status"] != status:
                raise ContractError("terminal handoff cannot transition to a different status")
            connection.execute("UPDATE handoffs SET status = ? WHERE handoff_id = ?", (status, handoff_id))
            updated = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
        assert updated is not None
        return _handoff_row(updated)


class WorkRepository:
    """Durable WorkRequest, delivery, attempt, and lease persistence."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_request(
        self,
        request: WorkRequest,
        *,
        crew_run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(request, WorkRequest):
            raise ContractError("request must be a WorkRequest")
        crew_run_id = _optional_id(crew_run_id, "crew run id")
        values = request.to_dict()
        with self.database.transaction() as connection:
            _require_workspace_actor(
                connection,
                request.workspace_id,
                request.requester.id,
                allow_system=True,
            )
            for target_agent_id in request.target_agent_ids:
                _require_agent_workspace(connection, request.workspace_id, target_agent_id)
                installation = connection.execute(
                    "SELECT status FROM agent_installations WHERE agent_id = ?",
                    (target_agent_id,),
                ).fetchone()
                if installation is None:
                    raise ContractError(f"target agent installation is missing: {target_agent_id}")
                if installation["status"] in {
                    AgentStatus.PAUSED.value,
                    AgentStatus.ARCHIVED.value,
                }:
                    raise ContractError(
                        f"target agent cannot receive work while {installation['status']}: {target_agent_id}"
                    )
            if request.conversation_id is not None:
                conversation = connection.execute(
                    "SELECT workspace_id FROM conversations WHERE conversation_id = ?",
                    (request.conversation_id,),
                ).fetchone()
                if conversation is None or conversation["workspace_id"] != request.workspace_id:
                    raise ContractError("work request conversation is missing or belongs to another workspace")
            if request.thread_id is not None:
                thread = connection.execute(
                    """
                    SELECT c.workspace_id FROM threads AS t
                    JOIN conversations AS c ON c.conversation_id = t.conversation_id
                    WHERE t.thread_id = ?
                    """,
                    (request.thread_id,),
                ).fetchone()
                if thread is None or thread["workspace_id"] != request.workspace_id:
                    raise ContractError("work request thread is missing or belongs to another workspace")
            if request.task_id is not None:
                task = connection.execute(
                    "SELECT workspace_id FROM tasks WHERE task_id = ?", (request.task_id,)
                ).fetchone()
                if task is None or task["workspace_id"] != request.workspace_id:
                    raise ContractError("work request task is missing or belongs to another workspace")
            if crew_run_id is not None:
                crew = connection.execute(
                    "SELECT workspace_id, status FROM crew_runs WHERE crew_run_id = ?",
                    (crew_run_id,),
                ).fetchone()
                if crew is None or crew["workspace_id"] != request.workspace_id:
                    raise ContractError("work request crew run is missing or belongs to another workspace")
                crew_targets = {
                    row["agent_id"]
                    for row in connection.execute(
                        "SELECT agent_id FROM crew_run_agents WHERE crew_run_id = ?",
                        (crew_run_id,),
                    ).fetchall()
                }
                if any(agent_id not in crew_targets for agent_id in request.target_agent_ids):
                    raise ContractError("work request target is not a member of the crew run")
            for artifact_id in request.input_artifact_ids:
                artifact = connection.execute(
                    "SELECT workspace_id FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                if artifact is None or artifact["workspace_id"] != request.workspace_id:
                    raise ContractError("work request artifact is missing or belongs to another workspace")
            existing = connection.execute(
                "SELECT * FROM work_requests WHERE workspace_id = ? AND idempotency_key = ?",
                (request.workspace_id, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                persisted = _work_request_row(existing, connection)
                if _request_matches(persisted, request, crew_run_id=crew_run_id):
                    return persisted
                raise PersistenceConflictError("work request idempotency key has a different payload")
            if crew_run_id is not None and crew["status"] != "active":
                raise ContractError("work request can only be added to an active crew run")
            try:
                connection.execute(
                    """
                    INSERT INTO work_requests
                        (request_id, workspace_id, crew_run_id, source_kind, source_ref_kind,
                         source_ref_id, requester_actor_id, conversation_id, thread_id,
                         task_id, instruction, expected_output, permission_scope_json,
                         budget_json, priority, idempotency_key, correlation_id,
                         causation_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.request_id,
                        request.workspace_id,
                        crew_run_id,
                        request.source.value,
                        request.source_ref.kind,
                        request.source_ref.id,
                        request.requester.id,
                        request.conversation_id,
                        request.thread_id,
                        request.task_id,
                        request.instruction,
                        request.expected_output,
                        _json(values["permission_scope"]),
                        _json(values["budget"]),
                        request.priority,
                        request.idempotency_key,
                        request.correlation_id,
                        request.causation_id,
                        request.created_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO work_usage (request_id, updated_at) VALUES (?, ?)",
                    (request.request_id, request.created_at),
                )
                for position, target_agent_id in enumerate(request.target_agent_ids):
                    connection.execute(
                        "INSERT INTO work_request_targets (request_id, agent_id, position) VALUES (?, ?, ?)",
                        (request.request_id, target_agent_id, position),
                    )
                for position, resource in enumerate(request.context_refs):
                    connection.execute(
                        """
                        INSERT INTO work_request_context_refs
                            (request_id, position, resource_kind, resource_id)
                        VALUES (?, ?, ?, ?)
                        """,
                        (request.request_id, position, resource.kind, resource.id),
                    )
                for position, artifact_id in enumerate(request.input_artifact_ids):
                    connection.execute(
                        """
                        INSERT INTO work_request_input_artifacts (request_id, position, artifact_id)
                        VALUES (?, ?, ?)
                        """,
                        (request.request_id, position, artifact_id),
                    )
                row = connection.execute(
                    "SELECT * FROM work_requests WHERE request_id = ?", (request.request_id,)
                ).fetchone()
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("work request references a missing relation") from error
        assert row is not None
        return _work_request_row(row, self.database._conn)

    def get_request(self, request_id: str) -> Optional[dict[str, Any]]:
        request_id = _required_id(request_id, "request id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            return _work_request_row(row, self.database._conn) if row is not None else None

    def list_requests(
        self,
        workspace_id: str,
        *,
        after_cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        workspace_id = _required_id(workspace_id, "workspace id")
        _require_page_limit(limit)
        cursor_created_at, cursor_request_id = _request_cursor(after_cursor)
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if cursor_created_at is not None:
            where.append("(created_at > ? OR (created_at = ? AND request_id > ?))")
            params.extend((cursor_created_at, cursor_created_at, cursor_request_id))
        params.append(limit + 1)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM work_requests WHERE {' AND '.join(where)} ORDER BY created_at, request_id LIMIT ?",
                params,
            ).fetchall()
            requests = [_work_request_row(row, self.database._conn) for row in rows[:limit]]
        has_more = len(rows) > limit
        next_cursor = (
            _request_cursor_encode(requests[-1]["created_at"], requests[-1]["request_id"])
            if requests
            else after_cursor
        )
        return {"requests": requests, "next_cursor": next_cursor, "has_more": has_more}

    def create_deliveries(self, request_id: str) -> list[dict[str, Any]]:
        request_id = _required_id(request_id, "request id")
        now = _utc_now()
        with self.database.transaction() as connection:
            request = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ContractError(f"work request not found: {request_id}")
            target_rows = connection.execute(
                "SELECT agent_id FROM work_request_targets WHERE request_id = ? ORDER BY position",
                (request_id,),
            ).fetchall()
            for target in target_rows:
                target_agent_id = target["agent_id"]
                idempotency_key = f"{request_id}:{target_agent_id}"
                existing = connection.execute(
                    "SELECT * FROM deliveries WHERE workspace_id = ? AND target_agent_id = ? AND idempotency_key = ?",
                    (request["workspace_id"], target_agent_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    continue
                try:
                    connection.execute(
                        """
                        INSERT INTO deliveries
                            (delivery_id, request_id, workspace_id, target_agent_id, status,
                             idempotency_key, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_domain_id(DomainIdPrefix.DELIVERY),
                            request_id,
                            request["workspace_id"],
                            target_agent_id,
                            DeliveryStatus.PENDING.value,
                            idempotency_key,
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise PersistenceConflictError("delivery references a missing work target") from error
            rows = connection.execute(
                "SELECT * FROM deliveries WHERE request_id = ? ORDER BY created_at, delivery_id",
                (request_id,),
            ).fetchall()
        return [_delivery_row(row) for row in rows]

    def create_crew_run(
        self,
        crew_run_id: str,
        workspace_id: str,
        requested_by_actor_id: str,
        correlation_id: str,
        *,
        root_task_id: Optional[str] = None,
        agent_ids: Sequence[str] = (),
        agent_roles: Optional[Mapping[str, str]] = None,
    ) -> dict[str, Any]:
        crew_run_id = _required_id(crew_run_id, "crew run id")
        workspace_id = _required_id(workspace_id, "workspace id")
        requested_by_actor_id = _required_id(requested_by_actor_id, "requester actor id")
        correlation_id = _required_id(correlation_id, "crew run correlation id")
        root_task_id = _optional_id(root_task_id, "root task id")
        normalized_agents = tuple(
            dict.fromkeys(_required_id(agent_id, "crew agent id") for agent_id in agent_ids)
        )
        if len(normalized_agents) > 32:
            raise ContractError("a crew run cannot contain more than 32 agents")
        roles = dict(agent_roles or {})
        unknown_roles = set(roles) - set(normalized_agents)
        if unknown_roles:
            raise ContractError("crew roles contain an agent that is not in the crew")
        for agent_id, role in roles.items():
            if not str(role).strip() or len(str(role).strip()) > 80:
                raise ContractError(f"invalid crew role for agent: {agent_id}")
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, requested_by_actor_id, allow_system=True)
            if root_task_id is not None:
                task = connection.execute(
                    "SELECT workspace_id FROM tasks WHERE task_id = ?", (root_task_id,)
                ).fetchone()
                if task is None or task["workspace_id"] != workspace_id:
                    raise ContractError("root task is missing or belongs to another workspace")
            for agent_id in normalized_agents:
                _require_agent_workspace(connection, workspace_id, agent_id)
                installation = connection.execute(
                    "SELECT status FROM agent_installations WHERE agent_id = ?", (agent_id,)
                ).fetchone()
                if installation is None or installation["status"] in {
                    AgentStatus.PAUSED.value,
                    AgentStatus.ARCHIVED.value,
                }:
                    raise ContractError(f"crew agent cannot receive work: {agent_id}")
            existing = connection.execute(
                "SELECT * FROM crew_runs WHERE crew_run_id = ?", (crew_run_id,)
            ).fetchone()
            if existing is not None:
                existing_agents = tuple(
                    row["agent_id"]
                    for row in connection.execute(
                        "SELECT agent_id FROM crew_run_agents WHERE crew_run_id = ? ORDER BY position",
                        (crew_run_id,),
                    ).fetchall()
                )
                existing_roles = {
                    row["agent_id"]: row["crew_role"]
                    for row in connection.execute(
                        "SELECT agent_id, crew_role FROM crew_run_agents WHERE crew_run_id = ?",
                        (crew_run_id,),
                    ).fetchall()
                }
                requested_roles = {
                    agent_id: str(roles.get(agent_id, "worker")).strip()
                    for agent_id in normalized_agents
                }
                if (
                    existing["workspace_id"] == workspace_id
                    and existing["root_task_id"] == root_task_id
                    and existing["requested_by_actor_id"] == requested_by_actor_id
                    and existing["correlation_id"] == correlation_id
                    and existing_agents == normalized_agents
                    and existing_roles == requested_roles
                ):
                    row = existing
                else:
                    raise PersistenceConflictError("crew run conflicts with an existing identity")
            else:
                try:
                    connection.execute(
                        """
                        INSERT INTO crew_runs
                            (crew_run_id, workspace_id, root_task_id, requested_by_actor_id,
                             status, correlation_id, created_at)
                        VALUES (?, ?, ?, ?, 'active', ?, ?)
                        """,
                        (crew_run_id, workspace_id, root_task_id, requested_by_actor_id, correlation_id, _utc_now()),
                    )
                    for position, agent_id in enumerate(normalized_agents):
                        connection.execute(
                            """
                            INSERT INTO crew_run_agents
                                (crew_run_id, agent_id, crew_role, position)
                            VALUES (?, ?, ?, ?)
                            """,
                            (crew_run_id, agent_id, str(roles.get(agent_id, "worker")).strip(), position),
                        )
                except sqlite3.IntegrityError as error:
                    raise PersistenceConflictError("crew run conflicts with an existing relation") from error
                row = connection.execute("SELECT * FROM crew_runs WHERE crew_run_id = ?", (crew_run_id,)).fetchone()
        assert row is not None
        return self.get_crew_run(crew_run_id) or dict(row)

    def get_crew_run(self, crew_run_id: str) -> Optional[dict[str, Any]]:
        crew_run_id = _required_id(crew_run_id, "crew run id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM crew_runs WHERE crew_run_id = ?", (crew_run_id,)
            ).fetchone()
            if row is None:
                return None
            agents = self.database._conn.execute(
                """
                SELECT cra.agent_id, cra.crew_role, cra.position,
                       a.actor_id, a.display_name, a.handle,
                       i.status AS agent_status
                FROM crew_run_agents AS cra
                JOIN agent_installations AS i ON i.agent_id = cra.agent_id
                JOIN actors AS a ON a.actor_id = i.actor_id
                WHERE cra.crew_run_id = ?
                ORDER BY cra.position
                """,
                (crew_run_id,),
            ).fetchall()
        result = dict(row)
        result["agents"] = [dict(agent) for agent in agents]
        return result

    def list_crew_runs(
        self,
        workspace_id: str,
        *,
        statuses: Sequence[str] = (),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "workspace id")
        _require_page_limit(limit)
        normalized = tuple(_required_kind(status, "crew run status") for status in statuses)
        if any(status not in {"active", "canceled", "completed", "failed"} for status in normalized):
            raise ContractError("crew run status is unsupported")
        where = "workspace_id = ?"
        params: list[Any] = [workspace_id]
        if normalized:
            where += " AND status IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT crew_run_id FROM crew_runs WHERE {where} ORDER BY created_at DESC, crew_run_id LIMIT ?",
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            crew = self.get_crew_run(row["crew_run_id"])
            if crew is not None:
                result.append(crew)
        return result

    def transition_crew_run(self, crew_run_id: str, status: str) -> dict[str, Any]:
        crew_run_id = _required_id(crew_run_id, "crew run id")
        status = _required_kind(status, "crew run status")
        allowed = {"active", "canceled", "completed", "failed"}
        if status not in allowed:
            raise ContractError("crew run status is unsupported")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM crew_runs WHERE crew_run_id = ?", (crew_run_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"crew run not found: {crew_run_id}")
            current = row["status"]
            if current == status:
                return self.get_crew_run(crew_run_id) or dict(row)
            if current != "active":
                raise ContractError("terminal crew run cannot transition to a different status")
            if status == "active":
                raise ContractError("crew run is already active")
            if status in {"completed", "failed"}:
                active = connection.execute(
                    "SELECT 1 FROM work_requests WHERE crew_run_id = ? AND status = 'active' LIMIT 1",
                    (crew_run_id,),
                ).fetchone()
                if active is not None:
                    raise ContractError("crew run still has active work")
            finished_at = _utc_now()
            connection.execute(
                "UPDATE crew_runs SET status = ?, finished_at = ? WHERE crew_run_id = ?",
                (status, finished_at, crew_run_id),
            )
            updated = connection.execute(
                "SELECT * FROM crew_runs WHERE crew_run_id = ?", (crew_run_id,)
            ).fetchone()
        assert updated is not None
        return self.get_crew_run(crew_run_id) or dict(updated)

    def create_run(
        self,
        run_id: str,
        delivery_id: str,
        request_id: str,
        workspace_id: str,
        agent_id: str,
        agent_revision_id: str,
        sandbox_id: str,
        *,
        budget: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        values = (
            _required_id(run_id, "run id"),
            _required_id(delivery_id, "delivery id"),
            _required_id(request_id, "request id"),
            _required_id(workspace_id, "workspace id"),
            _required_id(agent_id, "agent id"),
            _required_id(agent_revision_id, "agent revision id"),
            _required_id(sandbox_id, "sandbox id"),
            _json_object(budget, "run budget"),
        )
        with self.database.transaction() as connection:
            _require_agent_workspace(connection, workspace_id, agent_id)
            request = connection.execute(
                "SELECT workspace_id FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            delivery = connection.execute(
                "SELECT request_id, workspace_id, target_agent_id FROM deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            revision = connection.execute(
                """
                SELECT r.agent_definition_id, d.workspace_id
                FROM agent_revisions AS r JOIN agent_definitions AS d
                  ON d.agent_definition_id = r.agent_definition_id
                WHERE r.agent_revision_id = ?
                """,
                (agent_revision_id,),
            ).fetchone()
            sandbox = connection.execute(
                "SELECT workspace_id, agent_id FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone()
            if request is None or request["workspace_id"] != workspace_id:
                raise ContractError("run request is missing or belongs to another workspace")
            if (
                delivery is None
                or delivery["request_id"] != request_id
                or delivery["workspace_id"] != workspace_id
                or delivery["target_agent_id"] != agent_id
            ):
                raise ContractError("run delivery does not match its request and agent")
            if revision is None or revision["workspace_id"] != workspace_id:
                raise ContractError("run revision is missing or belongs to another workspace")
            if sandbox is None or sandbox["workspace_id"] != workspace_id or sandbox["agent_id"] != agent_id:
                raise ContractError("run sandbox does not belong to its agent and workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO runs
                        (run_id, delivery_id, request_id, workspace_id, agent_id,
                         agent_revision_id, sandbox_id, status, budget_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (*values[:7], values[7], _utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("run conflicts with an existing delivery") from error
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert row is not None
        return _run_row(row)

    def dispatch_run(
        self,
        run_id: str,
        delivery_id: str,
        request_id: str,
        workspace_id: str,
        agent_id: str,
        agent_revision_id: str,
        sandbox_id: str,
        lease_token: str,
        *,
        budget: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a run and move its leased delivery to dispatched atomically.

        The lease is deliberately retained until terminal settlement.  If the
        worker disappears after this transaction, lease recovery can time out
        the active run and put the delivery back into the retry queue.
        """

        values = (
            _required_id(run_id, "run id"),
            _required_id(delivery_id, "delivery id"),
            _required_id(request_id, "request id"),
            _required_id(workspace_id, "workspace id"),
            _required_id(agent_id, "agent id"),
            _required_id(agent_revision_id, "agent revision id"),
            _required_id(sandbox_id, "sandbox id"),
            _require_text(lease_token, "lease token"),
            _json_object(budget, "run budget"),
        )
        now = now or _utc_now()
        _require_timestamp(now, "dispatch timestamp")
        with self.database.transaction() as connection:
            _require_agent_workspace(connection, workspace_id, agent_id)
            lease = connection.execute(
                "SELECT expires_at FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (delivery_id, lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            request = connection.execute(
                "SELECT workspace_id, status FROM work_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            delivery = connection.execute(
                "SELECT request_id, workspace_id, target_agent_id, status, attempt_count FROM deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            revision = connection.execute(
                """
                SELECT r.agent_definition_id, d.workspace_id
                FROM agent_revisions AS r JOIN agent_definitions AS d
                  ON d.agent_definition_id = r.agent_definition_id
                WHERE r.agent_revision_id = ?
                """,
                (agent_revision_id,),
            ).fetchone()
            sandbox = connection.execute(
                "SELECT workspace_id, agent_id FROM sandboxes WHERE sandbox_id = ?",
                (sandbox_id,),
            ).fetchone()
            if request is None or request["workspace_id"] != workspace_id or request["status"] != "active":
                raise ContractError("run request is missing, canceled, or belongs to another workspace")
            if (
                delivery is None
                or delivery["request_id"] != request_id
                or delivery["workspace_id"] != workspace_id
                or delivery["target_agent_id"] != agent_id
                or delivery["status"] != DeliveryStatus.LEASED.value
            ):
                raise ContractError("run delivery is not leased by its target agent")
            if revision is None or revision["workspace_id"] != workspace_id:
                raise ContractError("run revision is missing or belongs to another workspace")
            if sandbox is None or sandbox["workspace_id"] != workspace_id or sandbox["agent_id"] != agent_id:
                raise ContractError("run sandbox does not belong to its agent and workspace")
            active_run = connection.execute(
                "SELECT run_id FROM runs WHERE delivery_id = ? AND status IN ('queued', 'running', 'waiting_input', 'waiting_approval') LIMIT 1",
                (delivery_id,),
            ).fetchone()
            if active_run is not None:
                raise PersistenceConflictError("delivery already has an active run")
            try:
                connection.execute(
                    """
                    INSERT INTO runs
                        (run_id, delivery_id, request_id, workspace_id, agent_id,
                         agent_revision_id, sandbox_id, status, budget_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (values[0], values[1], values[2], values[3], values[4], values[5], values[6], values[8], now),
                )
                attempt_number = int(delivery["attempt_count"]) + 1
                connection.execute(
                    """
                    INSERT INTO delivery_attempts
                        (delivery_attempt_id, delivery_id, attempt_number, outcome,
                         started_at)
                    VALUES (?, ?, ?, 'started', ?)
                    """,
                    (new_domain_id(DomainIdPrefix.DELIVERY_ATTEMPT), delivery_id, attempt_number, now),
                )
                connection.execute(
                    "UPDATE deliveries SET status = 'dispatched', attempt_count = ?, updated_at = ? WHERE delivery_id = ?",
                    (attempt_number, now, delivery_id),
                )
                row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("run or delivery attempt conflicts with existing state") from error
        assert row is not None
        return _run_row(row)

    def list_ready_deliveries(
        self,
        agent_id: str,
        *,
        now: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        agent_id = _required_id(agent_id, "agent id")
        now = now or _utc_now()
        _require_timestamp(now, "queue timestamp")
        _require_page_limit(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT d.*, r.source_kind, r.instruction, r.priority AS request_priority,
                       r.status AS request_status,
                       (
                           SELECT rr.run_id
                           FROM runs AS rr
                           JOIN approvals AS aa ON aa.run_id = rr.run_id
                           WHERE rr.delivery_id = d.delivery_id
                             AND rr.status = 'waiting_approval'
                             AND aa.status = 'approved'
                             AND (aa.expires_at IS NULL OR aa.expires_at > ?)
                           ORDER BY rr.created_at DESC, rr.run_id DESC
                           LIMIT 1
                       ) AS resume_run_id,
                       (
                           SELECT aa.approval_id
                           FROM runs AS rr
                           JOIN approvals AS aa ON aa.run_id = rr.run_id
                           WHERE rr.delivery_id = d.delivery_id
                             AND rr.status = 'waiting_approval'
                             AND aa.status = 'approved'
                             AND (aa.expires_at IS NULL OR aa.expires_at > ?)
                           ORDER BY rr.created_at DESC, aa.created_at DESC, aa.approval_id DESC
                           LIMIT 1
                       ) AS resume_approval_id,
                       (
                           SELECT rr.run_id
                           FROM runs AS rr
                           JOIN takeover_requests AS tr ON tr.run_id = rr.run_id
                           WHERE rr.delivery_id = d.delivery_id
                             AND rr.status = 'waiting_input'
                             AND tr.status = 'approved'
                             AND tr.run_id = rr.run_id
                           ORDER BY rr.created_at DESC, rr.run_id DESC
                           LIMIT 1
                       ) AS resume_takeover_run_id,
                       (
                           SELECT tr.takeover_request_id
                           FROM runs AS rr
                           JOIN takeover_requests AS tr ON tr.run_id = rr.run_id
                           WHERE rr.delivery_id = d.delivery_id
                             AND rr.status = 'waiting_input'
                             AND tr.status = 'approved'
                             AND tr.run_id = rr.run_id
                           ORDER BY rr.created_at DESC, tr.created_at DESC, tr.takeover_request_id DESC
                           LIMIT 1
                       ) AS resume_takeover_id
                FROM deliveries AS d
                JOIN work_requests AS r ON r.request_id = d.request_id
                WHERE d.target_agent_id = ?
                  AND r.status = 'active'
                  AND r.budget_paused_at IS NULL
                  AND (
                      (
                          d.status IN ('pending', 'retry_wait')
                          AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
                      )
                      OR (
                          d.status = 'dispatched'
                          AND EXISTS (
                              SELECT 1
                              FROM runs AS rr
                              JOIN approvals AS aa ON aa.run_id = rr.run_id
                              WHERE rr.delivery_id = d.delivery_id
                                AND rr.status = 'waiting_approval'
                                AND aa.status = 'approved'
                                AND (aa.expires_at IS NULL OR aa.expires_at > ?)
                          )
                      )
                      OR (
                          d.status = 'dispatched'
                          AND EXISTS (
                              SELECT 1
                              FROM runs AS rr
                              JOIN takeover_requests AS tr ON tr.run_id = rr.run_id
                              WHERE rr.delivery_id = d.delivery_id
                                AND rr.status = 'waiting_input'
                                AND tr.status = 'approved'
                                AND tr.run_id = rr.run_id
                          )
                      )
                  )
                ORDER BY
                  CASE WHEN r.source_kind IN ('direct_message', 'group_message') THEN 1 ELSE 0 END DESC,
                  r.priority DESC,
                  d.created_at,
                  d.delivery_id
                LIMIT ?
                """,
                (now, now, agent_id, now, now, limit),
            ).fetchall()
        return [_delivery_row(row) for row in rows]

    def agent_capacity_available(self, agent_id: str) -> bool:
        agent_id = _required_id(agent_id, "agent id")
        with self.database._lock:
            row = self.database._conn.execute(
                """
                SELECT i.max_concurrency,
                       COUNT(r.run_id) AS active_runs
                FROM agent_installations AS i
                LEFT JOIN runs AS r
                  ON r.agent_id = i.agent_id
                 AND r.status IN ('queued', 'running')
                WHERE i.agent_id = ?
                GROUP BY i.agent_id, i.max_concurrency
                """,
                (agent_id,),
            ).fetchone()
        return row is not None and int(row["active_runs"]) < int(row["max_concurrency"])

    def agent_can_claim(self, agent_id: str) -> bool:
        """Return whether the installation is currently allowed to execute work.

        Pending deliveries are intentionally retained while an agent is offline,
        provisioning, errored, or paused.  Only an installation that can actually
        start a run may acquire a lease; archived agents are terminal and never
        re-enter the execution path.
        """
        agent_id = _required_id(agent_id, "agent id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT status FROM agent_installations WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        return row is not None and row["status"] in {
            AgentStatus.READY.value,
            AgentStatus.WORKING.value,
            AgentStatus.WAITING.value,
        }

    def recover_expired_leases(self, *, now: Optional[str] = None) -> list[str]:
        now = now or _utc_now()
        _require_timestamp(now, "lease recovery timestamp")
        recovered: list[str] = []
        with self.database.transaction() as connection:
            leases = connection.execute(
                "SELECT delivery_id FROM delivery_leases WHERE expires_at <= ? ORDER BY delivery_id",
                (now,),
            ).fetchall()
            for lease in leases:
                delivery_id = lease["delivery_id"]
                row = connection.execute(
                    "SELECT status FROM deliveries WHERE delivery_id = ?", (delivery_id,)
                ).fetchone()
                if row is None:
                    connection.execute("DELETE FROM delivery_leases WHERE delivery_id = ?", (delivery_id,))
                    continue
                if row["status"] == DeliveryStatus.LEASED.value:
                    connection.execute(
                        "UPDATE deliveries SET status = 'pending', updated_at = ? WHERE delivery_id = ?",
                        (now, delivery_id),
                    )
                elif row["status"] == DeliveryStatus.DISPATCHED.value:
                    latest_run = connection.execute(
                        """
                        SELECT run_id, status FROM runs
                        WHERE delivery_id = ?
                        ORDER BY created_at DESC, run_id DESC LIMIT 1
                        """,
                        (delivery_id,),
                    ).fetchone()
                    if latest_run is not None and latest_run["status"] == RunStatus.SUCCEEDED.value:
                        connection.execute(
                            "UPDATE deliveries SET status = 'acknowledged', next_attempt_at = NULL, updated_at = ? WHERE delivery_id = ?",
                            (now, delivery_id),
                        )
                        connection.execute(
                            """
                            UPDATE delivery_attempts
                            SET outcome = 'accepted', finished_at = ?
                            WHERE delivery_id = ? AND attempt_number = (
                                SELECT MAX(attempt_number) FROM delivery_attempts WHERE delivery_id = ?
                            )
                            """,
                            (now, delivery_id, delivery_id),
                        )
                    else:
                        consequential_activity = False
                        if latest_run is not None:
                            consequential_activity = connection.execute(
                                """
                                SELECT 1 FROM run_events
                                WHERE run_id = ? AND event_type = 'run.consequential_started'
                                LIMIT 1
                                """,
                                (latest_run["run_id"],),
                            ).fetchone() is not None
                        recovery_status = (
                            DeliveryStatus.DEAD_LETTER.value
                            if consequential_activity
                            else DeliveryStatus.RETRY_WAIT.value
                        )
                        connection.execute(
                            "UPDATE deliveries SET status = ?, next_attempt_at = ?, updated_at = ? WHERE delivery_id = ?",
                            (
                                recovery_status,
                                None if consequential_activity else now,
                                now,
                                delivery_id,
                            ),
                        )
                        if latest_run is not None and latest_run["status"] in {
                            RunStatus.QUEUED.value,
                            RunStatus.RUNNING.value,
                            RunStatus.WAITING_INPUT.value,
                            RunStatus.WAITING_APPROVAL.value,
                        }:
                            current = RunStatus(latest_run["status"])
                            require_transition(current, RunStatus.TIMED_OUT, RUN_EDGES)
                            connection.execute(
                                "UPDATE runs SET status = 'timed_out', finished_at = ? WHERE run_id = ?",
                                (now, latest_run["run_id"]),
                            )
                            connection.execute(
                                """
                                UPDATE delivery_attempts
                                SET outcome = 'timed_out', finished_at = ?
                                WHERE delivery_id = ? AND attempt_number = (
                                    SELECT MAX(attempt_number) FROM delivery_attempts WHERE delivery_id = ?
                                )
                                """,
                                (now, delivery_id, delivery_id),
                            )
                connection.execute("DELETE FROM delivery_leases WHERE delivery_id = ?", (delivery_id,))
                recovered.append(delivery_id)
        return recovered

    def cancel_request(self, request_id: str, *, reason: str = "canceled by requester") -> dict[str, Any]:
        request_id = _required_id(request_id, "request id")
        reason = _require_text(reason, "cancellation reason")
        now = _utc_now()
        with self.database.transaction() as connection:
            request = connection.execute("SELECT * FROM work_requests WHERE request_id = ?", (request_id,)).fetchone()
            if request is None:
                raise ContractError(f"work request not found: {request_id}")
            # Completed work is immutable history.  Cancellation only applies to
            # active requests; a repeated cancel of an already-canceled request
            # remains an idempotent no-op.
            if request["status"] == "active":
                connection.execute(
                    "UPDATE work_requests SET status = 'canceled', canceled_at = ?, cancel_reason = ? WHERE request_id = ?",
                    (now, reason, request_id),
                )
                run_rows = connection.execute(
                    "SELECT run_id, status FROM runs WHERE request_id = ?", (request_id,)
                ).fetchall()
                for run in run_rows:
                    current = RunStatus(run["status"])
                    if current in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.WAITING_INPUT, RunStatus.WAITING_APPROVAL}:
                        require_transition(current, RunStatus.CANCELED, RUN_EDGES)
                        connection.execute(
                            "UPDATE runs SET status = 'canceled', finished_at = ? WHERE run_id = ?",
                            (now, run["run_id"]),
                        )
                connection.execute(
                    """
                    UPDATE deliveries
                    SET status = 'canceled', updated_at = ?, next_attempt_at = NULL
                    WHERE request_id = ? AND status IN ('pending', 'leased', 'dispatched', 'retry_wait')
                    """,
                    (now, request_id),
                )
                connection.execute(
                    "DELETE FROM delivery_leases WHERE delivery_id IN (SELECT delivery_id FROM deliveries WHERE request_id = ?)",
                    (request_id,),
                )
            updated = connection.execute("SELECT * FROM work_requests WHERE request_id = ?", (request_id,)).fetchone()
        assert updated is not None
        return _work_request_row(updated, self.database._conn)

    def cancel_delivery(self, delivery_id: str, *, reason: str = "canceled by requester") -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "delivery id")
        reason = _require_text(reason, "cancellation reason")
        now = _utc_now()
        with self.database.transaction() as connection:
            delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if delivery is None:
                raise ContractError(f"delivery not found: {delivery_id}")
            run_rows = connection.execute(
                """
                SELECT run_id, status FROM runs
                WHERE delivery_id = ? AND status IN ('queued', 'running', 'waiting_input', 'waiting_approval')
                """,
                (delivery_id,),
            ).fetchall()
            for run in run_rows:
                current = RunStatus(run["status"])
                require_transition(current, RunStatus.CANCELED, RUN_EDGES)
                connection.execute(
                    "UPDATE runs SET status = 'canceled', finished_at = ? WHERE run_id = ?",
                    (now, run["run_id"]),
                )
            if delivery["status"] not in {
                DeliveryStatus.ACKNOWLEDGED.value,
                DeliveryStatus.DEAD_LETTER.value,
                DeliveryStatus.CANCELED.value,
            }:
                connection.execute(
                    "UPDATE deliveries SET status = 'canceled', next_attempt_at = NULL, updated_at = ? WHERE delivery_id = ?",
                    (now, delivery_id),
                )
                connection.execute(
                    "DELETE FROM delivery_leases WHERE delivery_id = ?", (delivery_id,)
                )
                connection.execute(
                    """
                    UPDATE delivery_attempts
                    SET outcome = 'canceled', error_detail = ?, finished_at = ?
                    WHERE delivery_id = ? AND attempt_number = (
                        SELECT MAX(attempt_number) FROM delivery_attempts WHERE delivery_id = ?
                    )
                    """,
                    (reason, now, delivery_id, delivery_id),
                )
            updated = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        assert updated is not None
        return _delivery_row(updated, connection=self.database._conn)

    def cancel_run(self, run_id: str, *, reason: str = "canceled by requester") -> dict[str, Any]:
        run_id = _required_id(run_id, "run id")
        reason = _require_text(reason, "cancellation reason")
        now = _utc_now()
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise ContractError(f"run not found: {run_id}")
            current = RunStatus(run["status"])
            if current in {
                RunStatus.QUEUED,
                RunStatus.RUNNING,
                RunStatus.WAITING_INPUT,
                RunStatus.WAITING_APPROVAL,
            }:
                require_transition(current, RunStatus.CANCELED, RUN_EDGES)
                connection.execute(
                    "UPDATE runs SET status = 'canceled', finished_at = ? WHERE run_id = ?",
                    (now, run_id),
                )
                connection.execute(
                    """
                    UPDATE deliveries SET status = 'canceled', next_attempt_at = NULL, updated_at = ?
                    WHERE delivery_id = ? AND status IN ('leased', 'dispatched', 'pending', 'retry_wait')
                    """,
                    (now, run["delivery_id"]),
                )
                connection.execute(
                    "DELETE FROM delivery_leases WHERE delivery_id = ?", (run["delivery_id"],)
                )
                connection.execute(
                    """
                    UPDATE delivery_attempts
                    SET outcome = 'canceled', error_detail = ?, finished_at = ?
                    WHERE delivery_id = ? AND attempt_number = (
                        SELECT MAX(attempt_number) FROM delivery_attempts WHERE delivery_id = ?
                    )
                    """,
                    (reason, now, run["delivery_id"], run["delivery_id"]),
                )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (run["delivery_id"],)
            ).fetchone()
        assert updated is not None and delivery is not None
        return {"run": _run_row(updated), "delivery": _delivery_row(delivery, connection=self.database._conn)}

    def list_request_ids_for_scope(self, scope_kind: str, scope_id: str) -> list[str]:
        scope_kind = _required_kind(scope_kind, "cancellation scope kind")
        scope_id = _required_id(scope_id, "cancellation scope id")
        if scope_kind == "conversation":
            where, params = "conversation_id = ?", (scope_id,)
        elif scope_kind == "task":
            # A task cancellation owns its whole parent/child work graph.  The
            # recursive query keeps descendant dispatches from surviving merely
            # because they were submitted against a child task.
            where = "task_id IN (SELECT task_id FROM task_tree)"
            params = (scope_id,)
        elif scope_kind == "crew_run":
            where, params = "crew_run_id = ?", (scope_id,)
        elif scope_kind == "handoff":
            where, params = "source_ref_kind = 'handoff' AND source_ref_id = ?", (scope_id,)
        elif scope_kind == "agent":
            where = "request_id IN (SELECT request_id FROM work_request_targets WHERE agent_id = ?)"
            params = (scope_id,)
        else:
            raise ContractError("unsupported cancellation scope")
        with self.database._lock:
            if scope_kind == "task":
                rows = self.database._conn.execute(
                    """
                    WITH RECURSIVE task_tree(task_id) AS (
                        SELECT task_id FROM tasks WHERE task_id = ?
                        UNION ALL
                        SELECT child.task_id
                        FROM tasks AS child
                        JOIN task_tree AS parent ON child.parent_task_id = parent.task_id
                    )
                    SELECT request_id FROM work_requests
                    WHERE task_id IN (SELECT task_id FROM task_tree)
                    ORDER BY created_at, request_id
                    """,
                    params,
                ).fetchall()
            else:
                rows = self.database._conn.execute(
                    f"SELECT request_id FROM work_requests WHERE {where} ORDER BY created_at, request_id",
                    params,
                ).fetchall()
        return [row["request_id"] for row in rows]

    def list_active_run_ids_for_requests(self, request_ids: Sequence[str]) -> list[str]:
        """Return live run IDs before a cancellation mutates their durable state."""

        normalized = tuple(dict.fromkeys(_required_id(value, "request id") for value in request_ids))
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"""
                SELECT run_id FROM runs
                WHERE request_id IN ({placeholders})
                  AND status IN ('queued', 'running', 'waiting_input', 'waiting_approval')
                ORDER BY created_at, run_id
                """,
                normalized,
            ).fetchall()
        return [row["run_id"] for row in rows]

    def mark_request_completed_if_settled(self, request_id: str) -> dict[str, Any]:
        """Close a request only after every fan-out delivery reached a terminal state."""

        request_id = _required_id(request_id, "request id")
        with self.database.transaction() as connection:
            request = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ContractError(f"work request not found: {request_id}")
            if request["status"] == "active":
                pending = connection.execute(
                    """
                    SELECT 1 FROM deliveries
                    WHERE request_id = ? AND status IN ('pending', 'leased', 'dispatched', 'retry_wait')
                    LIMIT 1
                    """,
                    (request_id,),
                ).fetchone()
                if pending is None:
                    connection.execute(
                        "UPDATE work_requests SET status = 'completed' WHERE request_id = ?",
                        (request_id,),
                    )
            updated = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        assert updated is not None
        return _work_request_row(updated, self.database._conn)

    def pause_request_budget(
        self,
        request_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        request_id = _required_id(request_id, "request id")
        reason = _require_text(reason, "budget pause reason")
        with self.database.transaction() as connection:
            request = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ContractError(f"work request not found: {request_id}")
            if request["status"] == "active":
                connection.execute(
                    "UPDATE work_requests SET budget_paused_at = ?, budget_pause_reason = ? WHERE request_id = ?",
                    (_utc_now(), reason, request_id),
                )
            updated = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        assert updated is not None
        return _work_request_row(updated, self.database._conn)

    def resume_request(self, request_id: str) -> dict[str, Any]:
        request_id = _required_id(request_id, "request id")
        with self.database.transaction() as connection:
            request = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ContractError(f"work request not found: {request_id}")
            if request["status"] == "canceled":
                raise ContractError("cannot resume a canceled request")
            connection.execute(
                "UPDATE work_requests SET budget_paused_at = NULL, budget_pause_reason = NULL WHERE request_id = ?",
                (request_id,),
            )
            updated = connection.execute(
                "SELECT * FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        assert updated is not None
        return _work_request_row(updated, self.database._conn)

    def consume_usage(self, request_id: str, increments: Mapping[str, int]) -> dict[str, Any]:
        request_id = _required_id(request_id, "request id")
        if not isinstance(increments, Mapping) or not increments:
            raise ContractError("usage increments must be a non-empty object")
        allowed = (
            "tokens",
            "cost_micros",
            "model_calls",
            "tool_calls",
            "wall_seconds",
            "wakes",
            "fanout",
            "handoffs",
        )
        unknown = sorted(set(increments) - set(allowed))
        if unknown:
            raise ContractError(f"usage contains unknown fields: {', '.join(unknown)}")
        normalized = {}
        for key, value in increments.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError(f"usage {key} must be a non-negative integer")
            normalized[key] = value
        with self.database.transaction() as connection:
            request = connection.execute(
                "SELECT budget_json, status FROM work_requests WHERE request_id = ?", (request_id,)
            ).fetchone()
            if request is None:
                raise ContractError(f"work request not found: {request_id}")
            if request["status"] == "canceled":
                raise ContractError("cannot consume usage for a canceled request")
            columns = tuple(allowed)
            usage = connection.execute("SELECT * FROM work_usage WHERE request_id = ?", (request_id,)).fetchone()
            if usage is None:
                connection.execute("INSERT INTO work_usage (request_id, updated_at) VALUES (?, ?)", (request_id, _utc_now()))
                usage = connection.execute("SELECT * FROM work_usage WHERE request_id = ?", (request_id,)).fetchone()
            assert usage is not None
            next_values = {column: int(usage[column]) + normalized.get(column, 0) for column in columns}
            budget = _json_load(request["budget_json"], {})
            limits = {
                "tokens": budget.get("max_tokens"),
                "cost_micros": budget.get("max_cost_micros"),
                "model_calls": budget.get("max_model_calls"),
                "tool_calls": budget.get("max_tool_calls"),
                "wall_seconds": budget.get("max_wall_seconds"),
                "wakes": budget.get("max_wakes"),
                "fanout": budget.get("max_fanout"),
                "handoffs": budget.get("max_handoffs"),
            }
            exceeded = [column for column in columns if limits[column] is not None and next_values[column] > int(limits[column])]
            if exceeded:
                raise BudgetExceededError(
                    f"budget exhausted: {', '.join(exceeded)}",
                    request_id=request_id,
                    fields=exceeded,
                )
            connection.execute(
                """
                UPDATE work_usage SET tokens = ?, cost_micros = ?, model_calls = ?,
                    tool_calls = ?, wall_seconds = ?, wakes = ?, fanout = ?, handoffs = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (*[next_values[column] for column in columns], _utc_now(), request_id),
            )
            updated = connection.execute("SELECT * FROM work_usage WHERE request_id = ?", (request_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def get_usage(self, request_id: str) -> dict[str, Any]:
        request_id = _required_id(request_id, "request id")
        with self.database._lock:
            row = self.database._conn.execute("SELECT * FROM work_usage WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            return {"request_id": request_id, "tokens": 0, "cost_micros": 0, "model_calls": 0, "tool_calls": 0, "wall_seconds": 0, "wakes": 0, "fanout": 0, "handoffs": 0}
        return dict(row)

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        run_id = _required_id(run_id, "run id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _run_row(row) if row is not None else None

    def list_runs(
        self,
        workspace_id: str,
        *,
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        statuses: Sequence[str] = (),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "workspace id")
        _require_page_limit(limit)
        normalized_statuses = tuple(_required_kind(status, "run status") for status in statuses)
        allowed = {
            "queued",
            "running",
            "waiting_input",
            "waiting_approval",
            "succeeded",
            "failed",
            "timed_out",
            "canceled",
        }
        if any(status not in allowed for status in normalized_statuses):
            raise ContractError("run status is unsupported")
        where = ["r.workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if agent_id is not None:
            where.append("r.agent_id = ?")
            params.append(_required_id(agent_id, "run agent id"))
        if task_id is not None:
            where.append("wr.task_id = ?")
            params.append(_required_id(task_id, "run task id"))
        if conversation_id is not None:
            where.append("wr.conversation_id = ?")
            params.append(_required_id(conversation_id, "run conversation id"))
        if normalized_statuses:
            where.append("r.status IN (" + ",".join("?" for _ in normalized_statuses) + ")")
            params.extend(normalized_statuses)
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"""
                SELECT r.*
                FROM runs AS r
                JOIN work_requests AS wr ON wr.request_id = r.request_id
                WHERE {' AND '.join(where)}
                ORDER BY r.created_at DESC, r.run_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {**_run_row(row), "usage": self.get_usage(row["request_id"])}
            for row in rows
        ]

    def list_run_events(self, run_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        run_id = _required_id(run_id, "run id")
        _require_page_limit(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT * FROM run_events
                WHERE run_id = ? AND event_type != 'agent.reasoning.delta'
                ORDER BY occurred_at, run_event_id
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [
            {**dict(row), "payload": _json_load(row["payload_json"], {})}
            for row in rows
        ]

    def get_run_event(self, run_event_id: str) -> Optional[dict[str, Any]]:
        """Read one durable run event by its idempotency key."""

        run_event_id = _required_id(run_event_id, "run event id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM run_events WHERE run_event_id = ? AND event_type != 'agent.reasoning.delta'",
                (run_event_id,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "payload": _json_load(row["payload_json"], {})}

    def get_delivery_output(
        self, delivery_id: str, *, exclude_run_id: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """Return the first durable output produced by a delivery attempt.

        A delivery may have multiple run attempts, but its externally visible
        response is one logical result.  Once an attempt has durably recorded
        that result, a later attempt must replay it instead of invoking the
        model and consequential tools again.
        """

        delivery_id = _required_id(delivery_id, "delivery id")
        exclude_run_id = _optional_id(exclude_run_id, "excluded run id")
        with self.database._lock:
            if exclude_run_id is None:
                row = self.database._conn.execute(
                    """
                    SELECT re.run_id, re.payload_json, re.occurred_at
                    FROM run_events AS re
                    JOIN runs AS r ON r.run_id = re.run_id
                    WHERE r.delivery_id = ? AND re.event_type = 'run.output'
                    ORDER BY re.occurred_at, re.run_id, re.run_event_id
                    LIMIT 1
                    """,
                    (delivery_id,),
                ).fetchone()
            else:
                row = self.database._conn.execute(
                    """
                    SELECT re.run_id, re.payload_json, re.occurred_at
                    FROM run_events AS re
                    JOIN runs AS r ON r.run_id = re.run_id
                    WHERE r.delivery_id = ?
                      AND re.run_id <> ?
                      AND re.event_type = 'run.output'
                    ORDER BY re.occurred_at, re.run_id, re.run_event_id
                    LIMIT 1
                    """,
                    (delivery_id, exclude_run_id),
                ).fetchone()
        if row is None:
            return None
        payload = _json_load(row["payload_json"], {})
        text = str(payload.get("text") or "").strip() if isinstance(payload, Mapping) else ""
        if not text:
            return None
        return {
            "run_id": row["run_id"],
            "text": text,
            "occurred_at": row["occurred_at"],
        }

    def record_run_event(
        self,
        run_event_id: str,
        run_id: str,
        event_type: str,
        *,
        actor_id: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        occurred_at: Optional[str] = None,
    ) -> dict[str, Any]:
        kind = _required_kind(event_type, "run event type")
        if kind == "agent.reasoning.delta":
            raise ContractError("recording raw chain-of-thought (agent.reasoning.delta) is forbidden")
        values = (
            _required_id(run_event_id, "run event id"),
            _required_id(run_id, "run id"),
            kind,
            _optional_id(actor_id, "run event actor id"),
            _json_object(payload, "run event payload"),
            occurred_at or _utc_now(),
        )
        try:
            with self.database.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM run_events WHERE run_event_id = ?",
                    (values[0],),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["run_id"] == values[1]
                        and existing["event_type"] == values[2]
                        and existing["actor_id"] == values[3]
                        and existing["payload_json"] == values[4]
                    ):
                        return {
                            **dict(existing),
                            "payload": _json_load(existing["payload_json"], {}),
                        }
                    raise PersistenceConflictError(
                        "run event idempotency key has a different payload"
                    )
                connection.execute(
                    """
                    INSERT INTO run_events
                        (run_event_id, run_id, event_type, actor_id, payload_json, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                row = connection.execute("SELECT * FROM run_events WHERE run_event_id = ?", (values[0],)).fetchone()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("run event references a missing relation") from error
        assert row is not None
        return {**dict(row), "payload": _json_load(row["payload_json"], {})}

    def transition_run(self, run_id: str, target: RunStatus | str) -> dict[str, Any]:
        run_id = _required_id(run_id, "run id")
        target = _as_enum(RunStatus, target, "run status")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise ContractError(f"run not found: {run_id}")
            current = RunStatus(row["status"])
            require_transition(current, target, RUN_EDGES)
            now = _utc_now()
            connection.execute(
                "UPDATE runs SET status = ?, started_at = CASE WHEN ? = 'running' AND started_at IS NULL THEN ? ELSE started_at END, finished_at = CASE WHEN ? IN ('succeeded', 'failed', 'timed_out', 'canceled') THEN ? ELSE finished_at END WHERE run_id = ?",
                (target.value, target.value, now, target.value, now, run_id),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert updated is not None
        return _run_row(updated)

    def park_run_for_approval(
        self,
        run_id: str,
        approval_id: str,
        lease_token: str,
        *,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a run's approval wait while releasing its delivery lease.

        The delivery remains dispatched and points at the same logical run.  A
        later approved decision can therefore reacquire the delivery and resume
        that run instead of creating a second run or retrying a side effect.
        """

        run_id = _required_id(run_id, "run id")
        approval_id = _required_id(approval_id, "approval id")
        lease_token = _require_text(lease_token, "lease token")
        now = now or _utc_now()
        _require_timestamp(now, "approval wait timestamp")
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise ContractError(f"run not found: {run_id}")
            if run["status"] != RunStatus.RUNNING.value:
                raise ContractError("only a running run can wait for approval")
            approval = connection.execute(
                "SELECT run_id, status, expires_at FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if approval is None or approval["run_id"] != run_id or approval["status"] != "pending":
                raise ContractError("approval is not pending for this run")
            if approval["expires_at"] and _timestamp_expired(approval["expires_at"], now):
                raise ContractError("approval has expired")
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (run["delivery_id"], lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            require_transition(RunStatus(run["status"]), RunStatus.WAITING_APPROVAL, RUN_EDGES)
            connection.execute(
                "UPDATE runs SET status = 'waiting_approval' WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM delivery_leases WHERE delivery_id = ?",
                (run["delivery_id"],),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert updated is not None
        return _run_row(updated)

    def resume_run_after_approval(
        self,
        run_id: str,
        approval_id: str,
        lease_token: str,
        *,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Resume the same waiting run after a still-valid approval."""

        run_id = _required_id(run_id, "run id")
        approval_id = _required_id(approval_id, "approval id")
        lease_token = _require_text(lease_token, "lease token")
        now = now or _utc_now()
        _require_timestamp(now, "approval resume timestamp")
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["status"] != RunStatus.WAITING_APPROVAL.value:
                raise ContractError("run is not waiting for approval")
            approval = connection.execute(
                "SELECT run_id, status, expires_at FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if (
                approval is None
                or approval["run_id"] != run_id
                or approval["status"] != "approved"
                or (approval["expires_at"] and _timestamp_expired(approval["expires_at"], now))
            ):
                raise ContractError("approval is not valid for this run")
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (run["delivery_id"], lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            require_transition(RunStatus(run["status"]), RunStatus.RUNNING, RUN_EDGES)
            connection.execute(
                "UPDATE runs SET status = 'running', started_at = COALESCE(started_at, ?) WHERE run_id = ?",
                (now, run_id),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert updated is not None
        return _run_row(updated)

    def park_run_for_takeover(
        self,
        run_id: str,
        takeover_request_id: str,
        lease_token: str,
        *,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a human takeover wait while releasing the delivery lease."""

        run_id = _required_id(run_id, "run id")
        takeover_request_id = _required_id(takeover_request_id, "takeover request id")
        lease_token = _require_text(lease_token, "lease token")
        now = now or _utc_now()
        _require_timestamp(now, "human takeover timestamp")
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                raise ContractError(f"run not found: {run_id}")
            if run["status"] != RunStatus.RUNNING.value:
                raise ContractError("only a running run can wait for human takeover")
            takeover = connection.execute(
                "SELECT run_id, status FROM takeover_requests WHERE takeover_request_id = ?",
                (takeover_request_id,),
            ).fetchone()
            if (
                takeover is None
                or takeover["run_id"] != run_id
                or takeover["status"] != "pending"
            ):
                raise ContractError("takeover request is not pending for this run")
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (run["delivery_id"], lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            require_transition(RunStatus(run["status"]), RunStatus.WAITING_INPUT, RUN_EDGES)
            connection.execute(
                "UPDATE runs SET status = 'waiting_input' WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM delivery_leases WHERE delivery_id = ?",
                (run["delivery_id"],),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert updated is not None
        return _run_row(updated)

    def resume_run_after_takeover(
        self,
        run_id: str,
        takeover_request_id: str,
        lease_token: str,
        *,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Resume the same waiting run after an approved human takeover."""

        run_id = _required_id(run_id, "run id")
        takeover_request_id = _required_id(takeover_request_id, "takeover request id")
        lease_token = _require_text(lease_token, "lease token")
        now = now or _utc_now()
        _require_timestamp(now, "human takeover resume timestamp")
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["status"] != RunStatus.WAITING_INPUT.value:
                raise ContractError("run is not waiting for human takeover")
            takeover = connection.execute(
                "SELECT run_id, session_id, status FROM takeover_requests WHERE takeover_request_id = ?",
                (takeover_request_id,),
            ).fetchone()
            if (
                takeover is None
                or takeover["run_id"] != run_id
                or takeover["status"] != "approved"
            ):
                raise ContractError("takeover request is not approved for this run")
            session = connection.execute(
                "SELECT status FROM computer_sessions WHERE session_id = ?",
                (takeover["session_id"],),
            ).fetchone()
            if session is None or session["status"] != "active":
                raise ContractError("approved takeover session is not active")
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (run["delivery_id"], lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            require_transition(RunStatus(run["status"]), RunStatus.RUNNING, RUN_EDGES)
            connection.execute(
                "UPDATE runs SET status = 'running', started_at = COALESCE(started_at, ?) WHERE run_id = ?",
                (now, run_id),
            )
            updated = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert updated is not None
        return _run_row(updated)

    def fail_waiting_takeover(
        self,
        run_id: str,
        takeover_request_id: str,
        *,
        error_code: str = "HumanTakeoverDenied",
        error_detail: str = "human takeover was denied",
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fail a parked run when its human takeover request is denied."""

        run_id = _required_id(run_id, "run id")
        takeover_request_id = _required_id(takeover_request_id, "takeover request id")
        now = now or _utc_now()
        _require_timestamp(now, "takeover resolution timestamp")
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["status"] != RunStatus.WAITING_INPUT.value:
                raise ContractError("run is not waiting for human takeover")
            takeover = connection.execute(
                "SELECT run_id, status FROM takeover_requests WHERE takeover_request_id = ?",
                (takeover_request_id,),
            ).fetchone()
            if (
                takeover is None
                or takeover["run_id"] != run_id
                or takeover["status"] == "approved"
            ):
                raise ContractError("takeover cannot fail this run")
            require_transition(RunStatus(run["status"]), RunStatus.FAILED, RUN_EDGES)
            connection.execute(
                "UPDATE runs SET status = 'failed', finished_at = ? WHERE run_id = ?",
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE delivery_attempts
                SET outcome = 'failed', error_code = ?, error_detail = ?, finished_at = ?
                WHERE delivery_id = ? AND attempt_number = (
                    SELECT MAX(attempt_number) FROM delivery_attempts WHERE delivery_id = ?
                )
                """,
                (error_code, error_detail, now, run["delivery_id"], run["delivery_id"]),
            )
            delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (run["delivery_id"],)
            ).fetchone()
            if delivery is None:
                raise ContractError("run delivery not found")
            require_transition(DeliveryStatus(delivery["status"]), DeliveryStatus.DEAD_LETTER, DELIVERY_EDGES)
            connection.execute(
                "UPDATE deliveries SET status = 'dead_letter', next_attempt_at = NULL, updated_at = ? WHERE delivery_id = ?",
                (now, run["delivery_id"]),
            )
            connection.execute(
                "DELETE FROM delivery_leases WHERE delivery_id = ?",
                (run["delivery_id"],),
            )
            updated_run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            updated_delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (run["delivery_id"],)
            ).fetchone()
        assert updated_run is not None and updated_delivery is not None
        return {"run": _run_row(updated_run), "delivery": _delivery_row(updated_delivery)}

    def fail_waiting_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        error_code: str = "ApprovalDenied",
        error_detail: str = "tool approval was denied",
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fail a parked run and dead-letter its delivery without a worker lease."""

        run_id = _required_id(run_id, "run id")
        approval_id = _required_id(approval_id, "approval id")
        now = now or _utc_now()
        _require_timestamp(now, "approval resolution timestamp")
        with self.database.transaction() as connection:
            run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None or run["status"] != RunStatus.WAITING_APPROVAL.value:
                raise ContractError("run is not waiting for approval")
            approval = connection.execute(
                "SELECT run_id, status FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if approval is None or approval["run_id"] != run_id or approval["status"] == "approved":
                raise ContractError("approval cannot fail this run")
            require_transition(RunStatus(run["status"]), RunStatus.FAILED, RUN_EDGES)
            connection.execute(
                "UPDATE runs SET status = 'failed', finished_at = ? WHERE run_id = ?",
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE delivery_attempts
                SET outcome = 'failed', error_code = ?, error_detail = ?, finished_at = ?
                WHERE delivery_id = ? AND attempt_number = (
                    SELECT MAX(attempt_number) FROM delivery_attempts WHERE delivery_id = ?
                )
                """,
                (error_code, error_detail, now, run["delivery_id"], run["delivery_id"]),
            )
            delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (run["delivery_id"],)
            ).fetchone()
            if delivery is None:
                raise ContractError("run delivery not found")
            require_transition(DeliveryStatus(delivery["status"]), DeliveryStatus.DEAD_LETTER, DELIVERY_EDGES)
            connection.execute(
                "UPDATE deliveries SET status = 'dead_letter', next_attempt_at = NULL, updated_at = ? WHERE delivery_id = ?",
                (now, run["delivery_id"]),
            )
            connection.execute(
                "DELETE FROM delivery_leases WHERE delivery_id = ?",
                (run["delivery_id"],),
            )
            updated_run = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            updated_delivery = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (run["delivery_id"],)
            ).fetchone()
        assert updated_run is not None and updated_delivery is not None
        return {"run": _run_row(updated_run), "delivery": _delivery_row(updated_delivery)}

    def get_delivery(self, delivery_id: str) -> Optional[dict[str, Any]]:
        delivery_id = _required_id(delivery_id, "delivery id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if row is None:
                return None
        return _delivery_row(row, connection=self.database._conn)

    def list_deliveries_for_request(self, request_id: str) -> list[dict[str, Any]]:
        """Return every fan-out delivery for one logical request in stable order."""

        request_id = _required_id(request_id, "request id")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM deliveries WHERE request_id = ? ORDER BY created_at, delivery_id",
                (request_id,),
            ).fetchall()
        return [_delivery_row(row, connection=self.database._conn) for row in rows]

    def list_runs_for_request(self, request_id: str) -> list[dict[str, Any]]:
        """Return attempts for a logical request without reconstructing from live events."""

        request_id = _required_id(request_id, "request id")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM runs WHERE request_id = ? ORDER BY created_at, run_id",
                (request_id,),
            ).fetchall()
        return [_run_row(row) for row in rows]

    def lease_delivery(
        self,
        delivery_id: str,
        holder_id: str,
        *,
        lease_seconds: int = 60,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "delivery id")
        holder_id = _required_id(holder_id, "lease holder id")
        _require_positive_int(lease_seconds, "lease seconds")
        now = now or _utc_now()
        _require_timestamp(now, "lease timestamp")
        expires_at = _shift_timestamp(now, lease_seconds)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"delivery not found: {delivery_id}")
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if lease is not None and not _timestamp_expired(lease["expires_at"], now):
                raise PersistenceConflictError("delivery is already leased")
            if lease is not None:
                connection.execute("DELETE FROM delivery_leases WHERE delivery_id = ?", (delivery_id,))
                if row["status"] == DeliveryStatus.LEASED.value:
                    connection.execute(
                        "UPDATE deliveries SET status = ?, updated_at = ? WHERE delivery_id = ?",
                        (DeliveryStatus.PENDING.value, now, delivery_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
                    ).fetchone()
            status = DeliveryStatus(row["status"])
            resume_run_id: Optional[str] = None
            resume_approval_id: Optional[str] = None
            resume_takeover_id: Optional[str] = None
            if status is DeliveryStatus.DISPATCHED:
                resumable = connection.execute(
                    """
                    SELECT rr.run_id, aa.approval_id
                    FROM runs AS rr
                    JOIN approvals AS aa ON aa.run_id = rr.run_id
                    WHERE rr.delivery_id = ?
                      AND rr.status = 'waiting_approval'
                      AND aa.status = 'approved'
                      AND (aa.expires_at IS NULL OR aa.expires_at > ?)
                    ORDER BY rr.created_at DESC, aa.created_at DESC, aa.approval_id DESC
                    LIMIT 1
                    """,
                    (delivery_id, now),
                ).fetchone()
                if resumable is None:
                    takeover = connection.execute(
                        """
                        SELECT rr.run_id, tr.takeover_request_id
                        FROM runs AS rr
                        JOIN takeover_requests AS tr ON tr.run_id = rr.run_id
                        WHERE rr.delivery_id = ?
                          AND rr.status = 'waiting_input'
                          AND tr.status = 'approved'
                          AND tr.run_id = rr.run_id
                        ORDER BY rr.created_at DESC, tr.created_at DESC, tr.takeover_request_id DESC
                        LIMIT 1
                        """,
                        (delivery_id,),
                    ).fetchone()
                    if takeover is None:
                        raise ContractError("dispatched delivery is not resumable")
                    resume_run_id = str(takeover["run_id"])
                    resume_takeover_id = str(takeover["takeover_request_id"])
                else:
                    resume_run_id = str(resumable["run_id"])
                    resume_approval_id = str(resumable["approval_id"])
            if status is DeliveryStatus.RETRY_WAIT:
                connection.execute(
                    "UPDATE deliveries SET status = ?, updated_at = ? WHERE delivery_id = ?",
                    (DeliveryStatus.PENDING.value, now, delivery_id),
                )
                status = DeliveryStatus.PENDING
            if status not in {DeliveryStatus.PENDING, DeliveryStatus.DISPATCHED}:
                raise ContractError(f"delivery cannot be leased from {status.value}")
            token = uuid.uuid4().hex
            connection.execute(
                "UPDATE deliveries SET status = ?, updated_at = ? WHERE delivery_id = ?",
                (DeliveryStatus.LEASED.value, now, delivery_id),
            )
            connection.execute(
                """
                INSERT INTO delivery_leases
                    (delivery_id, lease_token, holder_id, acquired_at, heartbeat_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (delivery_id, token, holder_id, now, now, expires_at),
            )
            updated = connection.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
        assert updated is not None
        result = _delivery_row(updated, connection=self.database._conn)
        result["lease_token"] = token
        result["lease_expires_at"] = expires_at
        if resume_run_id is not None:
            result["resume_run_id"] = resume_run_id
            result["resume_approval_id"] = resume_approval_id
            result["resume_takeover_id"] = resume_takeover_id
        return result

    def heartbeat_lease(
        self,
        delivery_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 60,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "delivery id")
        lease_token = _require_text(lease_token, "lease token")
        _require_positive_int(lease_seconds, "lease seconds")
        now = now or _utc_now()
        _require_timestamp(now, "lease timestamp")
        expires_at = _shift_timestamp(now, lease_seconds)
        with self.database.transaction() as connection:
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (delivery_id, lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            connection.execute(
                "UPDATE delivery_leases SET heartbeat_at = ?, expires_at = ? WHERE delivery_id = ?",
                (now, expires_at, delivery_id),
            )
        return {"delivery_id": delivery_id, "lease_token": lease_token, "expires_at": expires_at}

    def mark_dispatched(self, delivery_id: str, lease_token: str, *, now: Optional[str] = None) -> dict[str, Any]:
        return self._lease_status(delivery_id, lease_token, DeliveryStatus.DISPATCHED, now=now)

    def acknowledge(self, delivery_id: str, lease_token: str, *, now: Optional[str] = None) -> dict[str, Any]:
        return self._lease_status(delivery_id, lease_token, DeliveryStatus.ACKNOWLEDGED, now=now, release=True)

    def finish_leased_delivery(
        self,
        delivery_id: str,
        lease_token: str,
        target: DeliveryStatus | str,
        *,
        next_attempt_at: Optional[str] = None,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        target = _as_enum(DeliveryStatus, target, "delivery status")
        if target not in {
            DeliveryStatus.RETRY_WAIT,
            DeliveryStatus.DEAD_LETTER,
            DeliveryStatus.CANCELED,
        }:
            raise ContractError("leased delivery can only be failed, dead-lettered, or canceled")
        return self._lease_status(
            delivery_id,
            lease_token,
            target,
            now=now,
            release=True,
            next_attempt_at=next_attempt_at,
        )

    def release_lease(
        self,
        delivery_id: str,
        lease_token: str,
        *,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return a pre-dispatch lease to the queue without creating an attempt.

        A worker may lose execution eligibility after claiming a delivery (for
        example, an operator pauses the agent).  That is not a failed model
        attempt and must remain retryable rather than being dead-lettered.
        """

        return self._lease_status(
            delivery_id,
            lease_token,
            DeliveryStatus.PENDING,
            now=now,
            release=True,
        )

    def record_attempt(
        self,
        delivery_id: str,
        outcome: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "delivery id")
        outcome = _required_kind(outcome, "delivery attempt outcome")
        if outcome not in {"started", "accepted", "failed", "timed_out", "canceled"}:
            raise ContractError("delivery attempt outcome is not supported")
        started_at = started_at or _utc_now()
        _require_timestamp(started_at, "attempt started_at")
        if finished_at is not None:
            _require_timestamp(finished_at, "attempt finished_at")
        with self.database.transaction() as connection:
            delivery = connection.execute(
                "SELECT attempt_count FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if delivery is None:
                raise ContractError(f"delivery not found: {delivery_id}")
            attempt_number = int(delivery["attempt_count"]) + 1
            try:
                connection.execute(
                    """
                    INSERT INTO delivery_attempts
                        (delivery_attempt_id, delivery_id, attempt_number, outcome,
                         error_code, error_detail, started_at, finished_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_domain_id(DomainIdPrefix.DELIVERY_ATTEMPT),
                        delivery_id,
                        attempt_number,
                        outcome,
                        error_code,
                        error_detail,
                        started_at,
                        finished_at,
                    ),
                )
                connection.execute(
                    "UPDATE deliveries SET attempt_count = ?, updated_at = ? WHERE delivery_id = ?",
                    (attempt_number, _utc_now(), delivery_id),
                )
                row = connection.execute(
                    "SELECT * FROM delivery_attempts WHERE delivery_id = ? AND attempt_number = ?",
                    (delivery_id, attempt_number),
                ).fetchone()
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("delivery attempt conflicts with existing record") from error
        assert row is not None
        return dict(row)

    def finish_attempt(
        self,
        delivery_id: str,
        outcome: str,
        *,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Close the current delivery attempt without creating a retry row."""

        delivery_id = _required_id(delivery_id, "delivery id")
        outcome = _required_kind(outcome, "delivery attempt outcome")
        if outcome not in {"accepted", "failed", "timed_out", "canceled"}:
            raise ContractError("finished delivery attempt outcome is not supported")
        finished_at = finished_at or _utc_now()
        _require_timestamp(finished_at, "attempt finished_at")
        with self.database.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM delivery_attempts
                WHERE delivery_id = ? ORDER BY attempt_number DESC LIMIT 1
                """,
                (delivery_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE delivery_attempts
                SET outcome = ?, error_code = ?, error_detail = ?, finished_at = ?
                WHERE delivery_attempt_id = ?
                """,
                (outcome, error_code, error_detail, finished_at, row["delivery_attempt_id"]),
            )
            updated = connection.execute(
                "SELECT * FROM delivery_attempts WHERE delivery_attempt_id = ?",
                (row["delivery_attempt_id"],),
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def finish_delivery(
        self,
        delivery_id: str,
        target: DeliveryStatus | str,
        *,
        next_attempt_at: Optional[str] = None,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "delivery id")
        target = _as_enum(DeliveryStatus, target, "delivery status")
        now = now or _utc_now()
        _require_timestamp(now, "delivery timestamp")
        if next_attempt_at is not None:
            _require_timestamp(next_attempt_at, "next attempt timestamp")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
            if row is None:
                raise ContractError(f"delivery not found: {delivery_id}")
            current = DeliveryStatus(row["status"])
            require_transition(current, target, DELIVERY_EDGES)
            connection.execute("DELETE FROM delivery_leases WHERE delivery_id = ?", (delivery_id,))
            connection.execute(
                "UPDATE deliveries SET status = ?, next_attempt_at = ?, updated_at = ? WHERE delivery_id = ?",
                (target.value, next_attempt_at, now, delivery_id),
            )
            updated = connection.execute("SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
        assert updated is not None
        return _delivery_row(updated, connection=self.database._conn)

    def _lease_status(
        self,
        delivery_id: str,
        lease_token: str,
        target: DeliveryStatus,
        *,
        now: Optional[str],
        release: bool = False,
        next_attempt_at: Optional[str] = None,
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "delivery id")
        lease_token = _require_text(lease_token, "lease token")
        now = now or _utc_now()
        _require_timestamp(now, "delivery timestamp")
        if next_attempt_at is not None:
            _require_timestamp(next_attempt_at, "next attempt timestamp")
        with self.database.transaction() as connection:
            lease = connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ? AND lease_token = ?",
                (delivery_id, lease_token),
            ).fetchone()
            if lease is None or _timestamp_expired(lease["expires_at"], now):
                raise ContractError("delivery lease is missing or expired")
            row = connection.execute("SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
            assert row is not None
            current = DeliveryStatus(row["status"])
            require_transition(current, target, DELIVERY_EDGES)
            connection.execute(
                "UPDATE deliveries SET status = ?, next_attempt_at = ?, updated_at = ? WHERE delivery_id = ?",
                (target.value, next_attempt_at, now, delivery_id),
            )
            if release:
                connection.execute("DELETE FROM delivery_leases WHERE delivery_id = ?", (delivery_id,))
            updated = connection.execute("SELECT * FROM deliveries WHERE delivery_id = ?", (delivery_id,)).fetchone()
        assert updated is not None
        return _delivery_row(updated, connection=self.database._conn)


class SandboxRepository:
    """Persistent sandbox identity, mounts, snapshots, and resource policy."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_sandbox(
        self,
        sandbox_id: str,
        workspace_id: str,
        agent_id: str,
        provider_kind: str,
        isolation_level: str,
        home_ref: str,
        *,
        status: SandboxStatus | str = SandboxStatus.PROVISIONING,
    ) -> dict[str, Any]:
        values = (
            _required_id(sandbox_id, "sandbox id"),
            _required_id(workspace_id, "workspace id"),
            _required_id(agent_id, "agent id"),
            _require_kind_or_text(provider_kind, "sandbox provider kind"),
            _require_kind_or_text(isolation_level, "sandbox isolation level"),
            _as_enum(SandboxStatus, status, "sandbox status").value,
            _require_text(home_ref, "sandbox home reference"),
        )
        now = _utc_now()
        try:
            with self.database.transaction() as connection:
                _require_agent_workspace(connection, values[1], values[2])
                connection.execute(
                    """
                    INSERT INTO sandboxes
                        (sandbox_id, workspace_id, agent_id, provider_kind, isolation_level,
                         status, home_ref, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM sandboxes WHERE sandbox_id = ?", (values[0],)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("sandbox conflicts with an existing agent or identity") from error
        assert row is not None
        return dict(row)

    def get_sandbox(self, sandbox_id: str) -> Optional[dict[str, Any]]:
        sandbox_id = _required_id(sandbox_id, "sandbox id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone()
            if row is None:
                return None
            sandbox = dict(row)
            sandbox["mounts"] = [
                dict(item)
                for item in self.database._conn.execute(
                    "SELECT * FROM sandbox_mounts WHERE sandbox_id = ? AND revoked_at IS NULL ORDER BY created_at, sandbox_mount_id",
                    (sandbox_id,),
                ).fetchall()
            ]
            sandbox["resource_policy"] = next(
                (
                    dict(item)
                    for item in self.database._conn.execute(
                        "SELECT * FROM sandbox_resource_policies WHERE sandbox_id = ?", (sandbox_id,)
                    ).fetchall()
                ),
                None,
            )
        return sandbox

    def list_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        agent_id = _required_id(agent_id, "agent id")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT sandbox_id FROM sandboxes WHERE agent_id = ? ORDER BY created_at, sandbox_id",
                (agent_id,),
            ).fetchall()
        return [sandbox for row in rows if (sandbox := self.get_sandbox(row["sandbox_id"])) is not None]

    def get_sandbox_for_agent(self, agent_id: str) -> Optional[dict[str, Any]]:
        sandboxes = self.list_for_agent(agent_id)
        return sandboxes[0] if sandboxes else None

    def add_mount(
        self,
        mount_id: str,
        sandbox_id: str,
        source_ref: str,
        target_path: str,
        access_mode: str,
        granted_by_actor_id: str,
    ) -> dict[str, Any]:
        values = (
            _required_id(mount_id, "sandbox mount id"),
            _required_id(sandbox_id, "sandbox id"),
            _require_text(source_ref, "sandbox mount source"),
            _require_text(target_path, "sandbox mount target"),
            _require_kind_or_text(access_mode, "sandbox mount access mode"),
            _required_id(granted_by_actor_id, "mount grantor actor id"),
        )
        if values[4] not in {"read", "write"}:
            raise ContractError("sandbox mount access mode must be read or write")
        try:
            with self.database.transaction() as connection:
                sandbox = connection.execute(
                    "SELECT workspace_id FROM sandboxes WHERE sandbox_id = ?", (values[1],)
                ).fetchone()
                if sandbox is None:
                    raise ContractError("sandbox does not exist")
                _require_workspace_actor(connection, sandbox["workspace_id"], values[5])
                connection.execute(
                    """
                    INSERT INTO sandbox_mounts
                        (sandbox_mount_id, sandbox_id, source_ref, target_path,
                         access_mode, granted_by_actor_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (*values, _utc_now()),
                )
                row = connection.execute(
                    "SELECT * FROM sandbox_mounts WHERE sandbox_mount_id = ?", (values[0],)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise PersistenceConflictError("sandbox mount references a missing relation") from error
        assert row is not None
        return dict(row)

    def set_resource_policy(self, sandbox_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
        sandbox_id = _required_id(sandbox_id, "sandbox id")
        if not isinstance(policy, Mapping):
            raise ContractError("sandbox resource policy must be an object")
        allowed = {"cpu_millis", "memory_mib", "disk_mib", "max_processes", "max_wall_seconds", "network_policy"}
        unknown = sorted(set(policy) - allowed)
        if unknown:
            raise ContractError(f"sandbox resource policy contains unknown fields: {', '.join(unknown)}")
        for key in allowed - {"network_policy"}:
            value = policy.get(key)
            if value is not None:
                _require_positive_int(value, key)
        network_policy = _require_kind_or_text(policy.get("network_policy", "deny"), "network policy")
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO sandbox_resource_policies
                        (sandbox_id, cpu_millis, memory_mib, disk_mib, max_processes,
                         max_wall_seconds, network_policy, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sandbox_id) DO UPDATE SET
                        cpu_millis = excluded.cpu_millis,
                        memory_mib = excluded.memory_mib,
                        disk_mib = excluded.disk_mib,
                        max_processes = excluded.max_processes,
                        max_wall_seconds = excluded.max_wall_seconds,
                        network_policy = excluded.network_policy,
                        updated_at = excluded.updated_at
                    """,
                    (
                        sandbox_id,
                        policy.get("cpu_millis"),
                        policy.get("memory_mib"),
                        policy.get("disk_mib"),
                        policy.get("max_processes"),
                        policy.get("max_wall_seconds"),
                        network_policy,
                        _utc_now(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ContractError("sandbox does not exist") from error
            row = connection.execute(
                "SELECT * FROM sandbox_resource_policies WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def transition_status(self, sandbox_id: str, target: SandboxStatus | str) -> dict[str, Any]:
        sandbox_id = _required_id(sandbox_id, "sandbox id")
        target = _as_enum(SandboxStatus, target, "sandbox status")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)).fetchone()
            if row is None:
                raise ContractError(f"sandbox not found: {sandbox_id}")
            current = SandboxStatus(row["status"])
            require_transition(current, target, SANDBOX_EDGES)
            connection.execute(
                "UPDATE sandboxes SET status = ?, updated_at = ? WHERE sandbox_id = ?",
                (target.value, _utc_now(), sandbox_id),
            )
            updated = connection.execute("SELECT * FROM sandboxes WHERE sandbox_id = ?", (sandbox_id,)).fetchone()
        assert updated is not None
        return dict(updated)


class IntegrationRepository:
    """Durable review queue for bringing isolated task output into a project."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create(
        self,
        integration_id: str,
        workspace_id: str,
        task_id: str,
        requested_by_actor_id: str,
        source_ref: str,
        target_ref: str,
        *,
        run_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        source_agent_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        values = (
            _required_id(integration_id, "integration id"),
            _required_id(workspace_id, "integration workspace id"),
            _required_id(task_id, "integration task id"),
            _optional_id(run_id, "integration run id"),
            _optional_id(artifact_id, "integration artifact id"),
            _optional_id(source_agent_id, "integration source agent id"),
            _required_id(requested_by_actor_id, "integration requester actor id"),
            _require_text(source_ref, "integration source reference"),
            _require_text(target_ref, "integration target reference"),
            _required_id(idempotency_key or integration_id, "integration idempotency key"),
            _utc_now(),
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, values[1], values[6])
            task = connection.execute(
                "SELECT workspace_id FROM tasks WHERE task_id = ?", (values[2],)
            ).fetchone()
            if task is None or task["workspace_id"] != values[1]:
                raise ContractError("integration task is missing or belongs to another workspace")
            for table, value, label in (
                ("runs", values[3], "integration run"),
                ("artifacts", values[4], "integration artifact"),
            ):
                if value is None:
                    continue
                row = connection.execute(
                    f"SELECT workspace_id FROM {table} WHERE {table[:-1]}_id = ?", (value,)
                ).fetchone()
                if row is None or row["workspace_id"] != values[1]:
                    raise ContractError(f"{label} is missing or belongs to another workspace")
            if values[5] is not None:
                _require_agent_workspace(connection, values[1], values[5])
            existing = connection.execute(
                "SELECT * FROM integration_queue WHERE workspace_id = ? AND idempotency_key = ?",
                (values[1], values[9]),
            ).fetchone()
            if existing is not None:
                if all(
                    existing[key] == expected
                    for key, expected in {
                        "integration_id": values[0],
                        "task_id": values[2],
                        "run_id": values[3],
                        "artifact_id": values[4],
                        "source_agent_id": values[5],
                        "requested_by_actor_id": values[6],
                        "source_ref": values[7],
                        "target_ref": values[8],
                    }.items()
                ):
                    return dict(existing)
                raise PersistenceConflictError("integration idempotency key has a different payload")
            try:
                connection.execute(
                    """
                    INSERT INTO integration_queue
                        (integration_id, workspace_id, task_id, run_id, artifact_id,
                         source_agent_id, requested_by_actor_id, source_ref, target_ref,
                         status, idempotency_key, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("integration queue entry conflicts with existing state") from error
            row = connection.execute(
                "SELECT * FROM integration_queue WHERE integration_id = ?", (values[0],)
            ).fetchone()
        assert row is not None
        return dict(row)

    def get(self, integration_id: str) -> Optional[dict[str, Any]]:
        integration_id = _required_id(integration_id, "integration id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM integration_queue WHERE integration_id = ?", (integration_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_for_workspace(
        self, workspace_id: str, *, task_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "integration workspace id")
        _require_page_limit(limit)
        params: list[Any] = [workspace_id]
        where = "workspace_id = ?"
        if task_id is not None:
            where += " AND task_id = ?"
            params.append(_required_id(task_id, "integration task id"))
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM integration_queue WHERE {where} ORDER BY created_at DESC, integration_id LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, integration_id: str, status: str, *, conflict_detail: str = "") -> dict[str, Any]:
        integration_id = _required_id(integration_id, "integration id")
        status = _required_kind(status, "integration status")
        allowed = {"queued", "approved", "rejected", "applied", "conflict", "canceled"}
        if status not in allowed:
            raise ContractError("integration status is unsupported")
        edges = {
            "queued": {"approved", "rejected", "conflict", "canceled"},
            "approved": {"applied", "conflict", "canceled"},
            "conflict": {"approved", "canceled"},
            "rejected": set(),
            "applied": set(),
            "canceled": set(),
        }
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM integration_queue WHERE integration_id = ?", (integration_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"integration entry not found: {integration_id}")
            if row["status"] != status:
                if status not in edges[row["status"]]:
                    raise ContractError("invalid integration status transition")
                if status == "conflict" and not str(conflict_detail or "").strip():
                    raise ContractError("conflict detail is required")
                connection.execute(
                    "UPDATE integration_queue SET status = ?, conflict_detail = ?, resolved_at = ? WHERE integration_id = ?",
                    (
                        status,
                        str(conflict_detail or "").strip() or None,
                        _utc_now() if status in {"rejected", "applied", "canceled"} else None,
                        integration_id,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM integration_queue WHERE integration_id = ?", (integration_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)


class ConnectorRepository:
    """Durable external identity, grant, event, and outbound state.

    Connector credentials deliberately do not live in this repository.  The
    records here contain provider identifiers and exact authority scopes only;
    a connector adapter resolves a credential through its own secret broker at
    execution time.
    """

    _ACTIONS = frozenset({"connector.read", "connector.send", "connector.write"})
    _IDENTITY_STATES = frozenset({"active", "reauth_required", "revoked", "error"})

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_external_identity(
        self,
        record_id: str,
        workspace_id: str,
        owner_actor_id: str,
        connector_kind: str,
        external_workspace_id: str,
        external_identity_id: str,
        *,
        account_ref: str = "",
        display_name: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        status: str = "active",
    ) -> dict[str, Any]:
        now = _utc_now()
        values = (
            _required_id(record_id, "external identity record id"),
            _required_id(workspace_id, "external identity workspace id"),
            _required_id(owner_actor_id, "external identity owner actor id"),
            _required_kind(connector_kind, "connector kind"),
            _exact_external_ref(external_workspace_id, "external workspace id"),
            _exact_external_ref(external_identity_id, "external identity id"),
            str(account_ref or "").strip(),
            str(display_name or "").strip(),
            _required_kind(status, "external identity status"),
            _json_object(metadata, "external identity metadata"),
            now,
            now,
        )
        if values[8] not in self._IDENTITY_STATES:
            raise ContractError("external identity status is unsupported")
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, values[1], values[2])
            existing = connection.execute(
                """
                SELECT * FROM team_external_identities
                WHERE workspace_id = ? AND connector_kind = ?
                  AND external_workspace_id = ? AND external_identity_id = ?
                """,
                (values[1], values[3], values[4], values[5]),
            ).fetchone()
            if existing is not None:
                immutable = {
                    "external_identity_record_id": values[0],
                    "workspace_id": values[1],
                    "owner_actor_id": values[2],
                    "connector_kind": values[3],
                    "external_workspace_id": values[4],
                    "external_identity_id": values[5],
                }
                if all(existing[key] == expected for key, expected in immutable.items()):
                    return _connector_identity_row(existing)
                raise PersistenceConflictError(
                    "external identity already exists with a different record"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO team_external_identities
                        (external_identity_record_id, workspace_id, owner_actor_id,
                         connector_kind, external_workspace_id, external_identity_id,
                         account_ref, display_name, status, metadata_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError(
                    "external identity conflicts with an existing provider identity"
                ) from error
            row = connection.execute(
                "SELECT * FROM team_external_identities WHERE external_identity_record_id = ?",
                (values[0],),
            ).fetchone()
        assert row is not None
        return _connector_identity_row(row)

    def get_external_identity(self, record_id: str) -> Optional[dict[str, Any]]:
        record_id = _required_id(record_id, "external identity record id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM team_external_identities WHERE external_identity_record_id = ?",
                (record_id,),
            ).fetchone()
        return _connector_identity_row(row) if row is not None else None

    def list_external_identities(
        self, workspace_id: str, *, connector_kind: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "external identity workspace id")
        _require_page_limit(limit)
        params: list[Any] = [workspace_id]
        where = "workspace_id = ?"
        if connector_kind is not None:
            where += " AND connector_kind = ?"
            params.append(_required_kind(connector_kind, "connector kind"))
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM team_external_identities WHERE {where} ORDER BY created_at, external_identity_record_id LIMIT ?",
                params,
            ).fetchall()
        return [_connector_identity_row(row) for row in rows]

    def transition_external_identity(self, record_id: str, status: str) -> dict[str, Any]:
        record_id = _required_id(record_id, "external identity record id")
        status = _required_kind(status, "external identity status")
        if status not in self._IDENTITY_STATES:
            raise ContractError("external identity status is unsupported")
        edges = {
            "active": {"reauth_required", "error", "revoked"},
            "reauth_required": {"active", "error", "revoked"},
            "error": {"active", "reauth_required", "revoked"},
            "revoked": set(),
        }
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_external_identities WHERE external_identity_record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"external identity not found: {record_id}")
            current = row["status"]
            if current != status:
                if status not in edges[current]:
                    raise ContractError("invalid external identity status transition")
                now = _utc_now()
                connection.execute(
                    "UPDATE team_external_identities SET status = ?, updated_at = ?, revoked_at = ? WHERE external_identity_record_id = ?",
                    (status, now, now if status == "revoked" else None, record_id),
                )
            updated = connection.execute(
                "SELECT * FROM team_external_identities WHERE external_identity_record_id = ?",
                (record_id,),
            ).fetchone()
        assert updated is not None
        return _connector_identity_row(updated)

    def update_external_identity_health(
        self,
        record_id: str,
        health_status: str,
        *,
        error_detail: Optional[str] = None,
        reconnect: bool = False,
    ) -> dict[str, Any]:
        """Record transport health without changing the authorization identity."""

        record_id = _required_id(record_id, "external identity record id")
        health_status = _required_kind(health_status, "external identity health status")
        if health_status not in {"unknown", "healthy", "degraded", "offline", "error"}:
            raise ContractError("external identity health status is unsupported")
        now = _utc_now()
        detail = str(error_detail or "").strip()[:2_000] or None
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_external_identities WHERE external_identity_record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"external identity not found: {record_id}")
            connection.execute(
                """
                UPDATE team_external_identities
                SET health_status = ?, last_health_error = ?,
                    last_health_check_at = ?, reconnect_count = reconnect_count + ?,
                    updated_at = ?
                WHERE external_identity_record_id = ?
                """,
                (health_status, detail, now, 1 if reconnect else 0, now, record_id),
            )
            updated = connection.execute(
                "SELECT * FROM team_external_identities WHERE external_identity_record_id = ?",
                (record_id,),
            ).fetchone()
        assert updated is not None
        return _connector_identity_row(updated)

    def create_grant(
        self,
        grant_id: str,
        workspace_id: str,
        external_identity_record_id: str,
        grantor_actor_id: str,
        requester_actor_id: str,
        agent_id: str,
        actions: Sequence[str],
        resource_ids: Sequence[str],
        *,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        grant_id = _required_id(grant_id, "connector grant id")
        workspace_id = _required_id(workspace_id, "connector grant workspace id")
        external_identity_record_id = _required_id(
            external_identity_record_id, "external identity record id"
        )
        grantor_actor_id = _required_id(grantor_actor_id, "connector grantor actor id")
        requester_actor_id = _required_id(requester_actor_id, "connector requester actor id")
        agent_id = _required_id(agent_id, "connector grant agent id")
        normalized_actions = _exact_connector_actions(actions)
        normalized_resources = _exact_external_refs(resource_ids, "connector resource id")
        if expires_at is not None:
            expires_at = _require_timestamp(expires_at, "connector grant expiry")
        values = (
            grant_id,
            workspace_id,
            external_identity_record_id,
            grantor_actor_id,
            requester_actor_id,
            agent_id,
            _json(list(normalized_actions)),
            _json(list(normalized_resources)),
            "active",
            expires_at,
            _utc_now(),
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, grantor_actor_id)
            _require_workspace_actor(connection, workspace_id, requester_actor_id)
            _require_agent_workspace(connection, workspace_id, agent_id)
            identity = connection.execute(
                "SELECT workspace_id, status FROM team_external_identities WHERE external_identity_record_id = ?",
                (external_identity_record_id,),
            ).fetchone()
            if identity is None or identity["workspace_id"] != workspace_id:
                raise ContractError("external identity is missing or belongs to another workspace")
            if identity["status"] != "active":
                raise ContractError("connector grants require an active external identity")
            existing = connection.execute(
                """
                SELECT * FROM team_connector_grants
                WHERE workspace_id = ? AND external_identity_record_id = ?
                  AND requester_actor_id = ? AND agent_id = ?
                """,
                (workspace_id, external_identity_record_id, requester_actor_id, agent_id),
            ).fetchone()
            if existing is not None:
                immutable_match = (
                    existing["connector_grant_id"] == grant_id
                    and existing["workspace_id"] == workspace_id
                    and existing["external_identity_record_id"] == external_identity_record_id
                    and existing["grantor_actor_id"] == grantor_actor_id
                    and existing["requester_actor_id"] == requester_actor_id
                    and existing["agent_id"] == agent_id
                )
                if immutable_match and existing["status"] != "active":
                    connection.execute(
                        """
                        UPDATE team_connector_grants
                        SET actions_json = ?, resource_ids_json = ?, status = 'active',
                            expires_at = ?, revoked_at = NULL
                        WHERE connector_grant_id = ?
                        """,
                        (values[6], values[7], expires_at, grant_id),
                    )
                    refreshed = connection.execute(
                        "SELECT * FROM team_connector_grants WHERE connector_grant_id = ?",
                        (grant_id,),
                    ).fetchone()
                    assert refreshed is not None
                    return _connector_grant_row(refreshed)
                if (
                    existing["connector_grant_id"] == grant_id
                    and existing["grantor_actor_id"] == grantor_actor_id
                    and existing["actions_json"] == values[6]
                    and existing["resource_ids_json"] == values[7]
                    and existing["expires_at"] == expires_at
                ):
                    return _connector_grant_row(existing)
                raise PersistenceConflictError(
                    "connector grant already exists for this requester and agent"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO team_connector_grants
                        (connector_grant_id, workspace_id, external_identity_record_id,
                         grantor_actor_id, requester_actor_id, agent_id, actions_json,
                         resource_ids_json, status, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("connector grant conflicts with existing state") from error
            row = connection.execute(
                "SELECT * FROM team_connector_grants WHERE connector_grant_id = ?",
                (grant_id,),
            ).fetchone()
        assert row is not None
        return _connector_grant_row(row)

    def get_grant(self, grant_id: str) -> Optional[dict[str, Any]]:
        grant_id = _required_id(grant_id, "connector grant id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM team_connector_grants WHERE connector_grant_id = ?",
                (grant_id,),
            ).fetchone()
        return _connector_grant_row(row) if row is not None else None

    def list_grants(
        self, workspace_id: str, *, agent_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "connector grant workspace id")
        _require_page_limit(limit)
        params: list[Any] = [workspace_id]
        where = "workspace_id = ?"
        if agent_id is not None:
            where += " AND agent_id = ?"
            params.append(_required_id(agent_id, "connector grant agent id"))
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM team_connector_grants WHERE {where} ORDER BY created_at, connector_grant_id LIMIT ?",
                params,
            ).fetchall()
        return [_connector_grant_row(row) for row in rows]

    def revoke_grant(self, grant_id: str, *, actor_id: str) -> dict[str, Any]:
        grant_id = _required_id(grant_id, "connector grant id")
        actor_id = _required_id(actor_id, "connector grant actor id")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_connector_grants WHERE connector_grant_id = ?",
                (grant_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"connector grant not found: {grant_id}")
            if row["grantor_actor_id"] != actor_id:
                raise ContractError("only the connector grantor can revoke the grant")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            if row["status"] == "active":
                connection.execute(
                    "UPDATE team_connector_grants SET status = 'revoked', revoked_at = ? WHERE connector_grant_id = ?",
                    (_utc_now(), grant_id),
                )
            updated = connection.execute(
                "SELECT * FROM team_connector_grants WHERE connector_grant_id = ?",
                (grant_id,),
            ).fetchone()
        assert updated is not None
        return _connector_grant_row(updated)

    def bind_agent(
        self,
        binding_id: str,
        workspace_id: str,
        external_identity_record_id: str,
        agent_id: str,
        connector_grant_id: str,
        *,
        destination_conversation_id: Optional[str] = None,
        destination_thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        values = (
            _required_id(binding_id, "connector binding id"),
            _required_id(workspace_id, "connector binding workspace id"),
            _required_id(external_identity_record_id, "external identity record id"),
            _required_id(agent_id, "connector binding agent id"),
            _required_id(connector_grant_id, "connector binding grant id"),
            _optional_id(destination_conversation_id, "connector binding destination conversation id"),
            _optional_id(destination_thread_id, "connector binding destination thread id"),
            _utc_now(),
        )
        with self.database.transaction() as connection:
            _require_agent_workspace(connection, values[1], values[3])
            identity = connection.execute(
                "SELECT workspace_id, status FROM team_external_identities WHERE external_identity_record_id = ?",
                (values[2],),
            ).fetchone()
            grant = connection.execute(
                "SELECT workspace_id, external_identity_record_id, agent_id, status FROM team_connector_grants WHERE connector_grant_id = ?",
                (values[4],),
            ).fetchone()
            if (
                identity is None
                or identity["workspace_id"] != values[1]
                or identity["status"] != "active"
                or grant is None
                or grant["workspace_id"] != values[1]
                or grant["external_identity_record_id"] != values[2]
                or grant["agent_id"] != values[3]
                or grant["status"] != "active"
            ):
                raise ContractError("connector binding is outside the active grant boundary")
            for relation_id, table, column, label in (
                (values[5], "conversations", "conversation_id", "connector binding destination conversation"),
                (values[6], "threads", "thread_id", "connector binding destination thread"),
            ):
                if relation_id is None:
                    continue
                relation = connection.execute(
                    f"SELECT workspace_id FROM {table} WHERE {column} = ?"
                    if table == "conversations"
                    else "SELECT c.workspace_id FROM threads AS t JOIN conversations AS c ON c.conversation_id = t.conversation_id WHERE t.thread_id = ?",
                    (relation_id,),
                ).fetchone()
                if relation is None or relation["workspace_id"] != values[1]:
                    raise ContractError(f"{label} is missing or belongs to another workspace")
            if values[6] is not None and values[5] is not None:
                thread_parent = connection.execute(
                    "SELECT conversation_id FROM threads WHERE thread_id = ?",
                    (values[6],),
                ).fetchone()
                if thread_parent["conversation_id"] != values[5]:
                    raise ContractError(
                        "connector binding destination thread is outside its conversation"
                    )
            existing = connection.execute(
                "SELECT * FROM team_connector_bindings WHERE workspace_id = ? AND external_identity_record_id = ? AND agent_id = ?",
                (values[1], values[2], values[3]),
            ).fetchone()
            if existing is not None:
                if existing["connector_binding_id"] == values[0] and existing["connector_grant_id"] == values[4]:
                    if existing["status"] != "active":
                        connection.execute(
                            """
                            UPDATE team_connector_bindings
                            SET destination_conversation_id = ?, destination_thread_id = ?,
                                status = 'active', revoked_at = NULL
                            WHERE connector_binding_id = ?
                            """,
                            (values[5], values[6], values[0]),
                        )
                        existing = connection.execute(
                            "SELECT * FROM team_connector_bindings WHERE connector_binding_id = ?",
                            (values[0],),
                        ).fetchone()
                    assert existing is not None
                    return dict(existing)
                raise PersistenceConflictError("agent is already bound to this external identity")
            try:
                connection.execute(
                        """
                        INSERT INTO team_connector_bindings
                            (connector_binding_id, workspace_id, external_identity_record_id,
                         agent_id, connector_grant_id, destination_conversation_id,
                         destination_thread_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("connector binding conflicts with existing state") from error
            row = connection.execute(
                "SELECT * FROM team_connector_bindings WHERE connector_binding_id = ?",
                (values[0],),
            ).fetchone()
        assert row is not None
        return dict(row)

    def get_binding(self, binding_id: str) -> Optional[dict[str, Any]]:
        binding_id = _required_id(binding_id, "connector binding id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM team_connector_bindings WHERE connector_binding_id = ?",
                (binding_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_bindings(
        self, workspace_id: str, *, agent_id: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "connector binding workspace id")
        _require_page_limit(limit)
        params: list[Any] = [workspace_id]
        where = "b.workspace_id = ?"
        if agent_id is not None:
            where += " AND b.agent_id = ?"
            params.append(_required_id(agent_id, "connector binding agent id"))
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"""
                SELECT b.*, e.connector_kind, e.external_workspace_id,
                       e.external_identity_id, e.display_name,
                       e.status AS external_identity_status,
                       g.requester_actor_id, g.actions_json, g.resource_ids_json,
                       g.status AS grant_status, g.expires_at
                FROM team_connector_bindings AS b
                JOIN team_external_identities AS e
                  ON e.external_identity_record_id = b.external_identity_record_id
                JOIN team_connector_grants AS g
                  ON g.connector_grant_id = b.connector_grant_id
                WHERE {where}
                ORDER BY b.created_at, b.connector_binding_id
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_connector_binding_row(row) for row in rows]

    def find_inbound_routes(
        self,
        workspace_id: str,
        connector_kind: str,
        external_workspace_id: str,
        external_resource_id: str,
    ) -> list[dict[str, Any]]:
        """Find explicitly bound agents for one provider resource.

        Provider events are never routed merely because a connector is enabled:
        an active Team binding and an exact ``connector.read`` resource grant are
        both required.
        """

        workspace_id = _required_id(workspace_id, "connector route workspace id")
        connector_kind = _required_kind(connector_kind, "connector route kind")
        external_workspace_id = _exact_external_ref(
            external_workspace_id, "connector route external workspace id"
        )
        external_resource_id = _exact_external_ref(
            external_resource_id, "connector route external resource id"
        )
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT b.*, e.connector_kind, e.external_workspace_id,
                       e.external_identity_id, e.status AS external_identity_status,
                       g.requester_actor_id, g.actions_json, g.resource_ids_json,
                       g.status AS grant_status, g.expires_at
                FROM team_connector_bindings AS b
                JOIN team_external_identities AS e
                  ON e.external_identity_record_id = b.external_identity_record_id
                JOIN team_connector_grants AS g
                  ON g.connector_grant_id = b.connector_grant_id
                WHERE b.workspace_id = ?
                  AND e.connector_kind = ?
                  AND e.external_workspace_id = ?
                  AND b.status = 'active'
                  AND g.status = 'active'
                  AND e.status = 'active'
                ORDER BY b.created_at, b.connector_binding_id
                """,
                (workspace_id, connector_kind, external_workspace_id),
            ).fetchall()
        routes: list[dict[str, Any]] = []
        now = _utc_now()
        for row in rows:
            route = _connector_binding_row(row)
            if route["expires_at"] is not None and _timestamp_expired(route["expires_at"], now):
                continue
            if "connector.read" not in route["actions"] or external_resource_id not in route["resource_ids"]:
                continue
            routes.append(route)
        return routes

    def revoke_binding(self, binding_id: str) -> dict[str, Any]:
        binding_id = _required_id(binding_id, "connector binding id")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_connector_bindings WHERE connector_binding_id = ?",
                (binding_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"connector binding not found: {binding_id}")
            if row["status"] == "active":
                connection.execute(
                    "UPDATE team_connector_bindings SET status = 'revoked', revoked_at = ? WHERE connector_binding_id = ?",
                    (_utc_now(), binding_id),
                )
            updated = connection.execute(
                "SELECT * FROM team_connector_bindings WHERE connector_binding_id = ?",
                (binding_id,),
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def effective_grant(
        self,
        workspace_id: str,
        external_identity_record_id: str,
        agent_id: str,
        requester_actor_id: str,
        resource_id: str,
        action: str,
        *,
        now: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "connector workspace id")
        external_identity_record_id = _required_id(
            external_identity_record_id, "external identity record id"
        )
        agent_id = _required_id(agent_id, "connector agent id")
        requester_actor_id = _required_id(requester_actor_id, "connector requester actor id")
        resource_id = _exact_external_ref(resource_id, "connector resource id")
        action = _required_kind(action, "connector action")
        if action not in self._ACTIONS:
            raise ContractError("connector action is unsupported")
        now = now or _utc_now()
        _require_timestamp(now, "connector authorization time")
        with self.database._lock:
            row = self.database._conn.execute(
                """
                SELECT g.*, e.connector_kind, e.external_workspace_id,
                       e.external_identity_id, e.status AS external_identity_status,
                       b.status AS binding_status
                FROM team_connector_grants AS g
                JOIN team_connector_bindings AS b
                  ON b.connector_grant_id = g.connector_grant_id
                 AND b.workspace_id = g.workspace_id
                 AND b.external_identity_record_id = g.external_identity_record_id
                 AND b.agent_id = g.agent_id
                JOIN team_external_identities AS e
                  ON e.external_identity_record_id = g.external_identity_record_id
                WHERE g.workspace_id = ? AND g.external_identity_record_id = ?
                  AND g.agent_id = ? AND g.requester_actor_id = ?
                  AND g.status = 'active' AND b.status = 'active'
                  AND e.status = 'active'
                ORDER BY g.created_at DESC, g.connector_grant_id
                LIMIT 1
                """,
                (workspace_id, external_identity_record_id, agent_id, requester_actor_id),
            ).fetchone()
        if row is None:
            return None
        if row["expires_at"] is not None and _timestamp_expired(row["expires_at"], now):
            return None
        grant = _connector_grant_row(row)
        if action not in grant["actions"] or resource_id not in grant["resource_ids"]:
            return None
        return {
            **grant,
            "connector_kind": row["connector_kind"],
            "external_workspace_id": row["external_workspace_id"],
            "external_identity_id": row["external_identity_id"],
        }

    def create_event(
        self,
        connector_event_id: str,
        workspace_id: str,
        external_identity_record_id: str,
        external_event_id: str,
        requester_actor_id: str,
        target_agent_id: str,
        event_type: str,
        instruction: str,
        payload: Mapping[str, Any],
        *,
        external_thread_id: str = "",
        external_resource_id: str,
        destination_conversation_id: Optional[str] = None,
        destination_thread_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        connector_event_id = _required_id(connector_event_id, "connector event id")
        workspace_id = _required_id(workspace_id, "connector event workspace id")
        identity_id = _required_id(external_identity_record_id, "external identity record id")
        external_event_id = _exact_external_ref(external_event_id, "external event id")
        requester_actor_id = _required_id(requester_actor_id, "connector event requester actor id")
        target_agent_id = _required_id(target_agent_id, "connector event target agent id")
        event_type = _required_kind(event_type, "connector event type")
        instruction = _require_text(instruction, "connector event instruction")
        if len(instruction) > 32_000:
            raise ContractError("connector event instruction is too large")
        external_resource_id = _exact_external_ref(
            external_resource_id, "connector event resource id"
        )
        if not isinstance(payload, Mapping):
            raise ContractError("connector event payload must be an object")
        payload_json = _json(dict(payload))
        if len(payload_json.encode("utf-8")) > 256 * 1024:
            raise ContractError("connector event payload is too large")
        external_thread_id = str(external_thread_id or "").strip()
        destination_conversation_id = _optional_id(destination_conversation_id, "connector destination conversation id")
        destination_thread_id = _optional_id(destination_thread_id, "connector destination thread id")
        key = _required_id(idempotency_key or connector_event_id, "connector event idempotency key")
        now = _utc_now()
        values = (
            connector_event_id,
            workspace_id,
            identity_id,
            external_event_id,
            external_thread_id,
            external_resource_id,
            event_type,
            instruction,
            requester_actor_id,
            target_agent_id,
            destination_conversation_id,
            destination_thread_id,
            payload_json,
            key,
            now,
            now,
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, requester_actor_id)
            _require_agent_workspace(connection, workspace_id, target_agent_id)
            identity = connection.execute(
                "SELECT workspace_id, status FROM team_external_identities WHERE external_identity_record_id = ?",
                (identity_id,),
            ).fetchone()
            if identity is None or identity["workspace_id"] != workspace_id:
                raise ContractError("connector event identity is missing or belongs to another workspace")
            for relation_id, table, column, label in (
                (destination_conversation_id, "conversations", "conversation_id", "connector destination conversation"),
                (destination_thread_id, "threads", "thread_id", "connector destination thread"),
            ):
                if relation_id is None:
                    continue
                relation = connection.execute(
                    f"SELECT workspace_id FROM {table} WHERE {column} = ?" if table == "conversations" else "SELECT c.workspace_id FROM threads AS t JOIN conversations AS c ON c.conversation_id = t.conversation_id WHERE t.thread_id = ?",
                    (relation_id,),
                ).fetchone()
                if relation is None or relation["workspace_id"] != workspace_id:
                    raise ContractError(f"{label} is missing or belongs to another workspace")
            if destination_thread_id is not None and destination_conversation_id is not None:
                thread_parent = connection.execute(
                    "SELECT conversation_id FROM threads WHERE thread_id = ?",
                    (destination_thread_id,),
                ).fetchone()
                if thread_parent["conversation_id"] != destination_conversation_id:
                    raise ContractError(
                        "connector destination thread is outside the destination conversation"
                    )
            existing = connection.execute(
                "SELECT * FROM team_connector_events WHERE workspace_id = ? AND idempotency_key = ?",
                (workspace_id, key),
            ).fetchone()
            if existing is None:
                existing = connection.execute(
                    "SELECT * FROM team_connector_events WHERE workspace_id = ? AND external_identity_record_id = ? AND external_event_id = ?",
                    (workspace_id, identity_id, external_event_id),
                ).fetchone()
            if existing is not None:
                if all(
                    existing[column] == expected
                    for column, expected in {
                        "connector_event_id": connector_event_id,
                        "external_identity_record_id": identity_id,
                        "external_event_id": external_event_id,
                        "external_thread_id": external_thread_id,
                        "external_resource_id": external_resource_id,
                        "requester_actor_id": requester_actor_id,
                        "target_agent_id": target_agent_id,
                        "event_type": event_type,
                        "instruction": instruction,
                        "payload_json": payload_json,
                        "idempotency_key": key,
                    }.items()
                ):
                    return _connector_event_row(existing)
                raise PersistenceConflictError("connector event idempotency key has a different payload")
            try:
                connection.execute(
                    """
                    INSERT INTO team_connector_events
                        (connector_event_id, workspace_id, external_identity_record_id,
                         external_event_id, external_thread_id, external_resource_id,
                         event_type, instruction,
                         requester_actor_id, target_agent_id, destination_conversation_id,
                         destination_thread_id, payload_json, idempotency_key, status,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("connector event conflicts with existing state") from error
            row = connection.execute(
                "SELECT * FROM team_connector_events WHERE connector_event_id = ?",
                (connector_event_id,),
            ).fetchone()
        assert row is not None
        return _connector_event_row(row)

    def get_event(self, connector_event_id: str) -> Optional[dict[str, Any]]:
        connector_event_id = _required_id(connector_event_id, "connector event id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM team_connector_events WHERE connector_event_id = ?",
                (connector_event_id,),
            ).fetchone()
        return _connector_event_row(row) if row is not None else None

    def list_events(self, workspace_id: str, *, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "connector event workspace id")
        _require_page_limit(limit)
        params: list[Any] = [workspace_id]
        where = "workspace_id = ?"
        if status is not None:
            status = _required_kind(status, "connector event status")
            if status not in {"received", "routed", "failed", "ignored", "completed"}:
                raise ContractError("connector event status is unsupported")
            where += " AND status = ?"
            params.append(status)
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM team_connector_events WHERE {where} ORDER BY created_at, connector_event_id LIMIT ?",
                params,
            ).fetchall()
        return [_connector_event_row(row) for row in rows]

    def transition_event(
        self,
        connector_event_id: str,
        status: str,
        *,
        request_id: Optional[str] = None,
        error_detail: Optional[str] = None,
    ) -> dict[str, Any]:
        connector_event_id = _required_id(connector_event_id, "connector event id")
        status = _required_kind(status, "connector event status")
        allowed = {"received", "routed", "failed", "ignored", "completed"}
        if status not in allowed:
            raise ContractError("connector event status is unsupported")
        request_id = _optional_id(request_id, "connector event request id")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_connector_events WHERE connector_event_id = ?",
                (connector_event_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"connector event not found: {connector_event_id}")
            edges = {
                "received": {"routed", "failed", "ignored"},
                "routed": {"completed", "failed"},
                "failed": {"received", "ignored"},
                "ignored": set(),
                "completed": set(),
            }
            if row["status"] != status:
                if status not in edges[row["status"]]:
                    raise ContractError("invalid connector event status transition")
                connection.execute(
                    "UPDATE team_connector_events SET status = ?, request_id = COALESCE(?, request_id), error_detail = ?, updated_at = ? WHERE connector_event_id = ?",
                    (status, request_id, str(error_detail or "").strip() or None, _utc_now(), connector_event_id),
                )
            updated = connection.execute(
                "SELECT * FROM team_connector_events WHERE connector_event_id = ?",
                (connector_event_id,),
            ).fetchone()
        assert updated is not None
        return _connector_event_row(updated)

    def create_outbox(
        self,
        delivery_id: str,
        workspace_id: str,
        connector_event_id: str,
        external_identity_record_id: str,
        payload: Mapping[str, Any],
        *,
        external_resource_id: str,
        external_thread_id: str = "",
        run_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "connector delivery id")
        workspace_id = _required_id(workspace_id, "connector delivery workspace id")
        connector_event_id = _required_id(connector_event_id, "connector event id")
        external_identity_record_id = _required_id(external_identity_record_id, "external identity record id")
        external_resource_id = _exact_external_ref(
            external_resource_id, "connector delivery resource id"
        )
        if not isinstance(payload, Mapping):
            raise ContractError("connector outbound payload must be an object")
        payload_json = _json(dict(payload))
        if len(payload_json.encode("utf-8")) > 256 * 1024:
            raise ContractError("connector outbound payload is too large")
        run_id = _optional_id(run_id, "connector delivery run id")
        key = _required_id(idempotency_key or delivery_id, "connector delivery idempotency key")
        now = _utc_now()
        values = (
            delivery_id,
            workspace_id,
            connector_event_id,
            external_identity_record_id,
            run_id,
            external_resource_id,
            str(external_thread_id or "").strip(),
            payload_json,
            key,
            now,
            now,
        )
        with self.database.transaction() as connection:
            event = connection.execute(
                "SELECT workspace_id, external_identity_record_id FROM team_connector_events WHERE connector_event_id = ?",
                (connector_event_id,),
            ).fetchone()
            if event is None or event["workspace_id"] != workspace_id or event["external_identity_record_id"] != external_identity_record_id:
                raise ContractError("connector outbound event is missing or belongs to another identity")
            if run_id is not None:
                run = connection.execute(
                    "SELECT workspace_id FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run is None or run["workspace_id"] != workspace_id:
                    raise ContractError("connector outbound run is missing or belongs to another workspace")
            existing = connection.execute(
                "SELECT * FROM team_connector_outbox WHERE workspace_id = ? AND idempotency_key = ?",
                (workspace_id, key),
            ).fetchone()
            if existing is not None:
                if all(
                    existing[column] == expected
                    for column, expected in {
                        "connector_delivery_id": delivery_id,
                        "connector_event_id": connector_event_id,
                        "external_identity_record_id": external_identity_record_id,
                        "external_resource_id": external_resource_id,
                        "external_thread_id": values[6],
                        "payload_json": payload_json,
                    }.items()
                ):
                    return _connector_outbox_row(existing)
                raise PersistenceConflictError("connector outbound idempotency key has a different payload")
            try:
                connection.execute(
                    """
                    INSERT INTO team_connector_outbox
                        (connector_delivery_id, workspace_id, connector_event_id,
                         external_identity_record_id, run_id, external_resource_id,
                         external_thread_id,
                         payload_json, idempotency_key, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("connector outbound conflicts with existing state") from error
            row = connection.execute(
                "SELECT * FROM team_connector_outbox WHERE connector_delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        assert row is not None
        return _connector_outbox_row(row)

    def get_outbox(self, delivery_id: str) -> Optional[dict[str, Any]]:
        delivery_id = _required_id(delivery_id, "connector delivery id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM team_connector_outbox WHERE connector_delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        return _connector_outbox_row(row) if row is not None else None

    def list_outbox(
        self, workspace_id: str, *, statuses: Sequence[str] = (), limit: int = 100
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "connector delivery workspace id")
        _require_page_limit(limit)
        normalized = tuple(_required_kind(item, "connector delivery status") for item in statuses)
        if any(item not in {"queued", "sending", "sent", "failed", "canceled"} for item in normalized):
            raise ContractError("connector delivery status is unsupported")
        params: list[Any] = [workspace_id]
        where = "workspace_id = ?"
        if normalized:
            where += " AND status IN (" + ",".join("?" for _ in normalized) + ")"
            params.extend(normalized)
        params.append(limit)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM team_connector_outbox WHERE {where} ORDER BY created_at, connector_delivery_id LIMIT ?",
                params,
            ).fetchall()
        return [_connector_outbox_row(row) for row in rows]

    def transition_outbox(
        self, delivery_id: str, status: str, *, last_error: Optional[str] = None
    ) -> dict[str, Any]:
        delivery_id = _required_id(delivery_id, "connector delivery id")
        status = _required_kind(status, "connector delivery status")
        if status not in {"queued", "sending", "sent", "failed", "canceled"}:
            raise ContractError("connector delivery status is unsupported")
        edges = {
            "queued": {"sending", "failed", "canceled"},
            "sending": {"sent", "failed", "canceled"},
            "failed": {"queued", "canceled"},
            "sent": set(),
            "canceled": set(),
        }
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_connector_outbox WHERE connector_delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"connector delivery not found: {delivery_id}")
            if row["status"] != status:
                if status not in edges[row["status"]]:
                    raise ContractError("invalid connector delivery status transition")
                now = _utc_now()
                connection.execute(
                    """
                    UPDATE team_connector_outbox
                    SET status = ?, attempt_count = attempt_count + ?,
                        last_error = ?, updated_at = ?,
                        sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END
                    WHERE connector_delivery_id = ?
                    """,
                    (
                        status,
                        1 if status == "sending" else 0,
                        str(last_error or "").strip() or None,
                        now,
                        status,
                        now,
                        delivery_id,
                    ),
                )
            updated = connection.execute(
                "SELECT * FROM team_connector_outbox WHERE connector_delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        assert updated is not None
        return _connector_outbox_row(updated)

    def claim_outbox(self, delivery_id: str) -> Optional[dict[str, Any]]:
        """Atomically claim one outbound item for transport execution.

        ``sending`` is a real ownership boundary, not an ordinary idempotent
        transition.  A second worker must observe that another worker owns the
        item and return without invoking the external provider.  Failed items
        can be retried directly; the transition and attempt increment happen in
        the same transaction so two workers cannot both claim one item.
        """

        delivery_id = _required_id(delivery_id, "connector delivery id")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM team_connector_outbox WHERE connector_delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"connector delivery not found: {delivery_id}")
            if row["status"] in {"sent", "canceled", "sending"}:
                return None if row["status"] == "sending" else _connector_outbox_row(row)
            if row["status"] not in {"queued", "failed"}:
                raise ContractError("connector delivery cannot be claimed from its current state")
            now = _utc_now()
            updated_count = connection.execute(
                """
                UPDATE team_connector_outbox
                SET status = 'sending', attempt_count = attempt_count + 1,
                    last_error = NULL, updated_at = ?
                WHERE connector_delivery_id = ? AND status IN ('queued', 'failed')
                """,
                (now, delivery_id),
            ).rowcount
            if updated_count != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM team_connector_outbox WHERE connector_delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        assert updated is not None
        return _connector_outbox_row(updated)


class ArtifactRepository:
    """Immutable artifact publications plus explicit aggregate links."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def publish(self, publication: ArtifactPublication, storage_ref: str) -> dict[str, Any]:
        if not isinstance(publication, ArtifactPublication):
            raise ContractError("publication must be an ArtifactPublication")
        storage_ref = _require_text(storage_ref, "artifact storage reference")
        if (
            storage_ref.startswith(("/", "\\", "file:", "sandbox:"))
            or "://" not in storage_ref
            or ".." in storage_ref.split("://", 1)[1].split("/")
        ):
            raise ContractError("artifact storage reference must be an opaque non-sandbox URI")
        values = publication.to_dict()
        source_ref_kind = publication.source_ref.kind if publication.source_ref else None
        source_ref_id = publication.source_ref.id if publication.source_ref else None
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, publication.workspace_id, publication.publisher.id)
            _require_workspace_actor(connection, publication.workspace_id, publication.requester_id)
            source_run = connection.execute(
                "SELECT workspace_id FROM runs WHERE run_id = ?", (publication.source_run_id,)
            ).fetchone()
            if source_run is None or source_run["workspace_id"] != publication.workspace_id:
                raise ContractError("artifact source run is missing or belongs to another workspace")
            if publication.source_task_id is not None:
                source_task = connection.execute(
                    "SELECT workspace_id FROM tasks WHERE task_id = ?",
                    (publication.source_task_id,),
                ).fetchone()
                if source_task is None or source_task["workspace_id"] != publication.workspace_id:
                    raise ContractError("artifact source task is missing or belongs to another workspace")
            if publication.source_ref is not None and publication.source_ref.kind in {
                "run", "task", "message", "conversation", "approval"
            }:
                table = {
                    "run": ("runs", "run_id"),
                    "task": ("tasks", "task_id"),
                    "message": ("messages", "message_id"),
                    "conversation": ("conversations", "conversation_id"),
                    "approval": ("approvals", "approval_id"),
                }[publication.source_ref.kind]
                source = connection.execute(
                    f"SELECT workspace_id FROM {table[0]} WHERE {table[1]} = ?",
                    (publication.source_ref.id,),
                ).fetchone()
                if source is None or source["workspace_id"] != publication.workspace_id:
                    raise ContractError("artifact source reference is missing or belongs to another workspace")
            existing = connection.execute(
                "SELECT * FROM artifact_revisions WHERE artifact_id = ? AND idempotency_key = ?",
                (publication.artifact_id, publication.idempotency_key),
            ).fetchone()
            if existing is not None:
                if _artifact_revision_matches(existing, publication, storage_ref):
                    return _artifact_row_for_revision(connection, existing)
                raise PersistenceConflictError("artifact idempotency key has a different payload")
            try:
                connection.execute(
                    """
                    INSERT INTO artifacts
                        (artifact_id, workspace_id, status, visibility, scope_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        publication.artifact_id,
                        publication.workspace_id,
                        ArtifactStatus.PUBLISHED.value,
                        publication.visibility.value,
                        publication.scope_id,
                        publication.created_at,
                    ),
                )
            except sqlite3.IntegrityError:
                artifact = connection.execute(
                    "SELECT * FROM artifacts WHERE artifact_id = ?", (publication.artifact_id,)
                ).fetchone()
                if artifact is None:
                    raise PersistenceConflictError("artifact conflicts with an existing relation")
                if artifact["workspace_id"] != publication.workspace_id:
                    raise PersistenceConflictError("artifact id belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO artifact_revisions
                        (artifact_revision_id, artifact_id, revision_number, source_run_id,
                         source_task_id, source_ref_kind, source_ref_id, publisher_actor_id,
                         requester_actor_id, filename, media_type, size_bytes, sha256,
                         storage_ref, metadata_json, idempotency_key, correlation_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        publication.revision_id,
                        publication.artifact_id,
                        _revision_number(connection, publication.artifact_id),
                        publication.source_run_id,
                        publication.source_task_id,
                        source_ref_kind,
                        source_ref_id,
                        publication.publisher.id,
                        publication.requester_id,
                        publication.filename,
                        publication.media_type,
                        publication.size_bytes,
                        publication.sha256,
                        storage_ref,
                        _json(values["metadata"]),
                        publication.idempotency_key,
                        publication.correlation_id,
                        publication.created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM artifact_revisions WHERE artifact_revision_id = ?",
                    (publication.revision_id,),
                ).fetchone()
                if publication.source_ref is not None:
                    connection.execute(
                        """
                        INSERT INTO artifact_links
                            (artifact_id, artifact_revision_id, linked_aggregate_type,
                             linked_aggregate_id, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            publication.artifact_id,
                            publication.revision_id,
                            source_ref_kind,
                            source_ref_id,
                            publication.created_at,
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("artifact publication references a missing run or actor") from error
        assert row is not None
        return _artifact_row_for_revision(self.database._conn, row)

    def get_artifact(self, artifact_id: str) -> Optional[dict[str, Any]]:
        artifact_id = _required_id(artifact_id, "artifact id")
        with self.database._lock:
            artifact = self.database._conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if artifact is None:
                return None
            revisions = self.database._conn.execute(
                "SELECT * FROM artifact_revisions WHERE artifact_id = ? ORDER BY revision_number",
                (artifact_id,),
            ).fetchall()
            links = self.database._conn.execute(
                "SELECT * FROM artifact_links WHERE artifact_id = ? ORDER BY created_at, artifact_revision_id",
                (artifact_id,),
            ).fetchall()
        return {
            **dict(artifact),
            "revisions": [_artifact_revision_row(revision) for revision in revisions],
            "links": [dict(link) for link in links],
        }

    def get_accessible_artifact(
        self, artifact_id: str, workspace_id: str, actor_id: str
    ) -> Optional[dict[str, Any]]:
        """Read one artifact only after evaluating its durable visibility boundary."""
        artifact_id = _required_id(artifact_id, "artifact id")
        workspace_id = _required_id(workspace_id, "artifact workspace id")
        actor_id = _required_id(actor_id, "artifact requester actor id")
        if artifact_id not in self.list_accessible_artifact_ids(workspace_id, actor_id):
            return None
        artifact = self.get_artifact(artifact_id)
        if artifact is None or artifact["workspace_id"] != workspace_id:
            return None
        return artifact

    def list_accessible_artifact_ids(self, workspace_id: str, actor_id: str) -> set[str]:
        """Return artifact ids visible to one actor, without exposing raw storage refs.

        Visibility is evaluated from the durable aggregate boundary.  In particular,
        a workspace membership is not enough to read a DM/group artifact unless the
        actor is still a member of that conversation.
        """
        workspace_id = _required_id(workspace_id, "workspace id")
        actor_id = _required_id(actor_id, "artifact requester actor id")
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT DISTINCT a.artifact_id
                FROM artifacts AS a
                WHERE a.workspace_id = ? AND a.status <> 'deleted'
                  AND a.quarantined_at IS NULL
                  AND (
                    a.visibility = 'workspace'
                    OR (
                        a.visibility = 'agent_private'
                        AND EXISTS (
                            SELECT 1 FROM agent_installations AS i
                            WHERE i.agent_id = a.scope_id AND i.actor_id = ?
                        )
                    )
                    OR (
                        a.visibility IN ('direct_message', 'group')
                        AND EXISTS (
                            SELECT 1 FROM conversation_members AS cm
                            WHERE cm.conversation_id = a.scope_id
                              AND cm.actor_id = ? AND cm.left_at IS NULL
                        )
                    )
                    OR (
                        a.visibility = 'task'
                        AND EXISTS (
                            SELECT 1 FROM tasks AS t
                            WHERE t.task_id = a.scope_id AND t.workspace_id = ?
                        )
                    )
                  )
                """,
                (workspace_id, actor_id, actor_id, workspace_id),
            ).fetchall()
        return {str(row["artifact_id"]) for row in rows}

    def list_accessible_artifacts(
        self, workspace_id: str, actor_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        _require_page_limit(limit)
        artifact_ids = self.list_accessible_artifact_ids(workspace_id, actor_id)
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT artifact_id FROM artifacts
                WHERE workspace_id = ? AND artifact_id IN ({})
                ORDER BY created_at DESC, artifact_id
                LIMIT ?
                """.format(",".join("?" for _ in artifact_ids) or "NULL"),
                (workspace_id, *sorted(artifact_ids), limit),
            ).fetchall()
        return [
            artifact
            for row in rows
            if (artifact := self.get_artifact(row["artifact_id"])) is not None
        ]

    def transition(self, artifact_id: str, target: ArtifactStatus | str, *, actor_id: str) -> dict[str, Any]:
        artifact_id = _required_id(artifact_id, "artifact id")
        actor_id = _required_id(actor_id, "artifact actor id")
        target = _as_enum(ArtifactStatus, target, "artifact status")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"artifact not found: {artifact_id}")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            current = ArtifactStatus(row["status"])
            require_transition(current, target, ARTIFACT_EDGES)
            now = _utc_now()
            connection.execute(
                """
                UPDATE artifacts
                SET status = ?,
                    archived_at = CASE WHEN ? = 'archived' THEN ? WHEN ? = 'published' THEN NULL ELSE archived_at END,
                    deleted_at = CASE WHEN ? = 'deleted' THEN ? ELSE deleted_at END
                WHERE artifact_id = ?
                """,
                (target.value, target.value, now, target.value, target.value, now, artifact_id),
            )
            updated = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        assert updated is not None
        return self.get_artifact(artifact_id) or dict(updated)

    def quarantine(self, artifact_id: str, *, actor_id: str, reason: str) -> dict[str, Any]:
        artifact_id = _required_id(artifact_id, "artifact id")
        actor_id = _required_id(actor_id, "artifact actor id")
        reason = _require_text(reason, "quarantine reason")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT workspace_id, status FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if row is None:
                raise ContractError(f"artifact not found: {artifact_id}")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            if row["status"] == ArtifactStatus.DELETED.value:
                raise ContractError("deleted artifacts cannot be quarantined")
            connection.execute(
                "UPDATE artifacts SET quarantined_at = ?, quarantine_reason = ? WHERE artifact_id = ?",
                (_utc_now(), reason, artifact_id),
            )
        return self.get_artifact(artifact_id) or {}

    def release_quarantine(self, artifact_id: str, *, actor_id: str) -> dict[str, Any]:
        artifact_id = _required_id(artifact_id, "artifact id")
        actor_id = _required_id(actor_id, "artifact actor id")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT workspace_id FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"artifact not found: {artifact_id}")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            connection.execute(
                "UPDATE artifacts SET quarantined_at = NULL, quarantine_reason = NULL WHERE artifact_id = ?",
                (artifact_id,),
            )
        return self.get_artifact(artifact_id) or {}

    def link(
        self,
        artifact_id: str,
        aggregate: ResourceRef,
        *,
        revision_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(aggregate, ResourceRef):
            raise ContractError("artifact link aggregate must be a ResourceRef")
        artifact_id = _required_id(artifact_id, "artifact id")
        with self.database.transaction() as connection:
            artifact = connection.execute(
                "SELECT workspace_id FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if artifact is None:
                raise ContractError(f"artifact not found: {artifact_id}")
            revision = revision_id or connection.execute(
                "SELECT artifact_revision_id FROM artifact_revisions WHERE artifact_id = ? ORDER BY revision_number DESC LIMIT 1",
                (artifact_id,),
            ).fetchone()["artifact_revision_id"]
            revision_row = connection.execute(
                "SELECT artifact_id FROM artifact_revisions WHERE artifact_revision_id = ?",
                (revision,),
            ).fetchone()
            if revision_row is None or revision_row["artifact_id"] != artifact_id:
                raise ContractError("artifact revision does not belong to the artifact")
            try:
                connection.execute(
                    """
                    INSERT INTO artifact_links
                        (artifact_id, artifact_revision_id, linked_aggregate_type,
                         linked_aggregate_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (artifact_id, revision, aggregate.kind, aggregate.id, _utc_now()),
                )
            except sqlite3.IntegrityError:
                pass
            row = connection.execute(
                """
                SELECT * FROM artifact_links
                WHERE artifact_id = ? AND artifact_revision_id = ?
                  AND linked_aggregate_type = ? AND linked_aggregate_id = ?
                """,
                (artifact_id, revision, aggregate.kind, aggregate.id),
            ).fetchone()
        assert row is not None
        return dict(row)

    def compare_revisions(self, artifact_id: str, left_revision_id: str, right_revision_id: str) -> dict[str, Any]:
        artifact_id = _required_id(artifact_id, "artifact id")
        left_revision_id = _required_id(left_revision_id, "left artifact revision id")
        right_revision_id = _required_id(right_revision_id, "right artifact revision id")
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM artifact_revisions WHERE artifact_id = ? AND artifact_revision_id IN (?, ?)",
                (artifact_id, left_revision_id, right_revision_id),
            ).fetchall()
        revisions = {row["artifact_revision_id"]: _artifact_revision_row(row) for row in rows}
        if set(revisions) != {left_revision_id, right_revision_id}:
            raise ContractError("artifact revisions are missing or do not belong to the artifact")
        left, right = revisions[left_revision_id], revisions[right_revision_id]
        fields = (
            "filename", "media_type", "size_bytes", "sha256", "metadata",
            "source_run_id", "source_task_id",
        )
        return {
            "artifact_id": artifact_id,
            "left_revision_id": left_revision_id,
            "right_revision_id": right_revision_id,
            "changes": {
                field: {"left": left[field], "right": right[field]}
                for field in fields if left.get(field) != right.get(field)
            },
        }


class ContextRepository:
    """Explicit, auditable context-boundary crossings."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_share(self, share: ContextShare) -> dict[str, Any]:
        if not isinstance(share, ContextShare):
            raise ContractError("share must be a ContextShare")
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, share.workspace_id, share.shared_by.id)
            existing = connection.execute(
                "SELECT * FROM context_shares WHERE workspace_id = ? AND idempotency_key = ?",
                (share.workspace_id, share.idempotency_key),
            ).fetchone()
            if existing is not None:
                if _context_share_matches(existing, share):
                    return dict(existing)
                raise PersistenceConflictError("context-share idempotency key has a different payload")
            try:
                connection.execute(
                    """
                    INSERT INTO context_shares
                        (context_share_id, workspace_id, resource_kind, resource_id,
                         source_scope_kind, source_scope_id, target_scope_kind, target_scope_id,
                         shared_by_actor_id, idempotency_key, correlation_id, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        share.share_id,
                        share.workspace_id,
                        share.resource.kind,
                        share.resource.id,
                        share.source.scope.value,
                        share.source.scope_id,
                        share.target.scope.value,
                        share.target.scope_id,
                        share.shared_by.id,
                        share.idempotency_key,
                        share.correlation_id,
                        share.reason,
                        share.created_at,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM context_shares WHERE context_share_id = ?", (share.share_id,)
                ).fetchone()
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("context share references a missing actor") from error
        assert row is not None
        return dict(row)

    def list_scope_shares(self, workspace_id: str, scope: ContextScope | str, scope_id: str) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "workspace id")
        scope = _as_enum(ContextScope, scope, "context scope")
        scope_id = _required_id(scope_id, "context scope id")
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT * FROM context_shares
                WHERE workspace_id = ? AND target_scope_kind = ? AND target_scope_id = ?
                ORDER BY created_at, context_share_id
                """,
                (workspace_id, scope.value, scope_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_share(self, share_id: str) -> Optional[dict[str, Any]]:
        share_id = _required_id(share_id, "context share id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM context_shares WHERE context_share_id = ?", (share_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_resource_shares(
        self, workspace_id: str, resource: ResourceRef
    ) -> list[dict[str, Any]]:
        if not isinstance(resource, ResourceRef):
            raise ContractError("shared resource must be a ResourceRef")
        workspace_id = _required_id(workspace_id, "context share workspace id")
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT * FROM context_shares
                WHERE workspace_id = ? AND resource_kind = ? AND resource_id = ?
                ORDER BY created_at, context_share_id
                """,
                (workspace_id, resource.kind, resource.id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_memory(self, memory_id: str) -> Optional[dict[str, Any]]:
        memory_id = _required_id(memory_id, "memory id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def get_pin(self, pin_id: str) -> Optional[dict[str, Any]]:
        pin_id = _required_id(pin_id, "context pin id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM context_pins WHERE context_pin_id = ?", (pin_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_memories(
        self,
        workspace_id: str,
        scope: ContextScope | str,
        scope_id: str,
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "memory workspace id")
        scope = _as_enum(ContextScope, scope, "context scope")
        scope_id = _required_id(scope_id, "memory scope id")
        where = [
            "workspace_id = ?",
            "scope_kind = ?",
            "scope_id = ?",
            "deleted_at IS NULL",
        ]
        params: list[Any] = [workspace_id, scope.value, scope_id]
        if not include_expired:
            where.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_utc_now())
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM memory_records WHERE {' AND '.join(where)} ORDER BY created_at, memory_id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_pins(
        self,
        workspace_id: str,
        scope: ContextScope | str,
        scope_id: str,
        *,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        workspace_id = _required_id(workspace_id, "context pin workspace id")
        scope = _as_enum(ContextScope, scope, "context scope")
        scope_id = _required_id(scope_id, "context pin scope id")
        where = [
            "workspace_id = ?",
            "scope_kind = ?",
            "scope_id = ?",
        ]
        params: list[Any] = [workspace_id, scope.value, scope_id]
        if not include_expired:
            where.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_utc_now())
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM context_pins WHERE {' AND '.join(where)} ORDER BY created_at, context_pin_id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_memory(
        self,
        memory_id: str,
        workspace_id: str,
        scope: ContextScope | str,
        scope_id: str,
        content: str,
        created_by_actor_id: str,
        *,
        source_artifact_id: Optional[str] = None,
        source_run_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        values = (
            _required_id(memory_id, "memory id"),
            _required_id(workspace_id, "workspace id"),
            _as_enum(ContextScope, scope, "context scope").value,
            _required_id(scope_id, "context scope id"),
            _require_text(content, "memory content"),
            _optional_id(source_artifact_id, "memory source artifact id"),
            _optional_id(source_run_id, "memory source run id"),
            _required_id(created_by_actor_id, "memory author actor id"),
            _utc_now(),
            expires_at,
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, created_by_actor_id)
            if source_artifact_id is not None:
                artifact = connection.execute(
                    "SELECT workspace_id FROM artifacts WHERE artifact_id = ?", (source_artifact_id,)
                ).fetchone()
                if artifact is None or artifact["workspace_id"] != workspace_id:
                    raise ContractError("memory source artifact is missing or belongs to another workspace")
            if source_run_id is not None:
                run = connection.execute(
                    "SELECT workspace_id FROM runs WHERE run_id = ?", (source_run_id,)
                ).fetchone()
                if run is None or run["workspace_id"] != workspace_id:
                    raise ContractError("memory source run is missing or belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO memory_records
                        (memory_id, workspace_id, scope_kind, scope_id, content,
                         source_artifact_id, source_run_id, created_by_actor_id,
                         created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("memory record references a missing relation") from error
            row = connection.execute("SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)).fetchone()
        assert row is not None
        return dict(row)

    def update_memory(
        self,
        memory_id: str,
        content: str,
        *,
        actor_id: str,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        memory_id = _required_id(memory_id, "memory id")
        actor_id = _required_id(actor_id, "memory editor actor id")
        content = _require_text(content, "memory content")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"memory not found: {memory_id}")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            if row["deleted_at"] is not None:
                raise ContractError("deleted memory cannot be edited")
            connection.execute(
                "UPDATE memory_records SET content = ?, expires_at = ? WHERE memory_id = ?",
                (content, expires_at, memory_id),
            )
            updated = connection.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def delete_memory(self, memory_id: str, *, actor_id: str) -> dict[str, Any]:
        memory_id = _required_id(memory_id, "memory id")
        actor_id = _required_id(actor_id, "memory actor id")
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise ContractError(f"memory not found: {memory_id}")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            if row["deleted_at"] is None:
                connection.execute(
                    "UPDATE memory_records SET deleted_at = ? WHERE memory_id = ?",
                    (_utc_now(), memory_id),
                )
            updated = connection.execute(
                "SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        assert updated is not None
        return dict(updated)

    def pin_context(
        self,
        pin_id: str,
        workspace_id: str,
        scope: ContextScope | str,
        scope_id: str,
        resource: ResourceRef,
        pinned_by_actor_id: str,
        *,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(resource, ResourceRef):
            raise ContractError("pinned resource must be a ResourceRef")
        values = (
            _required_id(pin_id, "context pin id"),
            _required_id(workspace_id, "workspace id"),
            _as_enum(ContextScope, scope, "context scope").value,
            _required_id(scope_id, "context scope id"),
            resource.kind,
            resource.id,
            _required_id(pinned_by_actor_id, "context pin actor id"),
            _utc_now(),
            expires_at,
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, pinned_by_actor_id)
            existing = connection.execute(
                """
                SELECT * FROM context_pins
                WHERE scope_kind = ? AND scope_id = ?
                  AND resource_kind = ? AND resource_id = ?
                """,
                (values[2], values[3], values[4], values[5]),
            ).fetchone()
            if existing is not None:
                if existing["workspace_id"] == workspace_id:
                    return dict(existing)
                raise PersistenceConflictError("context pin belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO context_pins
                        (context_pin_id, workspace_id, scope_kind, scope_id, resource_kind,
                         resource_id, pinned_by_actor_id, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("context pin already exists or references a missing actor") from error
            row = connection.execute("SELECT * FROM context_pins WHERE context_pin_id = ?", (pin_id,)).fetchone()
        assert row is not None
        return dict(row)

    def unpin_context(
        self,
        workspace_id: str,
        scope: ContextScope | str,
        scope_id: str,
        resource: ResourceRef,
        *,
        actor_id: str,
    ) -> bool:
        if not isinstance(resource, ResourceRef):
            raise ContractError("pinned resource must be a ResourceRef")
        workspace_id = _required_id(workspace_id, "context pin workspace id")
        scope = _as_enum(ContextScope, scope, "context scope")
        scope_id = _required_id(scope_id, "context scope id")
        actor_id = _required_id(actor_id, "context pin actor id")
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, actor_id)
            cursor = connection.execute(
                """
                DELETE FROM context_pins
                WHERE workspace_id = ? AND scope_kind = ? AND scope_id = ?
                  AND resource_kind = ? AND resource_id = ?
                """,
                (workspace_id, scope.value, scope_id, resource.kind, resource.id),
            )
        return cursor.rowcount == 1


class GovernanceRepository:
    """Approval, grant, connector, audit, and notification records."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create_approval(
        self,
        approval_id: str,
        workspace_id: str,
        requested_by_actor_id: str,
        action_kind: str,
        resource: ResourceRef,
        reason: str,
        *,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(resource, ResourceRef):
            raise ContractError("approval resource must be a ResourceRef")
        values = (
            _required_id(approval_id, "approval id"),
            _required_id(workspace_id, "workspace id"),
            _optional_id(request_id, "approval request id"),
            _optional_id(run_id, "approval run id"),
            _required_id(requested_by_actor_id, "approval requester actor id"),
            _require_kind_or_text(action_kind, "approval action kind"),
            resource.kind,
            resource.id,
            _require_text(reason, "approval reason"),
            expires_at,
            _utc_now(),
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, requested_by_actor_id)
            for table, column, referenced_id, label in (
                ("work_requests", "request_id", request_id, "approval request"),
                ("runs", "run_id", run_id, "approval run"),
            ):
                if referenced_id is None:
                    continue
                referenced = connection.execute(
                    f"SELECT workspace_id FROM {table} WHERE {column} = ?",
                    (referenced_id,),
                ).fetchone()
                if referenced is None:
                    raise ContractError(f"{label} not found: {referenced_id}")
                if referenced["workspace_id"] != workspace_id:
                    raise ContractError(f"{label} belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO approvals
                        (approval_id, workspace_id, request_id, run_id, requested_by_actor_id,
                         action_kind, resource_kind, resource_id, status, reason, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("approval references a missing work record") from error
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        assert row is not None
        return dict(row)

    def get_approval(self, approval_id: str) -> Optional[dict[str, Any]]:
        approval_id = _required_id(approval_id, "approval id")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_approvals(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        statuses: Sequence[ApprovalStatus | str] = (),
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List approvals only after proving the caller belongs to the workspace."""

        workspace_id = _required_id(workspace_id, "approval workspace id")
        actor_id = _required_id(actor_id, "approval actor id")
        _require_page_limit(limit)
        normalized_statuses = tuple(
            _as_enum(ApprovalStatus, status, "approval status").value for status in statuses
        )
        request_id = _optional_id(request_id, "approval request id")
        run_id = _optional_id(run_id, "approval run id")
        where = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if normalized_statuses:
            where.append("status IN (" + ",".join("?" for _ in normalized_statuses) + ")")
            params.extend(normalized_statuses)
        if request_id is not None:
            where.append("request_id = ?")
            params.append(request_id)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        params.append(limit)
        with self.database._lock:
            _require_workspace_actor(self.database._conn, workspace_id, actor_id)
            rows = self.database._conn.execute(
                f"""
                SELECT * FROM approvals
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC, approval_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def decide_approval(
        self,
        approval_id: str,
        target: ApprovalStatus | str,
        *,
        approver_actor_id: str,
    ) -> dict[str, Any]:
        approval_id = _required_id(approval_id, "approval id")
        approver_actor_id = _required_id(approver_actor_id, "approver actor id")
        target = _as_enum(ApprovalStatus, target, "approval status")
        if target not in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}:
            raise ContractError("approval decision must be approved or denied")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ContractError(f"approval not found: {approval_id}")
            actor = _require_workspace_actor(connection, row["workspace_id"], approver_actor_id)
            if actor["actor_kind"] != ActorKind.HUMAN.value:
                raise ContractError("only a human workspace administrator can decide approvals")
            membership = connection.execute(
                """
                SELECT workspace_role FROM workspace_memberships
                WHERE workspace_id = ? AND actor_id = ? AND status = 'active'
                """,
                (row["workspace_id"], approver_actor_id),
            ).fetchone()
            if membership is None or membership["workspace_role"] not in {"owner", "admin"}:
                raise ContractError("only a workspace owner or admin can decide approvals")
            if row["status"] == ApprovalStatus.PENDING.value and row["expires_at"]:
                now = _utc_now()
                if _timestamp_expired(row["expires_at"], now):
                    connection.execute(
                        "UPDATE approvals SET status = 'expired', decided_at = ? WHERE approval_id = ?",
                        (now, approval_id),
                    )
                    raise ContractError("approval has expired")
            require_transition(ApprovalStatus(row["status"]), target, APPROVAL_EDGES)
            now = _utc_now()
            connection.execute(
                "UPDATE approvals SET status = ?, approver_actor_id = ?, decided_at = ? WHERE approval_id = ?",
                (target.value, approver_actor_id, now, approval_id),
            )
            updated = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def expire_approval(self, approval_id: str, *, now: Optional[str] = None) -> dict[str, Any]:
        approval_id = _required_id(approval_id, "approval id")
        now = now or _utc_now()
        _require_timestamp(now, "approval timestamp")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ContractError(f"approval not found: {approval_id}")
            if row["status"] == ApprovalStatus.PENDING.value:
                if row["expires_at"] is None:
                    raise ContractError("approval has no expiry and cannot be expired automatically")
                if not _timestamp_expired(row["expires_at"], now):
                    raise ContractError("approval has not reached its expiry")
                connection.execute(
                    "UPDATE approvals SET status = 'expired', decided_at = ? WHERE approval_id = ?",
                    (now, approval_id),
                )
            updated = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def cancel_approval(self, approval_id: str, *, actor_id: str) -> dict[str, Any]:
        approval_id = _required_id(approval_id, "approval id")
        actor_id = _required_id(actor_id, "approval actor id")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise ContractError(f"approval not found: {approval_id}")
            if row["requested_by_actor_id"] != actor_id:
                raise ContractError("only the approval requester can cancel it")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            require_transition(ApprovalStatus(row["status"]), ApprovalStatus.CANCELED, APPROVAL_EDGES)
            now = _utc_now()
            connection.execute(
                "UPDATE approvals SET status = 'canceled', decided_at = ? WHERE approval_id = ?",
                (now, approval_id),
            )
            updated = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def approval_is_valid(self, approval_id: str, *, now: Optional[str] = None) -> bool:
        approval_id = _required_id(approval_id, "approval id")
        now = now or _utc_now()
        _require_timestamp(now, "approval timestamp")
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT status, expires_at FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        if row is None or row["status"] != ApprovalStatus.APPROVED.value:
            return False
        return row["expires_at"] is None or not _timestamp_expired(row["expires_at"], now)

    def create_grant(
        self,
        grant_id: str,
        workspace_id: str,
        grantor_actor_id: str,
        grantee_actor_id: str,
        grant_kind: str,
        resource: ResourceRef,
        action_kind: str,
        *,
        agent_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(resource, ResourceRef):
            raise ContractError("grant resource must be a ResourceRef")
        values = (
            _required_id(grant_id, "grant id"),
            _required_id(workspace_id, "workspace id"),
            _required_id(grantor_actor_id, "grantor actor id"),
            _required_id(grantee_actor_id, "grantee actor id"),
            _optional_id(agent_id, "grant agent id"),
            _require_kind_or_text(grant_kind, "grant kind"),
            resource.kind,
            resource.id,
            _require_kind_or_text(action_kind, "grant action kind"),
            expires_at,
            _utc_now(),
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, grantor_actor_id)
            _require_workspace_actor(connection, workspace_id, grantee_actor_id)
            if agent_id is not None:
                _require_agent_workspace(connection, workspace_id, agent_id)
            try:
                connection.execute(
                    """
                    INSERT INTO grants
                        (grant_id, workspace_id, grantor_actor_id, grantee_actor_id,
                         agent_id, grant_kind, resource_kind, resource_id, action_kind,
                         status, expires_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("grant references a missing identity") from error
            row = connection.execute("SELECT * FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
        assert row is not None
        return dict(row)

    def revoke_grant(self, grant_id: str, *, actor_id: str) -> dict[str, Any]:
        grant_id = _required_id(grant_id, "grant id")
        actor_id = _required_id(actor_id, "grant actor id")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
            if row is None:
                raise ContractError(f"grant not found: {grant_id}")
            if row["grantor_actor_id"] != actor_id:
                raise ContractError("only the grantor can revoke this grant")
            _require_workspace_actor(connection, row["workspace_id"], actor_id)
            connection.execute(
                "UPDATE grants SET status = 'revoked', revoked_at = ? WHERE grant_id = ?",
                (_utc_now(), grant_id),
            )
            updated = connection.execute("SELECT * FROM grants WHERE grant_id = ?", (grant_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def bind_connector(
        self,
        binding_id: str,
        workspace_id: str,
        connector_kind: str,
        actor_id: str,
        *,
        external_workspace_id: Optional[str] = None,
        external_identity_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        grant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        actor_id = _required_id(actor_id, "connector actor id")
        values = (
            _required_id(binding_id, "connector binding id"),
            _required_id(workspace_id, "workspace id"),
            _require_kind_or_text(connector_kind, "connector kind"),
            external_workspace_id,
            external_identity_id,
            _optional_id(agent_id, "connector agent id"),
            _optional_id(grant_id, "connector grant id"),
            _utc_now(),
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, actor_id)
            if agent_id is not None:
                _require_agent_workspace(connection, workspace_id, agent_id)
            if grant_id is not None:
                grant = connection.execute(
                    "SELECT workspace_id FROM grants WHERE grant_id = ?", (grant_id,)
                ).fetchone()
                if grant is None or grant["workspace_id"] != workspace_id:
                    raise ContractError("connector grant is missing or belongs to another workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO connector_bindings
                        (connector_binding_id, workspace_id, connector_kind,
                         external_workspace_id, external_identity_id, agent_id,
                         grant_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("connector binding conflicts with an existing identity") from error
            row = connection.execute(
                "SELECT * FROM connector_bindings WHERE connector_binding_id = ?", (binding_id,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def append_audit(
        self,
        audit_event_id: str,
        workspace_id: str,
        action_kind: str,
        aggregate: ResourceRef,
        decision: str,
        correlation_id: str,
        *,
        actor_id: Optional[str] = None,
        requester_actor_id: Optional[str] = None,
        reason: str = "",
    ) -> dict[str, Any]:
        if not isinstance(aggregate, ResourceRef):
            raise ContractError("audit aggregate must be a ResourceRef")
        values = (
            _required_id(audit_event_id, "audit event id"),
            _required_id(workspace_id, "workspace id"),
            _optional_id(actor_id, "audit actor id"),
            _optional_id(requester_actor_id, "audit requester actor id"),
            _require_kind_or_text(action_kind, "audit action kind"),
            aggregate.kind,
            aggregate.id,
            _require_kind_or_text(decision, "audit decision"),
            str(reason or "").strip(),
            _required_id(correlation_id, "audit correlation id"),
            _utc_now(),
        )
        with self.database.transaction() as connection:
            if actor_id is not None:
                _require_workspace_actor(connection, workspace_id, actor_id, allow_system=True)
            if requester_actor_id is not None:
                _require_workspace_actor(connection, workspace_id, requester_actor_id, allow_system=True)
            try:
                connection.execute(
                    """
                    INSERT INTO team_audit_events
                        (audit_event_id, workspace_id, actor_id, requester_actor_id,
                         action_kind, aggregate_type, aggregate_id, decision, reason,
                         correlation_id, occurred_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("audit event references a missing actor") from error
            row = connection.execute("SELECT * FROM team_audit_events WHERE audit_event_id = ?", (audit_event_id,)).fetchone()
        assert row is not None
        return dict(row)

    def create_notification(
        self,
        notification_id: str,
        workspace_id: str,
        recipient_actor_id: str,
        notification_kind: str,
        aggregate: ResourceRef,
        dedupe_key: str,
        *,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        if not isinstance(aggregate, ResourceRef):
            raise ContractError("notification aggregate must be a ResourceRef")
        values = (
            _required_id(notification_id, "notification id"),
            _required_id(workspace_id, "workspace id"),
            _required_id(recipient_actor_id, "notification recipient actor id"),
            _require_kind_or_text(notification_kind, "notification kind"),
            aggregate.kind,
            aggregate.id,
            _required_id(dedupe_key, "notification dedupe key"),
            _utc_now(),
            expires_at,
        )
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, recipient_actor_id, allow_system=True)
            try:
                connection.execute(
                    """
                    INSERT INTO notifications
                        (notification_id, workspace_id, recipient_actor_id, notification_kind,
                         aggregate_type, aggregate_id, dedupe_key, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("notification dedupe key already exists") from error
            row = connection.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,)).fetchone()
        assert row is not None
        return dict(row)

    def mark_notification_read(self, notification_id: str, actor_id: str) -> dict[str, Any]:
        notification_id = _required_id(notification_id, "notification id")
        actor_id = _required_id(actor_id, "notification actor id")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,)).fetchone()
            if row is None:
                raise ContractError(f"notification not found: {notification_id}")
            if row["recipient_actor_id"] != actor_id:
                raise ContractError("only the notification recipient can mark it read")
            _require_workspace_actor(connection, row["workspace_id"], actor_id, allow_system=True)
            connection.execute(
                "UPDATE notifications SET read_at = ? WHERE notification_id = ?",
                (_utc_now(), notification_id),
            )
            updated = connection.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,)).fetchone()
        assert updated is not None
        return dict(updated)

    def list_notifications(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        unread_only: bool = False,
        after_cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List only the caller's notifications with stable cursor pagination."""
        workspace_id = _required_id(workspace_id, "notification workspace id")
        actor_id = _required_id(actor_id, "notification actor id")
        _require_page_limit(limit)
        cursor_created_at, cursor_notification_id = _notification_cursor(after_cursor)
        where = [
            "workspace_id = ?",
            "recipient_actor_id = ?",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        params: list[Any] = [workspace_id, actor_id, _utc_now()]
        if unread_only:
            where.append("read_at IS NULL")
        if cursor_created_at is not None:
            where.append("(created_at > ? OR (created_at = ? AND notification_id > ?))")
            params.extend((cursor_created_at, cursor_created_at, cursor_notification_id))
        params.append(limit + 1)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"""
                SELECT * FROM notifications
                WHERE {' AND '.join(where)}
                ORDER BY created_at, notification_id
                LIMIT ?
                """,
                params,
            ).fetchall()
        notifications = [dict(row) for row in rows[:limit]]
        has_more = len(rows) > limit
        next_cursor = (
            _notification_cursor_encode(
                notifications[-1]["created_at"], notifications[-1]["notification_id"]
            )
            if notifications
            else after_cursor
        )
        return {
            "notifications": notifications,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    def get_notification_preferences(
        self, workspace_id: str, actor_id: str
    ) -> dict[str, Any]:
        workspace_id = _required_id(workspace_id, "notification preference workspace id")
        actor_id = _required_id(actor_id, "notification preference actor id")
        with self.database._lock:
            _require_workspace_actor(
                self.database._conn, workspace_id, actor_id, allow_system=True
            )
            row = self.database._conn.execute(
                "SELECT * FROM notification_preferences WHERE workspace_id = ? AND actor_id = ?",
                (workspace_id, actor_id),
            ).fetchone()
        if row is None:
            return {
                "workspace_id": workspace_id,
                "actor_id": actor_id,
                "muted": False,
                "mention_only": False,
                "quiet_hours": {},
                "event_preferences": {},
                "updated_at": None,
            }
        result = dict(row)
        result["muted"] = bool(result.pop("muted"))
        result["mention_only"] = bool(result.pop("mention_only"))
        result["quiet_hours"] = _json_load(result.pop("quiet_hours_json"), {})
        result["event_preferences"] = _json_load(result.pop("event_preferences_json"), {})
        return result

    def set_notification_preferences(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        muted: Optional[bool] = None,
        mention_only: Optional[bool] = None,
        quiet_hours: Optional[Mapping[str, Any]] = None,
        event_preferences: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        current = self.get_notification_preferences(workspace_id, actor_id)
        muted = current["muted"] if muted is None else muted
        mention_only = current["mention_only"] if mention_only is None else mention_only
        if not isinstance(muted, bool) or not isinstance(mention_only, bool):
            raise ContractError("notification muted and mention_only must be boolean")
        quiet = dict(current["quiet_hours"] if quiet_hours is None else quiet_hours)
        events = dict(
            current["event_preferences"]
            if event_preferences is None
            else event_preferences
        )
        if set(quiet) - {"start", "end", "timezone"}:
            raise ContractError("quiet hours only support start, end, and timezone")
        for key in ("start", "end"):
            if key not in quiet:
                continue
            value = str(quiet[key] or "")
            if len(value) != 5 or value[2] != ":":
                raise ContractError("quiet hour times must use HH:MM")
            try:
                hour, minute = int(value[:2]), int(value[3:])
            except ValueError as error:
                raise ContractError("quiet hour times must use HH:MM") from error
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ContractError("quiet hour times must use HH:MM")
            quiet[key] = f"{hour:02d}:{minute:02d}"
        if "timezone" in quiet and not str(quiet["timezone"] or "").strip():
            raise ContractError("quiet hours timezone cannot be empty")
        if any(not isinstance(value, bool) for value in events.values()):
            raise ContractError("notification event preferences must be boolean")
        workspace_id = _required_id(workspace_id, "notification preference workspace id")
        actor_id = _required_id(actor_id, "notification preference actor id")
        now = _utc_now()
        with self.database.transaction() as connection:
            _require_workspace_actor(connection, workspace_id, actor_id, allow_system=True)
            connection.execute(
                """
                INSERT INTO notification_preferences
                    (workspace_id, actor_id, muted, mention_only,
                     quiet_hours_json, event_preferences_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, actor_id) DO UPDATE SET
                    muted = excluded.muted,
                    mention_only = excluded.mention_only,
                    quiet_hours_json = excluded.quiet_hours_json,
                    event_preferences_json = excluded.event_preferences_json,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_id,
                    actor_id,
                    int(muted),
                    int(mention_only),
                    _json(quiet),
                    _json(events),
                    now,
                ),
            )
        return self.get_notification_preferences(workspace_id, actor_id)


class SearchRepository:
    """Workspace-scoped FTS projections with bounded, opaque cursors."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def index_message(
        self, message_id: str, workspace_id: str, conversation_id: str, body: str
    ) -> None:
        for value, label in (
            (message_id, "message id"),
            (workspace_id, "workspace id"),
            (conversation_id, "conversation id"),
            (body, "message body"),
        ):
            _require_text(value, label)
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM team_message_fts WHERE message_id = ?", (message_id,))
            connection.execute(
                "INSERT INTO team_message_fts (message_id, workspace_id, conversation_id, body) VALUES (?, ?, ?, ?)",
                (message_id, workspace_id, conversation_id, body),
            )

    def remove_message(self, message_id: str) -> None:
        """Remove a tombstoned message from the searchable projection."""

        message_id = _required_id(message_id, "message id")
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM team_message_fts WHERE message_id = ?",
                (message_id,),
            )

    def index_task(
        self,
        task_id: str,
        workspace_id: str,
        title: str,
        description: str,
        acceptance_criteria: str,
    ) -> None:
        for value, label in (
            (task_id, "task id"),
            (workspace_id, "workspace id"),
            (title, "task title"),
        ):
            _require_text(value, label)
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM team_task_fts WHERE task_id = ?", (task_id,))
            connection.execute(
                """
                INSERT INTO team_task_fts
                    (task_id, workspace_id, title, description, acceptance_criteria)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, workspace_id, title, description or "", acceptance_criteria or ""),
            )

    def index_artifact(
        self,
        artifact_id: str,
        workspace_id: str,
        filename: str,
        media_type: str,
        metadata: Mapping[str, Any] | str = "",
    ) -> None:
        for value, label in (
            (artifact_id, "artifact id"),
            (workspace_id, "workspace id"),
            (filename, "artifact filename"),
        ):
            _require_text(value, label)
        metadata_text = metadata if isinstance(metadata, str) else _json(metadata)
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM team_artifact_fts WHERE artifact_id = ?", (artifact_id,))
            connection.execute(
                """
                INSERT INTO team_artifact_fts
                    (artifact_id, workspace_id, filename, media_type, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (artifact_id, workspace_id, filename, media_type or "", metadata_text),
            )

    def search_messages(
        self,
        workspace_id: str,
        query: str,
        *,
        after_cursor: Optional[str] = None,
        limit: int = 50,
        conversation_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        return self._search(
            table="team_message_fts",
            workspace_id=workspace_id,
            query=query,
            after_cursor=after_cursor,
            limit=limit,
            fields=("message_id", "conversation_id", "body"),
            id_field="conversation_id",
            allowed_ids=conversation_ids,
        )

    def search_tasks(
        self,
        workspace_id: str,
        query: str,
        *,
        after_cursor: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._search(
            table="team_task_fts",
            workspace_id=workspace_id,
            query=query,
            after_cursor=after_cursor,
            limit=limit,
            fields=("task_id", "title", "description", "acceptance_criteria"),
        )

    def search_artifacts(
        self,
        workspace_id: str,
        query: str,
        *,
        after_cursor: Optional[str] = None,
        limit: int = 50,
        artifact_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        return self._search(
            table="team_artifact_fts",
            workspace_id=workspace_id,
            query=query,
            after_cursor=after_cursor,
            limit=limit,
            fields=("artifact_id", "filename", "media_type", "metadata"),
            id_field="artifact_id",
            allowed_ids=artifact_ids,
        )

    def _search(
        self,
        *,
        table: str,
        workspace_id: str,
        query: str,
        after_cursor: Optional[str],
        limit: int,
        fields: tuple[str, ...],
        id_field: Optional[str] = None,
        allowed_ids: Optional[Sequence[str]] = None,
    ) -> dict[str, Any]:
        _require_text(workspace_id, "workspace id")
        terms = _fts_terms(query)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ContractError("limit must be an integer between 1 and 200")
        after_rowid = _cursor_rowid(after_cursor)
        if not terms:
            return {"results": [], "next_cursor": str(after_rowid), "has_more": False}
        columns = ", ".join(fields)
        allowed = None if allowed_ids is None else tuple(
            _required_id(value, "search scope id") for value in allowed_ids
        )
        if allowed is not None and not allowed:
            return {"results": [], "next_cursor": str(after_rowid), "has_more": False}
        scope_clause = ""
        scope_params: list[Any] = []
        if id_field is not None and allowed is not None:
            scope_clause = " AND " + id_field + " IN (" + ",".join("?" for _ in allowed) + ")"
            scope_params.extend(allowed)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"""
                SELECT rowid, {columns} FROM {table}
                WHERE {table} MATCH ? AND workspace_id = ? AND rowid > ?{scope_clause}
                ORDER BY rowid LIMIT ?
                """,
                (terms, workspace_id, after_rowid, *scope_params, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        results = [
            {"cursor": str(row["rowid"]), **{field: row[field] for field in fields}}
            for row in rows[:limit]
        ]
        return {
            "results": results,
            "next_cursor": results[-1]["cursor"] if results else str(after_rowid),
            "has_more": has_more,
        }


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} is required")
    return value.strip()


def _exact_external_ref(value: Any, label: str) -> str:
    """Validate a provider identifier without pretending it is an Astra ID.

    Provider identifiers may contain slashes, colons, and other namespace
    punctuation, so the normal Astra ``_required_id`` helper is intentionally
    not used here.  Wildcards and control characters are never valid scopes.
    """

    text = _require_text(value, label)
    if text == "*" or any(ord(char) < 32 for char in text) or len(text) > 512:
        raise ContractError(f"{label} must be one exact provider reference")
    return text


def _exact_external_refs(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ContractError(f"{label} list must be a sequence")
    normalized = tuple(_exact_external_ref(value, label) for value in values)
    if not normalized:
        raise ContractError(f"{label} list cannot be empty")
    if len(normalized) != len(set(normalized)):
        raise ContractError(f"{label} values must be unique")
    return normalized


def _exact_connector_actions(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ContractError("connector actions must be a sequence")
    normalized = tuple(sorted({_required_kind(value, "connector action") for value in values}))
    if not normalized or any(value not in ConnectorRepository._ACTIONS for value in normalized):
        raise ContractError("connector actions are unsupported")
    return normalized


def _connector_identity_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_load(result.pop("metadata_json"), {})
    return result


def _connector_grant_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["actions"] = tuple(_json_load(result.pop("actions_json"), []))
    result["resource_ids"] = tuple(_json_load(result.pop("resource_ids_json"), []))
    return result


def _connector_binding_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    if "actions_json" in result:
        result["actions"] = tuple(_json_load(result.pop("actions_json"), []))
    if "resource_ids_json" in result:
        result["resource_ids"] = tuple(_json_load(result.pop("resource_ids_json"), []))
    return result


def _connector_event_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = _json_load(result.pop("payload_json"), {})
    return result


def _connector_outbox_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = _json_load(result.pop("payload_json"), {})
    return result


def _fts_terms(query: Any) -> str:
    if not isinstance(query, str):
        raise ContractError("search query is required")
    tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    return " ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:32])


def _cursor_rowid(cursor: Optional[str]) -> int:
    if cursor is None or cursor == "":
        return 0
    if not isinstance(cursor, str) or not cursor.isdigit():
        raise ContractError("search cursor must be an opaque numeric cursor")
    value = int(cursor)
    if value < 0:
        raise ContractError("search cursor must be non-negative")
    return value


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "sequence": int(row["sequence"]),
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "workspace_id": row["workspace_id"],
        "actor_id": row["actor_id"],
        "aggregate_type": row["aggregate_type"],
        "aggregate_id": row["aggregate_id"],
        "correlation_id": row["correlation_id"],
        "causation_id": row["causation_id"],
        "schema_version": int(row["schema_version"]),
        "payload": _json_load(row["payload_json"], {}),
        "occurred_at": row["occurred_at"],
    }


def _event_matches(persisted: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    keys = (
        "event_id",
        "event_type",
        "workspace_id",
        "actor_id",
        "aggregate_type",
        "aggregate_id",
        "correlation_id",
        "causation_id",
        "schema_version",
        "payload",
        "occurred_at",
    )
    return all(persisted.get(key) == values.get(key) for key in keys)


def _handoff_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["artifact_ids"] = _json_load(result.pop("artifact_ids_json"), [])
    result["budget"] = _json_load(result.pop("budget_json"), {})
    result["agent_path"] = _json_load(result.pop("agent_path_json"), [])
    return result


def _handoff_matches(persisted: Mapping[str, Any], handoff: HandoffContract) -> bool:
    values = handoff.to_dict()
    values["requester_actor_id"] = handoff.requester.id
    return all(
        persisted.get(key) == values.get(key)
        for key in (
            "handoff_id",
            "workspace_id",
            "task_id",
            "sender_agent_id",
            "recipient_agent_id",
            "purpose",
            "expected_output",
            "requester_actor_id",
            "artifact_ids",
            "budget",
            "depth",
            "agent_path",
            "idempotency_key",
            "correlation_id",
        )
    )


@dataclass(frozen=True)
class ParityReport:
    """Read-only cutover evidence; counts are observations, never migration writes."""

    legacy_counts: Mapping[str, int]
    canonical_counts: Mapping[str, int]
    comparisons: Mapping[str, int]
    hash_chain_status: Mapping[str, str]
    foreign_key_errors: tuple[tuple[Any, ...], ...]
    corrupt_sources: tuple[str, ...]

    @property
    def is_usable(self) -> bool:
        return not self.corrupt_sources and not self.foreign_key_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy_counts": dict(self.legacy_counts),
            "canonical_counts": dict(self.canonical_counts),
            "comparisons": dict(self.comparisons),
            "hash_chain_status": dict(self.hash_chain_status),
            "foreign_key_errors": [list(error) for error in self.foreign_key_errors],
            "corrupt_sources": list(self.corrupt_sources),
            "is_usable": self.is_usable,
        }


def build_parity_report(database: TeamDatabase, legacy_base: str | Path) -> ParityReport:
    """Inspect legacy files without constructing legacy stores or changing them."""

    base = Path(legacy_base).expanduser()
    legacy_counts: dict[str, int] = {
        "sessions": 0,
        "conversation_jsonl_messages": 0,
        "team_events": 0,
        "team_items": 0,
        "chat_groups": 0,
        "chat_messages": 0,
    }
    corrupt: list[str] = []
    coworker_db = base / "coworker.db"
    teams_db = base / "teams.db"
    chat_db = base / "chat.db"
    legacy_counts["sessions"], error = _legacy_count(coworker_db, "sessions")
    if error:
        corrupt.append(f"{coworker_db}: {error}")
    for name, path, table in (
        ("team_events", teams_db, "team_events"),
        ("team_items", teams_db, "team_items"),
        ("chat_groups", chat_db, "chat_groups"),
        ("chat_messages", chat_db, "chat_messages"),
    ):
        legacy_counts[name], error = _legacy_count(path, table)
        if error:
            corrupt.append(f"{path}: {error}")

    conversations_dir = base / "conversations"
    if conversations_dir.exists():
        for path in sorted(conversations_dir.glob("*.jsonl")):
            valid, invalid = _jsonl_counts(path)
            legacy_counts["conversation_jsonl_messages"] += valid
            if invalid:
                corrupt.append(f"{path}: {invalid} malformed JSONL line(s)")

    canonical = database.table_counts()
    comparisons = {
        "team_events_to_domain_events": legacy_counts["team_events"] - canonical["domain_events"],
        "chat_messages_to_messages": legacy_counts["chat_messages"] - canonical["messages"],
        "session_rows_to_workspaces": legacy_counts["sessions"] - canonical["workspaces"],
    }
    hash_status = _legacy_team_hash_status(teams_db, corrupt)
    integrity = database.verify_integrity()
    return ParityReport(
        legacy_counts=legacy_counts,
        canonical_counts=canonical,
        comparisons=comparisons,
        hash_chain_status=hash_status,
        foreign_key_errors=tuple(tuple(error) for error in integrity["foreign_key_errors"]),
        corrupt_sources=tuple(corrupt),
    )


def _legacy_count(path: Path, table: str) -> tuple[int, Optional[str]]:
    if not path.exists():
        return 0, None
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if not row or not row[0]:
            return 0, None
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]), None
    except sqlite3.DatabaseError as error:
        return 0, str(error)
    finally:
        if connection is not None:
            connection.close()


def _jsonl_counts(path: Path) -> tuple[int, int]:
    valid = 0
    invalid = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
            else:
                valid += 1
    except (OSError, UnicodeError):
        return 0, 1
    return valid, invalid


def _legacy_team_hash_status(path: Path, corrupt: list[str]) -> dict[str, str]:
    if not path.exists():
        return {}
    connection: Optional[sqlite3.Connection] = None
    statuses: dict[str, str] = {}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'team_events'"
        ).fetchone()
        if exists is None:
            return {}
        spaces = connection.execute("SELECT DISTINCT space FROM team_events ORDER BY space").fetchall()
        for space_row in spaces:
            space = space_row[0]
            rows = connection.execute(
                "SELECT * FROM team_events WHERE space = ? ORDER BY seq", (space,)
            ).fetchall()
            previous = "genesis"
            status = "verified"
            for row in rows:
                record = {
                    key: row[key]
                    for key in (
                        "ts", "space", "kind", "actor", "actor_role", "item_id", "case_id",
                        "recipient", "payload", "taint", "prev_hash",
                    )
                }
                expected = hashlib.sha256(
                    json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
                ).hexdigest()
                if row["prev_hash"] != previous or row["hash"] != expected:
                    status = "corrupt"
                    break
                previous = row["hash"]
            if status == "verified" and rows:
                head = connection.execute(
                    "SELECT head_hash FROM team_meta WHERE space = ?", (space,)
                ).fetchone()
                if head is None or head[0] != previous:
                    status = "corrupt"
            statuses[str(space)] = status
            if status == "corrupt":
                corrupt.append(f"{path}: team event hash chain for {space!r} is corrupt")
    except sqlite3.DatabaseError as error:
        corrupt.append(f"{path}: {error}")
    finally:
        if connection is not None:
            connection.close()
    return statuses


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _json_object(value: Optional[Mapping[str, Any]], label: str) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{label} must be an object")
    return _json(dict(value))


def _require_handle(value: Any, label: str) -> str:
    value = _require_text(value, label).lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", value):
        raise ContractError(f"{label} must be a safe lowercase handle")
    return value


def _require_kind_or_text(value: Any, label: str) -> str:
    return _required_kind(value, label)


def _require_positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ContractError(f"{label} must be a positive integer")
    return value


def _require_workspace_actor(
    connection: sqlite3.Connection,
    workspace_id: str,
    actor_id: str,
    *,
    allow_system: bool = False,
) -> sqlite3.Row:
    actor = connection.execute(
        "SELECT actor_id, actor_kind FROM actors WHERE actor_id = ?", (actor_id,)
    ).fetchone()
    if actor is None:
        raise ContractError(f"actor not found: {actor_id}")
    if allow_system and actor["actor_kind"] == ActorKind.SYSTEM.value:
        return actor
    membership = connection.execute(
        """
        SELECT status FROM workspace_memberships
        WHERE workspace_id = ? AND actor_id = ?
        """,
        (workspace_id, actor_id),
    ).fetchone()
    if membership is None or membership["status"] != "active":
        raise ContractError(f"actor is not an active member of workspace: {actor_id}")
    return actor


def _require_conversation_member(
    connection: sqlite3.Connection,
    conversation_id: str,
    actor_id: str,
    *,
    active_only: bool = True,
) -> sqlite3.Row:
    query = """
        SELECT m.*, c.workspace_id, c.created_by_actor_id, c.status AS conversation_status
        FROM conversation_members AS m
        JOIN conversations AS c ON c.conversation_id = m.conversation_id
        WHERE m.conversation_id = ? AND m.actor_id = ?
    """
    params: tuple[Any, ...] = (conversation_id, actor_id)
    if active_only:
        query += " AND m.left_at IS NULL"
    row = connection.execute(query, params).fetchone()
    if row is None:
        raise ContractError("actor is not an active member of conversation")
    return row


def _is_conversation_admin(
    member: sqlite3.Row, conversation: sqlite3.Row, actor_id: str
) -> bool:
    return (
        member["member_role"] in {"owner", "admin"}
        or conversation["created_by_actor_id"] == actor_id
    )


def _require_agent_workspace(
    connection: sqlite3.Connection, workspace_id: str, agent_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM agent_installations WHERE workspace_id = ? AND agent_id = ?",
        (workspace_id, agent_id),
    ).fetchone()
    if row is None:
        raise ContractError(f"agent is missing or belongs to another workspace: {agent_id}")
    return row


def _require_page_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
        raise ContractError("limit must be an integer between 1 and 200")
    return value


def _require_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ContractError(f"{label} must include a timezone")
    return value


def _shift_timestamp(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value)
    from datetime import timedelta

    return (parsed + timedelta(seconds=seconds)).isoformat()


def _timestamp_expired(value: str, now: str) -> bool:
    try:
        return datetime.fromisoformat(value) <= datetime.fromisoformat(now)
    except ValueError as error:
        raise ContractError("stored lease timestamp is invalid") from error


def _unique_id_list(values: Sequence[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ContractError(f"{label} list must be a sequence")
    result = tuple(_required_id(value, label) for value in (values or ()))
    if len(result) != len(set(result)):
        raise ContractError(f"{label} values must be unique")
    return result


def _cursor_encode(kind: str, values: Sequence[str]) -> str:
    payload = _json({"kind": kind, "values": list(values)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _cursor_decode(cursor: Optional[str], kind: str, count: int) -> Optional[tuple[str, ...]]:
    if cursor is None or cursor == "":
        return None
    if not isinstance(cursor, str) or len(cursor) > 2048:
        raise ContractError("cursor must be an opaque value")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as error:
        raise ContractError("cursor is malformed") from error
    if not isinstance(raw, dict) or raw.get("kind") != kind or not isinstance(raw.get("values"), list):
        raise ContractError("cursor is malformed")
    values = tuple(raw["values"])
    if len(values) != count or any(not isinstance(value, str) or not value for value in values):
        raise ContractError("cursor is malformed")
    return values


def _message_cursor(cursor: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    values = _cursor_decode(cursor, "messages", 2)
    return (values[0], values[1]) if values else (None, None)


def _message_cursor_encode(created_at: str, message_id: str) -> str:
    return _cursor_encode("messages", (created_at, message_id))


def _task_cursor(cursor: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    values = _cursor_decode(cursor, "tasks", 2)
    return (values[0], values[1]) if values else (None, None)


def _task_cursor_encode(updated_at: str, task_id: str) -> str:
    return _cursor_encode("tasks", (updated_at, task_id))


def _notification_cursor(cursor: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    values = _cursor_decode(cursor, "notifications", 2)
    return (values[0], values[1]) if values else (None, None)


def _notification_cursor_encode(created_at: str, notification_id: str) -> str:
    return _cursor_encode("notifications", (created_at, notification_id))


def _request_cursor(cursor: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    values = _cursor_decode(cursor, "requests", 2)
    return (values[0], values[1]) if values else (None, None)


def _request_cursor_encode(created_at: str, request_id: str) -> str:
    return _cursor_encode("requests", (created_at, request_id))


def _conversation_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["allow_at_all"] = bool(result["allow_at_all"])
    if "pinned" in result:
        result["pinned"] = bool(result["pinned"])
    return result


def _attach_conversation_agent_projection(
    conversation: dict[str, Any], agent_members: Sequence[Mapping[str, Any]]
) -> None:
    """Expose the agent identities needed to render conversation-level UI."""

    conversation["agent_member_ids"] = [member["agent_id"] for member in agent_members]
    conversation["agent_actor_ids"] = [member["actor_id"] for member in agent_members]
    conversation["agent_statuses"] = {
        member["agent_id"]: member["status"] for member in agent_members
    }
    # Paused and archived installations stay visible in conversation history,
    # but cannot receive newly routed work. Offline/error/provisioning agents
    # remain eligible for durable queueing so recovery does not lose messages.
    dispatchable_members = [
        member
        for member in agent_members
        if member["status"] not in {AgentStatus.PAUSED.value, AgentStatus.ARCHIVED.value}
    ]
    conversation["dispatchable_agent_ids"] = [
        member["agent_id"] for member in dispatchable_members
    ]
    conversation["dispatchable_agent_actor_ids"] = [
        member["actor_id"] for member in dispatchable_members
    ]


def _message_row(row: sqlite3.Row, *, connection: sqlite3.Connection) -> dict[str, Any]:
    result = dict(row)
    revision = connection.execute(
        "SELECT COALESCE(MAX(revision_number), 1) AS revision_number FROM message_revisions WHERE message_id = ?",
        (row["message_id"],),
    ).fetchone()
    result["revision_number"] = int(revision["revision_number"] if revision is not None else 1)
    author = connection.execute(
        "SELECT display_name, handle, archived_at FROM actors WHERE actor_id = ?",
        (row["author_actor_id"],),
    ).fetchone()
    result["author_display_name"] = author["display_name"] if author is not None else ""
    result["author_handle"] = author["handle"] if author is not None else None
    result["author_archived"] = bool(author is not None and author["archived_at"] is not None)
    result["mentions"] = [
        item["actor_id"]
        for item in connection.execute(
            "SELECT actor_id FROM message_mentions WHERE message_id = ? ORDER BY actor_id",
            (row["message_id"],),
        ).fetchall()
    ]
    result["reactions"] = [
        dict(item)
        for item in connection.execute(
            "SELECT actor_id, emoji, created_at FROM message_reactions WHERE message_id = ? ORDER BY emoji, actor_id",
            (row["message_id"],),
        ).fetchall()
    ]
    result["attachments"] = [
        dict(item)
        for item in connection.execute(
            "SELECT * FROM message_attachments WHERE message_id = ? ORDER BY created_at, attachment_id",
            (row["message_id"],),
        ).fetchall()
    ] if _table_exists(connection, "message_attachments") else []
    result["translations"] = [
        dict(item)
        for item in connection.execute(
            "SELECT language, body, model, created_at FROM message_translations WHERE message_id = ? ORDER BY language",
            (row["message_id"],),
        ).fetchall()
    ] if _table_exists(connection, "message_translations") else []
    result["reasoning"] = row["reasoning"] if _table_has_column(connection, "messages", "reasoning") and "reasoning" in row.keys() and row["reasoning"] is not None else None
    result["metadata"] = _json_load(row["metadata_json"], {}) if _table_has_column(connection, "messages", "metadata_json") and "metadata_json" in row.keys() and row["metadata_json"] else {}
    return result


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return any(col["name"] == column for col in info)
    except Exception:
        return False


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _message_matches(
    row: sqlite3.Row,
    *,
    message_id: str,
    author: ActorRef,
    body: str,
    thread_id: Optional[str],
    parent_message_id: Optional[str],
    quote_message_id: Optional[str],
    correlation_id: str,
    causation_id: Optional[str],
    mentions: Sequence[str],
    connection: sqlite3.Connection,
) -> bool:
    stored_mentions = tuple(
        item["actor_id"]
        for item in connection.execute(
            "SELECT actor_id FROM message_mentions WHERE message_id = ? ORDER BY actor_id",
            (row["message_id"],),
        ).fetchall()
    )
    return (
        row["message_id"] == message_id
        and row["author_actor_id"] == author.id
        and row["author_kind"] == author.kind.value
        and row["body"] == body
        and row["thread_id"] == thread_id
        and row["parent_message_id"] == parent_message_id
        and row["quote_message_id"] == quote_message_id
        and row["correlation_id"] == correlation_id
        and row["causation_id"] == causation_id
        and stored_mentions == tuple(mentions)
    )


def _work_request_row(row: sqlite3.Row, connection: sqlite3.Connection) -> dict[str, Any]:
    result = dict(row)
    result["source_ref"] = {"kind": row["source_ref_kind"], "id": row["source_ref_id"]}
    actor = connection.execute(
        "SELECT actor_id, actor_kind, display_name FROM actors WHERE actor_id = ?",
        (row["requester_actor_id"],),
    ).fetchone()
    result["requester"] = (
        {
            "id": actor["actor_id"],
            "kind": actor["actor_kind"],
            "display_name": actor["display_name"],
        }
        if actor is not None
        else {"id": row["requester_actor_id"], "kind": "system", "display_name": ""}
    )
    result["target_agent_ids"] = [
        item["agent_id"]
        for item in connection.execute(
            "SELECT agent_id FROM work_request_targets WHERE request_id = ? ORDER BY position",
            (row["request_id"],),
        ).fetchall()
    ]
    result["context_refs"] = [
        {"kind": item["resource_kind"], "id": item["resource_id"]}
        for item in connection.execute(
            "SELECT resource_kind, resource_id FROM work_request_context_refs WHERE request_id = ? ORDER BY position",
            (row["request_id"],),
        ).fetchall()
    ]
    result["input_artifact_ids"] = [
        item["artifact_id"]
        for item in connection.execute(
            "SELECT artifact_id FROM work_request_input_artifacts WHERE request_id = ? ORDER BY position",
            (row["request_id"],),
        ).fetchall()
    ]
    result["permission_scope"] = _json_load(row["permission_scope_json"], {})
    result["budget"] = _json_load(row["budget_json"], {})
    return result


def _request_matches(
    persisted: Mapping[str, Any],
    request: WorkRequest,
    *,
    crew_run_id: Optional[str] = None,
) -> bool:
    values = request.to_dict()
    return (
        persisted.get("request_id") == request.request_id
        and persisted.get("workspace_id") == request.workspace_id
        and persisted.get("source_kind") == request.source.value
        and persisted.get("source_ref") == values["source_ref"]
        and persisted.get("requester", {}).get("id") == request.requester.id
        and persisted.get("target_agent_ids") == list(request.target_agent_ids)
        and persisted.get("instruction") == request.instruction
        and persisted.get("expected_output") == request.expected_output
        and persisted.get("conversation_id") == request.conversation_id
        and persisted.get("thread_id") == request.thread_id
        and persisted.get("task_id") == request.task_id
        and persisted.get("context_refs") == [ref.to_dict() for ref in request.context_refs]
        and persisted.get("input_artifact_ids") == list(request.input_artifact_ids)
        # Dispatch-time snapshots are derived from installation state. A retry
        # after the Bot is edited must return the original request rather than
        # become an idempotency conflict.
        and _request_scope_matches(
            persisted.get("permission_scope"), values["permission_scope"]
        )
        and persisted.get("budget") == values["budget"]
        and persisted.get("priority") == request.priority
        and persisted.get("correlation_id") == request.correlation_id
        and persisted.get("causation_id") == request.causation_id
        and persisted.get("crew_run_id") == crew_run_id
    )


_DERIVED_REQUEST_SCOPE_KEYS = frozenset(
    {
        "snapshot_version",
        "captured_at",
        "agent_revision_ids",
        "agent_revision_snapshots",
        "sandbox_ids",
        "sandbox_snapshots",
        "connector_grant_snapshots",
        "context_snapshot",
        "skill_snapshots",
        "computer_ids",
        "computer_snapshots",
    }
)


def _request_scope_matches(left: Any, right: Any) -> bool:
    """Compare caller-owned scope while ignoring dispatch-time snapshots."""

    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return left == right
    return (
        {key: value for key, value in left.items() if key not in _DERIVED_REQUEST_SCOPE_KEYS}
        == {key: value for key, value in right.items() if key not in _DERIVED_REQUEST_SCOPE_KEYS}
    )


def _delivery_row(row: sqlite3.Row, *, connection: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    result = dict(row)
    if connection is not None:
        result["lease"] = next(
            (dict(lease) for lease in connection.execute(
                "SELECT * FROM delivery_leases WHERE delivery_id = ?", (row["delivery_id"],)
            ).fetchall()),
            None,
        )
        result["attempts"] = [
            dict(attempt)
            for attempt in connection.execute(
                "SELECT * FROM delivery_attempts WHERE delivery_id = ? ORDER BY attempt_number",
                (row["delivery_id"],),
            ).fetchall()
        ]
    return result


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["budget"] = _json_load(result.pop("budget_json", None), {})
    return result


def _revision_number(connection: sqlite3.Connection, artifact_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(revision_number), 0) + 1 AS next_revision FROM artifact_revisions WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    return int(row["next_revision"])


def _artifact_revision_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_load(result.pop("metadata_json", None), {})
    return result


def _artifact_row_for_revision(connection: sqlite3.Connection, revision: sqlite3.Row) -> dict[str, Any]:
    artifact = connection.execute(
        "SELECT * FROM artifacts WHERE artifact_id = ?", (revision["artifact_id"],)
    ).fetchone()
    assert artifact is not None
    return {
        **dict(artifact),
        "revision": _artifact_revision_row(revision),
    }


def _artifact_revision_matches(
    row: sqlite3.Row, publication: ArtifactPublication, storage_ref: str
) -> bool:
    return (
        row["artifact_revision_id"] == publication.revision_id
        and row["source_run_id"] == publication.source_run_id
        and row["publisher_actor_id"] == publication.publisher.id
        and row["requester_actor_id"] == publication.requester_id
        and row["filename"] == publication.filename
        and row["media_type"] == publication.media_type
        and row["size_bytes"] == publication.size_bytes
        and row["sha256"] == publication.sha256
        and row["storage_ref"] == storage_ref
        and _json_load(row["metadata_json"], {}) == publication.to_dict()["metadata"]
    )


def _context_share_matches(row: sqlite3.Row, share: ContextShare) -> bool:
    return (
        row["context_share_id"] == share.share_id
        and row["resource_kind"] == share.resource.kind
        and row["resource_id"] == share.resource.id
        and row["source_scope_kind"] == share.source.scope.value
        and row["source_scope_id"] == share.source.scope_id
        and row["target_scope_kind"] == share.target.scope.value
        and row["target_scope_id"] == share.target.scope_id
        and row["shared_by_actor_id"] == share.shared_by.id
        and row["correlation_id"] == share.correlation_id
        and row["reason"] == share.reason
    )
