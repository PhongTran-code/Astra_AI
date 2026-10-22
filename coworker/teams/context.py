"""Bounded, requester-aware context assembly for Team work.

The persistence layer stores memories, pins, and shares independently.  This
module is the read boundary that turns those records into one agent input
package.  Nothing is included merely because it exists in the workspace: the
principal must be a member of the relevant scope, or an explicit share must
cross into a scope the principal can already read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .contracts import ContractError, ResourceRef
from .persistence import (
    AgentRepository,
    ArtifactRepository,
    ContextRepository,
    ConversationRepository,
    IdentityRepository,
    TaskRepository,
)
from .sharing import ContextScope


class ContextBuildError(ContractError):
    """A context package could not be built without crossing an ACL boundary."""


class ContextPackageBuilder:
    """Assemble a deterministic and size-bounded context package."""

    def __init__(
        self,
        identities: IdentityRepository,
        conversations: ConversationRepository,
        context: ContextRepository,
        tasks: TaskRepository,
        artifacts: ArtifactRepository,
        *,
        agents: Optional[AgentRepository] = None,
    ) -> None:
        self.identities = identities
        self.conversations = conversations
        self.context = context
        self.tasks = tasks
        self.artifacts = artifacts
        self.agents = agents

    def build(
        self,
        conversation_id: str,
        requester_actor_id: str,
        *,
        principal_actor_id: Optional[str] = None,
        private_agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        max_messages: int = 50,
        max_chars: int = 32_000,
    ) -> dict[str, Any]:
        self._validate_limits(max_messages, max_chars)
        principal_actor_id = principal_actor_id or requester_actor_id
        conversation = self.conversations.get_conversation(conversation_id)
        if conversation is None:
            raise ContextBuildError("conversation not found")
        self._require_member(conversation, requester_actor_id)
        self._require_workspace_actor(conversation["workspace_id"], principal_actor_id)
        if principal_actor_id != requester_actor_id:
            self._require_member(conversation, principal_actor_id)
        if private_agent_id is not None:
            if self.agents is None:
                raise ContextBuildError("agent identity repository is unavailable")
            installation = self.agents.get_installation(private_agent_id)
            if (
                installation is None
                or installation["workspace_id"] != conversation["workspace_id"]
                or installation["actor_id"] != principal_actor_id
            ):
                raise ContextBuildError("private agent context does not belong to the principal")

        conversation_scope = (
            ContextScope.DIRECT_MESSAGE
            if conversation["conversation_kind"] == "direct"
            else ContextScope.GROUP
        )
        visible_scopes: list[tuple[ContextScope, str]] = [
            (conversation_scope, conversation_id),
            (ContextScope.WORKSPACE, conversation["workspace_id"]),
        ]
        if task_id is not None:
            task = self.tasks.get_task(task_id)
            if task is None or task["workspace_id"] != conversation["workspace_id"]:
                raise ContextBuildError("context task is missing or belongs to another workspace")
            visible_scopes.append((ContextScope.TASK, task_id))
        else:
            task = None
        if private_agent_id is not None:
            visible_scopes.append((ContextScope.AGENT_PRIVATE, private_agent_id))

        messages = self.conversations.list_messages(
            conversation_id,
            limit=min(max_messages, 200),
            requester_actor_id=principal_actor_id,
            include_tombstones=False,
        )["messages"]
        memories: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        shares: list[dict[str, Any]] = []
        seen_share_ids: set[str] = set()
        for scope, scope_id in visible_scopes:
            memories.extend(self.context.list_memories(conversation["workspace_id"], scope, scope_id))
            pins.extend(self.context.list_pins(conversation["workspace_id"], scope, scope_id))
            for share in self.context.list_scope_shares(
                conversation["workspace_id"], scope, scope_id
            ):
                if share["context_share_id"] not in seen_share_ids:
                    seen_share_ids.add(share["context_share_id"])
                    shares.append(share)

        direct_resources: list[dict[str, Any]] = []
        for pin in pins:
            resource = self._resolve_resource(
                conversation["workspace_id"], principal_actor_id,
                ResourceRef(pin["resource_kind"], pin["resource_id"]),
                visible_scopes,
            )
            if resource is not None:
                direct_resources.append({"resource": resource, "pin": pin})

        shared_resources: list[dict[str, Any]] = []
        for share in shares:
            resource_ref = ResourceRef(share["resource_kind"], share["resource_id"])
            resource = self._resolve_resource(
                conversation["workspace_id"], principal_actor_id, resource_ref, visible_scopes,
                source_scope=(share["source_scope_kind"], share["source_scope_id"]),
                allow_shared=True,
            )
            if resource is not None:
                shared_resources.append({"resource": resource, "share": share})

        package: dict[str, Any] = {
            "version": 1,
            "workspace_id": conversation["workspace_id"],
            "conversation": {
                "conversation_id": conversation["conversation_id"],
                "kind": conversation["conversation_kind"],
                "title": conversation["title"],
            },
            "principal_actor_id": principal_actor_id,
            "messages": messages,
            "memories": memories,
            "pinned_resources": direct_resources,
            "shared_resources": shared_resources,
            "task": task,
            "truncated": False,
        }
        return self._bound(package, max_chars)

    def _resolve_resource(
        self,
        workspace_id: str,
        actor_id: str,
        resource: ResourceRef,
        visible_scopes: list[tuple[ContextScope, str]],
        *,
        source_scope: Optional[tuple[str, str]] = None,
        allow_shared: bool = False,
    ) -> Optional[dict[str, Any]]:
        actual_scope, value = self._resource_scope(workspace_id, resource)
        if actual_scope is None or (source_scope is not None and (actual_scope, value) != source_scope):
            return None
        is_visible = (actual_scope, value) in {
            (scope.value, scope_id) for scope, scope_id in visible_scopes
        }
        if allow_shared:
            # A share is valid only when its target is one of the package's
            # already-readable scopes; the source itself may then be private.
            if source_scope is None or not visible_scopes:
                return None
        if not is_visible and not allow_shared:
            return None
        if resource.kind == "message":
            row = self.conversations.get_message(
                resource.id,
                requester_actor_id=None if allow_shared else actor_id,
            )
            if row is None or row["conversation_id"] != value:
                return None
            return {"kind": resource.kind, "id": resource.id, "message": row}
        if resource.kind == "artifact":
            row = self.artifacts.get_accessible_artifact(resource.id, workspace_id, actor_id)
            if row is None and not allow_shared:
                return None
            if row is None:
                row = self.artifacts.get_artifact(resource.id)
            if row is None:
                return None
            return {"kind": resource.kind, "id": resource.id, "artifact": self._safe_artifact(row)}
        if resource.kind == "memory":
            row = self.context.get_memory(resource.id)
            if row is None or row["deleted_at"] is not None:
                return None
            if (
                row["scope_kind"] == ContextScope.AGENT_PRIVATE.value
                and not allow_shared
                and not self._agent_owns_private_scope(
                    workspace_id, actor_id, row["scope_id"]
                )
            ):
                return None
            if not allow_shared and not self._not_expired(row.get("expires_at")):
                return None
            return {"kind": resource.kind, "id": resource.id, "memory": row}
        if resource.kind == "task":
            row = self.tasks.get_task(resource.id)
            if row is None or row["workspace_id"] != workspace_id:
                return None
            return {"kind": resource.kind, "id": resource.id, "task": row}
        return {"kind": resource.kind, "id": resource.id, "reference_only": True}

    def _resource_scope(self, workspace_id: str, resource: ResourceRef) -> tuple[Optional[str], str]:
        if resource.kind == "message":
            row = self.conversations.get_message(resource.id)
            if row is None:
                return None, ""
            conversation = self.conversations.get_conversation(row["conversation_id"])
            if conversation is None or conversation["workspace_id"] != workspace_id:
                return None, ""
            scope = "direct_message" if conversation["conversation_kind"] == "direct" else "group"
            return scope, row["conversation_id"]
        if resource.kind == "artifact":
            row = self.artifacts.get_artifact(resource.id)
            if row is None or row["workspace_id"] != workspace_id:
                return None, ""
            return row["visibility"], row["scope_id"]
        if resource.kind == "memory":
            row = self.context.get_memory(resource.id)
            if row is None or row["workspace_id"] != workspace_id:
                return None, ""
            return row["scope_kind"], row["scope_id"]
        if resource.kind == "task":
            row = self.tasks.get_task(resource.id)
            if row is None or row["workspace_id"] != workspace_id:
                return None, ""
            return "task", row["task_id"]
        return None, ""

    def _require_member(self, conversation: dict[str, Any], actor_id: str) -> None:
        if not any(member["actor_id"] == actor_id and member["left_at"] is None for member in conversation["members"]):
            raise ContextBuildError("actor is not an active conversation member")

    def _require_workspace_actor(self, workspace_id: str, actor_id: str) -> None:
        with self.identities.database._lock:
            row = self.identities.database._conn.execute(
                """
                SELECT 1 FROM workspace_memberships
                WHERE workspace_id = ? AND actor_id = ? AND status = 'active'
                """,
                (workspace_id, actor_id),
            ).fetchone()
        if row is None:
            raise ContextBuildError("actor is not an active workspace member")

    def _agent_owns_private_scope(
        self, workspace_id: str, actor_id: str, agent_id: str
    ) -> bool:
        if self.agents is None:
            return False
        installation = self.agents.get_installation(agent_id)
        return bool(
            installation is not None
            and installation["workspace_id"] == workspace_id
            and installation["actor_id"] == actor_id
        )

    @staticmethod
    def _validate_limits(max_messages: int, max_chars: int) -> None:
        if isinstance(max_messages, bool) or not isinstance(max_messages, int) or not 1 <= max_messages <= 200:
            raise ContextBuildError("max_messages must be an integer between 1 and 200")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= 200_000:
            raise ContextBuildError("max_chars must be an integer between 1 and 200000")

    @staticmethod
    def _not_expired(expires_at: Optional[str]) -> bool:
        if not expires_at:
            return True
        try:
            return datetime.fromisoformat(expires_at) > datetime.now(timezone.utc)
        except ValueError:
            return False

    @staticmethod
    def _safe_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in artifact.items() if key != "revision"}
        revisions = []
        for revision in artifact.get("revisions", ()):
            revisions.append({key: value for key, value in revision.items() if key != "storage_ref"})
        if "revision" in artifact:
            result["revision"] = {
                key: value for key, value in artifact["revision"].items() if key != "storage_ref"
            }
        result["revisions"] = revisions
        return result

    @staticmethod
    def _bound(package: dict[str, Any], max_chars: int) -> dict[str, Any]:
        # Messages are the only unbounded user-authored field in the standard
        # package.  Keep the other records intact, then trim messages from the
        # oldest side until the serialized body fits the hard cap.
        def size(value: Any) -> int:
            return len(str(value))

        while size(package) > max_chars and package["messages"]:
            package["messages"].pop(0)
            package["truncated"] = True
        if size(package) > max_chars:
            package["memories"] = package["memories"][: max(1, len(package["memories"]) // 2)]
            package["truncated"] = True
        if size(package) > max_chars:
            package["shared_resources"] = []
            package["pinned_resources"] = []
            package["truncated"] = True
        if size(package) > max_chars:
            raise ContextBuildError("context package exceeds the configured size cap")
        return package
