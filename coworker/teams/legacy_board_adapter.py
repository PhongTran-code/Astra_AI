"""Compatibility projection for the legacy ``/v1/board`` protocol.

Once the operator activates a cutover, board requests must resolve through the
canonical Team database.  This adapter keeps the old wire shapes and numeric
``(space, id)`` references, while refusing unmapped or cross-workspace records.
It never opens or writes ``teams.db``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .contracts import ActorKind, ActorRef, ContractError
from .lifecycle import TaskStatus, TransitionAuthority
from .model import Actor, AuthorityError, BoardError, BoardNotFoundError, Role
from .persistence import TaskRepository, TeamDatabase


class LegacyBoardAdapterError(BoardError):
    """A legacy board operation cannot be represented safely in canonical Team."""


class LegacyBoardAdapter:
    """Map legacy board ids to canonical tasks without a second source of truth."""

    def __init__(self, database: TeamDatabase, workspace_id: str, requester_actor_id: str) -> None:
        self.database = database
        self.workspace_id = str(workspace_id).strip()
        self.requester_actor_id = str(requester_actor_id).strip()
        if not self.workspace_id or not self.requester_actor_id:
            raise ContractError("workspace_id and requester_actor_id are required")
        self.tasks = TaskRepository(database)

    # ------------------------------------------------------------- public board API

    def spaces(self) -> list[str]:
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT legacy_id FROM legacy_id_mappings
                WHERE source_system = 'legacy_board' AND legacy_kind = 'item'
                ORDER BY legacy_id
                """
            ).fetchall()
        spaces = {str(row["legacy_id"]).rsplit(":", 1)[0] for row in rows if ":" in str(row["legacy_id"])}
        return sorted(spaces)

    def list_items(
        self,
        space: str,
        actor: Actor,
        *,
        state: Optional[str] = None,
        assignee: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        requested_state = str(state or "").strip() or None
        if requested_state is not None:
            try:
                requested_state = TaskStatus(requested_state).value
            except ValueError as error:
                raise BoardError(f"unknown task state: {requested_state}") from error
        requested_assignee = str(assignee or "").strip() or None
        result: list[dict[str, Any]] = []
        for legacy_id, task_id in self._space_mappings(space):
            task = self._task(task_id)
            if requested_state and task["status"] != requested_state:
                continue
            if requested_assignee and self._legacy_assignee(task) != requested_assignee:
                continue
            if not self._visible(task, actor, canonical_actor):
                continue
            result.append(self._item(task, space, legacy_id, actor, canonical_actor))
        return result

    def get_item(self, space: str, item_id: int, *, actor: Actor) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        legacy_id, task_id = self._item_mapping(space, item_id)
        task = self._task(task_id)
        if not self._visible(task, actor, canonical_actor):
            raise BoardNotFoundError(f"no visible item #{item_id} in space {space!r}")
        return self._item(task, space, legacy_id, actor, canonical_actor)

    def create_item(
        self,
        space: str,
        actor: Actor,
        *,
        title: str,
        criteria: str,
        description: str = "",
        parent: Optional[int] = None,
        case: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        self._require_role(actor, {Role.USER, Role.LEAD, Role.WORKER}, "create_item")
        title = str(title or "").strip()
        criteria = str(criteria or "").strip()
        if not title:
            raise BoardError("title is required")
        if not criteria:
            raise BoardError("acceptance criteria are required")

        # A client request id gives retries a durable idempotency boundary.  For
        # clients that predate it, each call is intentionally a new filing.
        request_key = str(request_id or "").strip()
        if len(request_key) > 256:
            raise BoardError("request_id is too long")
        if request_key:
            request_ledger_id = f"{self.workspace_id}:{request_key}"
            existing = self._mapping("legacy_board_requests", "create", request_ledger_id)
            if existing is not None:
                taskref = str(existing["aggregate_id"])
                task_id = taskref.removeprefix("taskref_")
                if not task_id or taskref == task_id:
                    raise LegacyBoardAdapterError("create request mapping is missing its task")
                task = self._task(task_id)
                legacy_id = self._legacy_id_for_task(task["task_id"], space=space)
                if legacy_id is None:
                    raise LegacyBoardAdapterError("create request mapping is missing its board item")
                if not self._visible(task, actor, canonical_actor):
                    raise BoardNotFoundError(f"no visible item in space {space!r}")
                if (
                    task["title"] != title
                    or task["acceptance_criteria"] != criteria
                    or task["description"] != str(description or "").strip()
                ):
                    raise LegacyBoardAdapterError("request_id was reused with a different create payload")
                return self._item(task, space, legacy_id, actor, canonical_actor)

        parent_task_id: Optional[str] = None
        if parent is not None:
            _, parent_task_id = self._item_mapping(space, int(parent))
            parent_task = self._task(parent_task_id)
            if not self._visible(parent_task, actor, canonical_actor):
                raise BoardNotFoundError(f"no visible item #{parent} in space {space!r}")

        legacy_id = self._next_id(space)
        entropy = request_key or uuid.uuid4().hex
        task_id = "task_" + self._stable("legacy-board-create", f"{self.workspace_id}:{space}:{entropy}")[:24]
        task = self.tasks.create_task(
            task_id,
            self.workspace_id,
            title,
            criteria,
            canonical_actor,
            description=description,
            parent_task_id=parent_task_id,
        )
        self._record_mapping("legacy_board", "item", f"{space}:{legacy_id}", "task", task_id)
        if request_key:
            self._record_mapping(
                "legacy_board_requests",
                "create",
                request_ledger_id,
                "create_request",
                "taskref_" + task_id,
            )
        self._append_task_event(
            task_id,
            canonical_actor,
            "board.created",
            {"legacy_space": space, "legacy_id": legacy_id, "case": case or ""},
            idempotency=f"create:{task_id}",
        )
        return self._item(self._task(task_id), space, legacy_id, actor, canonical_actor)

    def transition(
        self,
        space: str,
        actor: Actor,
        item_id: int,
        to: str,
        *,
        comment: str = "",
        refs: Optional[list[str]] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        legacy_id, task_id = self._item_mapping(space, int(item_id))
        task = self._task(task_id)
        if not self._visible(task, actor, canonical_actor):
            raise BoardNotFoundError(f"no visible item #{item_id} in space {space!r}")
        target = self._task_status(to)
        idempotency = (
            f"transition:{task_id}:{request_id}"
            if str(request_id or "").strip()
            else f"transition:{task_id}:{uuid.uuid4().hex}"
        )
        if request_id:
            existing_payload = self._event_payload(task_id, idempotency)
            if existing_payload is not None:
                if (
                    existing_payload.get("to") != target.value
                    or existing_payload.get("comment", "") != str(comment or "")
                    or existing_payload.get("refs", []) != list(refs or [])
                ):
                    raise LegacyBoardAdapterError("request_id was reused with a different transition payload")
                return self._item(self._task(task_id), space, legacy_id, actor, canonical_actor)
        authority = self._transition_authority(actor, task)
        updated = self.tasks.transition_task(
            task_id,
            target,
            actor_id=canonical_actor,
            authority=authority,
        )
        self._append_task_event(
            task_id,
            canonical_actor,
            "board.transitioned",
            {"from": task["status"], "to": target.value, "comment": str(comment or ""), "refs": list(refs or [])},
            idempotency=idempotency,
        )
        return self._item(self._task(task_id), space, legacy_id, actor, canonical_actor)

    def comment(
        self,
        space: str,
        actor: Actor,
        item_id: int,
        body: str,
        *,
        refs: Optional[list[str]] = None,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        legacy_id, task_id = self._item_mapping(space, int(item_id))
        task = self._task(task_id)
        if not self._visible(task, actor, canonical_actor):
            raise BoardNotFoundError(f"no visible item #{item_id} in space {space!r}")
        body = str(body or "").strip()
        if not body:
            raise BoardError("comment body is required")
        idempotency = (
            f"comment:{task_id}:{request_id}"
            if str(request_id or "").strip()
            else f"comment:{task_id}:{uuid.uuid4().hex}"
        )
        if request_id:
            existing_payload = self._event_payload(task_id, idempotency)
            if existing_payload is not None:
                if existing_payload.get("body") != body or existing_payload.get("refs", []) != list(refs or []):
                    raise LegacyBoardAdapterError("request_id was reused with a different comment payload")
                event_id = self._task_event_id(idempotency)
                return {"seq": self._event_position(event_id), "event_id": event_id, "item": self._item(self._task(task_id), space, legacy_id, actor, canonical_actor)}
        event_id = self._append_task_event(
            task_id,
            canonical_actor,
            "board.comment",
            {"body": body, "refs": list(refs or [])},
            idempotency=idempotency,
        )
        return {"seq": self._event_position(event_id), "event_id": event_id, "item": self._item(self._task(task_id), space, legacy_id, actor, canonical_actor)}

    def assign(self, space: str, actor: Actor, item_id: int, assignee: str) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        self._require_role(actor, {Role.USER, Role.LEAD}, "assign")
        legacy_id, task_id = self._item_mapping(space, int(item_id))
        task = self._task(task_id)
        target_actor, target_agent = self._canonical_agent(str(assignee or ""))
        updated = self.tasks.assign_task(task_id, target_agent, actor_id=canonical_actor)
        self._append_task_event(
            task_id,
            canonical_actor,
            "board.assigned",
            {"assignee": str(assignee), "canonical_actor_id": target_actor, "owner_agent_id": target_agent},
            idempotency=f"assign:{task_id}:{target_agent}",
        )
        return self._item(updated, space, legacy_id, actor, canonical_actor)

    def claim(self, space: str, actor: Actor, item_id: int) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        legacy_id, task_id = self._item_mapping(space, int(item_id))
        task = self._task(task_id)
        if task["status"] != TaskStatus.OPEN.value or task.get("owner_agent_id"):
            raise BoardError(f"item #{item_id} is not claimable")
        target_actor, target_agent = self._canonical_agent(actor.id)
        if target_actor != canonical_actor:
            raise AuthorityError("a board token may only claim as its own agent")
        updated = self.tasks.claim_task(task_id, target_agent)
        self._append_task_event(
            task_id,
            canonical_actor,
            "board.assigned",
            {"assignee": actor.id, "canonical_actor_id": target_actor, "owner_agent_id": target_agent, "claimed": True},
            idempotency=f"claim:{task_id}:{target_agent}",
        )
        return self._item(updated, space, legacy_id, actor, canonical_actor)

    def link(self, space: str, actor: Actor, src: int, kind: str, dst: int) -> dict[str, Any]:
        space = self._space(space)
        canonical_actor = self._canonical_actor(actor)
        self._require_role(actor, {Role.USER, Role.LEAD}, "link")
        kind = str(kind or "").strip()
        if kind not in {"parent", "blocks"}:
            raise BoardError("unknown link kind (use parent or blocks)")
        if int(src) == int(dst):
            raise BoardError("an item cannot link to itself")
        _, src_task_id = self._item_mapping(space, int(src))
        _, dst_task_id = self._item_mapping(space, int(dst))
        if kind == "blocks":
            with self.database._lock:
                exists = self.database._conn.execute(
                    "SELECT 1 FROM task_dependencies WHERE task_id=? AND depends_on_task_id=?",
                    (dst_task_id, src_task_id),
                ).fetchone()
            if exists is None:
                self.tasks.add_dependency(dst_task_id, src_task_id)
        else:
            self._set_parent(src_task_id, dst_task_id)
        event_id = self._append_task_event(
            src_task_id,
            canonical_actor,
            "board.linked",
            {"src": int(src), "kind": kind, "dst": int(dst)},
            idempotency=f"link:{space}:{src}:{kind}:{dst}",
        )
        return {"space": space, "src": int(src), "kind": kind, "dst": int(dst), "event_id": event_id}

    # --------------------------------------------------------------- canonical IO

    def _task(self, task_id: str) -> dict[str, Any]:
        task = self.tasks.get_task(task_id)
        if task is None or task.get("workspace_id") != self.workspace_id:
            raise BoardNotFoundError("canonical task is missing")
        return task

    def _space_mappings(self, space: str) -> list[tuple[int, str]]:
        prefix = f"{space}:"
        with self.database._lock:
            rows = self.database._conn.execute(
                """
                SELECT legacy_id, aggregate_id FROM legacy_id_mappings
                WHERE source_system='legacy_board' AND legacy_kind='item' AND legacy_id LIKE ?
                ORDER BY CAST(substr(legacy_id, ?) AS INTEGER)
                """,
                (prefix + "%", len(prefix) + 1),
            ).fetchall()
        result: list[tuple[int, str]] = []
        for row in rows:
            try:
                result.append((int(str(row["legacy_id"])[len(prefix):]), str(row["aggregate_id"])))
            except ValueError:
                continue
        return result

    def _item_mapping(self, space: str, item_id: int) -> tuple[int, str]:
        if item_id <= 0:
            raise BoardNotFoundError(f"no visible item #{item_id} in space {space!r}")
        mapping = self._mapping("legacy_board", "item", f"{space}:{item_id}")
        if mapping is None:
            raise BoardNotFoundError(f"no visible item #{item_id} in space {space!r}")
        return item_id, str(mapping["aggregate_id"])

    def _next_id(self, space: str) -> int:
        rows = self._space_mappings(space)
        return (max((item_id for item_id, _ in rows), default=0) + 1)

    def _visible(self, task: dict[str, Any], actor: Actor, canonical_actor: str) -> bool:
        if actor.role != Role.WORKER:
            return True
        owner_agent = str(task.get("owner_agent_id") or "")
        owner_actor = self._agent_actor(owner_agent) if owner_agent else ""
        if owner_actor == canonical_actor or task.get("created_by_actor_id") == canonical_actor:
            return True
        return task["status"] == TaskStatus.OPEN.value and not owner_agent

    def _item(self, task: dict[str, Any], space: str, legacy_id: int, actor: Actor, canonical_actor: str) -> dict[str, Any]:
        result = {
            "space": space,
            "id": int(legacy_id),
            "title": task["title"],
            "description": task["description"],
            "criteria": task["acceptance_criteria"],
            "state": task["status"],
            "assignee": self._legacy_assignee(task),
            "creator": self._legacy_creator(str(task["created_by_actor_id"])),
            "case_id": "",
            "refs": self._refs(task["task_id"]),
            "created_ts": task["created_at"],
            "updated_seq": self._event_position_for_task(task["task_id"]),
            "links": self._links(space, task["task_id"]),
            "comments": self._comments(task["task_id"]),
        }
        return result

    def _links(self, space: str, task_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        task = self._task(task_id)
        if task.get("parent_task_id"):
            parent = self._legacy_id_for_task(str(task["parent_task_id"]), space=space)
            if parent is not None:
                out.append({"kind": "parent", "item": parent})
        with self.database._lock:
            children = self.database._conn.execute(
                "SELECT task_id FROM tasks WHERE workspace_id=? AND parent_task_id=? ORDER BY task_id",
                (self.workspace_id, task_id),
            ).fetchall()
            dependencies = self.database._conn.execute(
                "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? ORDER BY depends_on_task_id",
                (task_id,),
            ).fetchall()
            blocked = self.database._conn.execute(
                "SELECT task_id FROM task_dependencies WHERE depends_on_task_id=? ORDER BY task_id",
                (task_id,),
            ).fetchall()
        for row in children:
            item_id = self._legacy_id_for_task(str(row["task_id"]), space=space)
            if item_id is not None:
                out.append({"kind": "child", "item": item_id})
        for row in dependencies:
            item_id = self._legacy_id_for_task(str(row["depends_on_task_id"]), space=space)
            if item_id is not None:
                out.append({"kind": "blocked_by", "item": item_id})
        for row in blocked:
            item_id = self._legacy_id_for_task(str(row["task_id"]), space=space)
            if item_id is not None:
                out.append({"kind": "blocks", "item": item_id})
        return out

    def _comments(self, task_id: str) -> list[dict[str, Any]]:
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT * FROM task_events WHERE task_id=? ORDER BY occurred_at, task_event_id",
                (task_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            legacy_payload = payload.get("legacy_payload") if isinstance(payload, dict) else {}
            if not isinstance(legacy_payload, dict):
                legacy_payload = {}
            body = payload.get("body") or payload.get("comment") or legacy_payload.get("body") or legacy_payload.get("comment")
            if not body:
                continue
            result.append({
                "seq": self._event_position(str(row["task_event_id"])),
                "ts": row["occurred_at"],
                "author": self._legacy_creator(str(row["actor_id"])),
                "role": "lead" if row["actor_id"] == self.requester_actor_id else "worker",
                "body": str(body),
                "taint": bool(payload.get("taint", False)),
            })
        return result

    def _refs(self, task_id: str) -> list[str]:
        refs: list[str] = []
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT payload_json FROM task_events WHERE task_id=? ORDER BY occurred_at, task_event_id",
                (task_id,),
            ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            for value in payload.get("refs") or (payload.get("legacy_payload") or {}).get("refs") or []:
                if str(value) not in refs:
                    refs.append(str(value))
        return refs

    # --------------------------------------------------------------- identity/auth

    def _canonical_actor(self, actor: Actor) -> str:
        if not isinstance(actor, Actor):
            raise AuthorityError("invalid board actor")
        self._require_role(actor, {Role.USER, Role.LEAD, Role.WORKER}, "board")
        if actor.id in {"user", "owner", self.requester_actor_id}:
            actor_id = self.requester_actor_id
        else:
            mapping = self._mapping("legacy_actors", "actor", actor.id)
            if mapping is None:
                raise AuthorityError(f"legacy actor is not imported: {actor.id}")
            actor_id = str(mapping["aggregate_id"])
        with self.database._lock:
            member = self.database._conn.execute(
                "SELECT 1 FROM workspace_memberships WHERE workspace_id=? AND actor_id=? AND status='active'",
                (self.workspace_id, actor_id),
            ).fetchone()
        if member is None:
            raise AuthorityError("board actor is not an active workspace member")
        return actor_id

    @staticmethod
    def _require_role(actor: Actor, allowed: set[Role], operation: str) -> None:
        if actor.role not in allowed:
            raise AuthorityError(f"{actor.role.value} may not {operation}")

    def _canonical_agent(self, legacy_actor: str) -> tuple[str, str]:
        key = str(legacy_actor or "").strip()
        if not key:
            raise BoardError("assignee is required")
        mapping = self._mapping("legacy_actors", "actor", key)
        if mapping is None:
            raise BoardError(f"legacy assignee is not imported: {key}")
        actor_id = str(mapping["aggregate_id"])
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT agent_id FROM agent_installations WHERE workspace_id=? AND actor_id=?",
                (self.workspace_id, actor_id),
            ).fetchone()
        if row is None:
            raise BoardError(f"legacy assignee is not an installed agent: {key}")
        return actor_id, str(row["agent_id"])

    def _agent_actor(self, agent_id: str) -> str:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT actor_id FROM agent_installations WHERE agent_id=? AND workspace_id=?",
                (agent_id, self.workspace_id),
            ).fetchone()
        return str(row["actor_id"]) if row is not None else ""

    def _transition_authority(self, actor: Actor, task: dict[str, Any]) -> TransitionAuthority:
        if actor.role == Role.WORKER:
            return TransitionAuthority.TASK_OWNER
        return TransitionAuthority.WORKSPACE_ADMIN

    # --------------------------------------------------------------- mappings/events

    def _mapping(self, source: str, kind: str, legacy_id: str) -> Optional[dict[str, Any]]:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM legacy_id_mappings WHERE source_system=? AND legacy_kind=? AND legacy_id=?",
                (source, kind, str(legacy_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def _record_mapping(self, source: str, kind: str, legacy_id: str, aggregate_kind: str, aggregate_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT aggregate_kind, aggregate_id FROM legacy_id_mappings WHERE source_system=? AND legacy_kind=? AND legacy_id=?",
                (source, kind, str(legacy_id)),
            ).fetchone()
            if existing is not None:
                if existing["aggregate_kind"] != aggregate_kind or existing["aggregate_id"] != aggregate_id:
                    raise LegacyBoardAdapterError(f"mapping conflict for {source}:{kind}:{legacy_id}")
                return
            try:
                connection.execute(
                    "INSERT INTO legacy_id_mappings (source_system, legacy_kind, legacy_id, aggregate_kind, aggregate_id, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (source, kind, str(legacy_id), aggregate_kind, aggregate_id, now),
                )
            except sqlite3.IntegrityError as error:
                raise LegacyBoardAdapterError(f"mapping collision for {aggregate_kind}:{aggregate_id}") from error

    def _append_task_event(self, task_id: str, actor_id: str, event_type: str, payload: dict[str, Any], *, idempotency: str) -> str:
        event_id = self._task_event_id(idempotency)
        with self.database.transaction() as connection:
            task = connection.execute(
                "SELECT workspace_id FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None or task["workspace_id"] != self.workspace_id:
                raise BoardNotFoundError("canonical task is missing")
            member = connection.execute(
                "SELECT 1 FROM workspace_memberships WHERE workspace_id=? AND actor_id=? AND status='active'",
                (self.workspace_id, actor_id),
            ).fetchone()
            if member is None:
                raise AuthorityError("event actor is not an active workspace member")
            connection.execute(
                "INSERT OR IGNORE INTO task_events (task_event_id, task_id, workspace_id, event_type, actor_id, correlation_id, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, task_id, self.workspace_id, event_type, actor_id, self._stable("legacy-board-correlation", idempotency), json.dumps(payload, sort_keys=True, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
            )
        return event_id

    def _task_event_id(self, idempotency: str) -> str:
        return "task_event_" + self._stable("legacy-board-event", f"{self.workspace_id}:{idempotency}")[:24]

    def _event_payload(self, task_id: str, idempotency: str) -> Optional[dict[str, Any]]:
        event_id = self._task_event_id(idempotency)
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT payload_json FROM task_events WHERE task_event_id=? AND task_id=?",
                (event_id, task_id),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _event_position(self, event_id: str) -> int:
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT task_event_id FROM task_events ORDER BY occurred_at, task_event_id"
            ).fetchall()
        for index, row in enumerate(rows, 1):
            if row["task_event_id"] == event_id:
                return index
        return 0

    def _event_position_for_task(self, task_id: str) -> int:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT task_event_id FROM task_events WHERE task_id=? ORDER BY occurred_at DESC, task_event_id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._event_position(str(row["task_event_id"])) if row is not None else 0

    def _legacy_assignee(self, task: dict[str, Any]) -> str:
        if not task.get("owner_agent_id"):
            return ""
        return self._legacy_actor_for_canonical(self._agent_actor(str(task["owner_agent_id"])))

    def _legacy_creator(self, actor_id: str) -> str:
        return self._legacy_actor_for_canonical(actor_id)

    def _legacy_actor_for_canonical(self, actor_id: str) -> str:
        if actor_id == self.requester_actor_id:
            return "user"
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT legacy_id FROM legacy_id_mappings WHERE source_system='legacy_actors' AND legacy_kind='actor' AND aggregate_kind='actor' AND aggregate_id=? ORDER BY legacy_id LIMIT 1",
                (actor_id,),
            ).fetchone()
            if row is not None:
                return str(row["legacy_id"])
            actor = self.database._conn.execute("SELECT handle FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
        return str(actor["handle"] or actor_id) if actor is not None else actor_id

    def _legacy_id_for_task(self, task_id: str, *, space: Optional[str] = None) -> Optional[int]:
        with self.database._lock:
            if space is None:
                row = self.database._conn.execute(
                    "SELECT legacy_id FROM legacy_id_mappings WHERE source_system='legacy_board' AND legacy_kind='item' AND aggregate_kind='task' AND aggregate_id=? LIMIT 1",
                    (task_id,),
                ).fetchone()
            else:
                row = self.database._conn.execute(
                    "SELECT legacy_id FROM legacy_id_mappings WHERE source_system='legacy_board' AND legacy_kind='item' AND aggregate_kind='task' AND aggregate_id=? AND legacy_id LIKE ? LIMIT 1",
                    (task_id, f"{space}:%"),
                ).fetchone()
        if row is None:
            return None
        try:
            return int(str(row["legacy_id"]).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None

    def _set_parent(self, child_task_id: str, parent_task_id: str) -> None:
        if child_task_id == parent_task_id:
            raise BoardError("an item cannot link to itself")
        with self.database.transaction() as connection:
            child = connection.execute("SELECT workspace_id FROM tasks WHERE task_id=?", (child_task_id,)).fetchone()
            parent = connection.execute("SELECT workspace_id FROM tasks WHERE task_id=?", (parent_task_id,)).fetchone()
            if child is None or parent is None or child["workspace_id"] != parent["workspace_id"]:
                raise BoardNotFoundError("linked task is missing")
            current = parent_task_id
            for _ in range(1000):
                if current == child_task_id:
                    raise BoardError("parent link would create a cycle")
                row = connection.execute("SELECT parent_task_id FROM tasks WHERE task_id=?", (current,)).fetchone()
                if row is None or row["parent_task_id"] is None:
                    break
                current = str(row["parent_task_id"])
            connection.execute("UPDATE tasks SET parent_task_id=?, updated_at=? WHERE task_id=?", (parent_task_id, datetime.now(timezone.utc).isoformat(), child_task_id))

    @staticmethod
    def _space(value: str) -> str:
        space = str(value or "").strip()
        if not space or len(space) > 200 or "\x00" in space or ":" in space:
            raise BoardError("invalid board space")
        return space

    @staticmethod
    def _task_status(value: str) -> TaskStatus:
        try:
            return TaskStatus(str(value or "").strip())
        except ValueError as error:
            raise BoardError(f"unknown task state: {value}") from error

    @staticmethod
    def _stable(kind: str, value: str) -> str:
        return hashlib.sha256(f"{kind}\x00{value}".encode("utf-8")).hexdigest()
