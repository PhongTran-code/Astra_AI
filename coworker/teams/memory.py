"""Team-scoped memory candidates, retention, export, and explicit acceptance."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .contracts import ActorKind, ContractError, new_domain_id
from .persistence import ContextRepository, PersistenceConflictError, TeamDatabase, WorkRepository
from .sharing import ContextScope


class MemoryConflict(ContractError):
    """A candidate would silently replace an already accepted fact."""


@dataclass(frozen=True)
class MemoryCandidate:
    candidate_id: str
    workspace_id: str
    scope_kind: str
    scope_id: str
    content: str
    importance: int
    retention_seconds: int | None
    source_run_id: str | None
    status: str
    memory_id: str | None = None
    conflict_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "workspace_id": self.workspace_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "content": self.content,
            "importance": self.importance,
            "retention_seconds": self.retention_seconds,
            "source_run_id": self.source_run_id,
            "status": self.status,
            "memory_id": self.memory_id,
            "conflict_key": self.conflict_key,
        }


class MemoryCandidateRepository:
    """Persistence owner for reviewable candidate facts; accepted memory uses ContextRepository."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create(
        self,
        candidate_id: str,
        workspace_id: str,
        scope_kind: ContextScope | str,
        scope_id: str,
        content: str,
        proposed_by_actor_id: str,
        *,
        source_run_id: str | None = None,
        importance: int = 0,
        retention_seconds: int | None = None,
        conflict_key: str | None = None,
    ) -> dict[str, Any]:
        candidate_id = _id(candidate_id, "memory candidate id")
        workspace_id = _id(workspace_id, "memory candidate workspace id")
        scope = _scope(scope_kind)
        scope_id = _id(scope_id, "memory candidate scope id")
        content = _content(content)
        proposed_by_actor_id = _id(proposed_by_actor_id, "memory candidate actor id")
        source_run_id = _optional_id(source_run_id)
        if isinstance(importance, bool) or not isinstance(importance, int) or not 0 <= importance <= 100:
            raise ContractError("memory candidate importance must be between 0 and 100")
        if retention_seconds is not None and (isinstance(retention_seconds, bool) or not isinstance(retention_seconds, int) or retention_seconds <= 0):
            raise ContractError("memory candidate retention must be positive")
        conflict_key = None if conflict_key is None else _id(conflict_key, "memory conflict key")
        now = _utc_now()
        content_hash = _hash(content)
        with self.database.transaction() as connection:
            _require_member(connection, workspace_id, proposed_by_actor_id)
            if source_run_id is not None:
                run = connection.execute("SELECT workspace_id FROM runs WHERE run_id = ?", (source_run_id,)).fetchone()
                if run is None or run["workspace_id"] != workspace_id:
                    raise ContractError("memory candidate source run is missing or cross-workspace")
            existing = connection.execute(
                "SELECT * FROM memory_candidates WHERE workspace_id = ? AND scope_kind = ? AND scope_id = ? AND content_hash = ?",
                (workspace_id, scope.value, scope_id, content_hash),
            ).fetchone()
            if existing is not None:
                return _candidate_row(existing)
            connection.execute(
                """
                INSERT INTO memory_candidates
                    (candidate_id, workspace_id, scope_kind, scope_id, content,
                     content_hash, conflict_key, importance, retention_seconds,
                     source_run_id, proposed_by_actor_id, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (candidate_id, workspace_id, scope.value, scope_id, content, content_hash,
                 conflict_key, importance, retention_seconds, source_run_id, proposed_by_actor_id, now),
            )
            row = connection.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        assert row is not None
        return _candidate_row(row)

    def get(self, candidate_id: str) -> dict[str, Any] | None:
        candidate_id = _id(candidate_id, "memory candidate id")
        with self.database._lock:
            row = self.database._conn.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        return _candidate_row(row) if row is not None else None

    def list_scope(self, workspace_id: str, scope_kind: ContextScope | str, scope_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        workspace_id = _id(workspace_id, "memory candidate workspace id")
        scope = _scope(scope_kind)
        scope_id = _id(scope_id, "memory candidate scope id")
        where = ["workspace_id = ?", "scope_kind = ?", "scope_id = ?"]
        params: list[Any] = [workspace_id, scope.value, scope_id]
        if status is not None:
            if status not in {"pending", "accepted", "rejected"}:
                raise ContractError("memory candidate status is unsupported")
            where.append("status = ?")
            params.append(status)
        with self.database._lock:
            rows = self.database._conn.execute(
                f"SELECT * FROM memory_candidates WHERE {' AND '.join(where)} ORDER BY created_at, candidate_id", params
            ).fetchall()
        return [_candidate_row(row) for row in rows]

    def list_for_workspace(
        self, workspace_id: str, *, status: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        workspace_id = _id(workspace_id, "memory candidate workspace id")
        if status is not None and status not in {"pending", "accepted", "rejected"}:
            raise ContractError("memory candidate status is unsupported")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ContractError("memory candidate limit must be between 1 and 500")
        with self.database._lock:
            if status is None:
                rows = self.database._conn.execute(
                    "SELECT * FROM memory_candidates WHERE workspace_id = ? ORDER BY created_at DESC, candidate_id LIMIT ?",
                    (workspace_id, limit),
                ).fetchall()
            else:
                rows = self.database._conn.execute(
                    "SELECT * FROM memory_candidates WHERE workspace_id = ? AND status = ? ORDER BY created_at DESC, candidate_id LIMIT ?",
                    (workspace_id, status, limit),
                ).fetchall()
        return [_candidate_row(row) for row in rows]

    def decide(self, candidate_id: str, status: str, *, memory_id: str | None = None) -> dict[str, Any]:
        candidate_id = _id(candidate_id, "memory candidate id")
        if status not in {"accepted", "rejected"}:
            raise ContractError("memory candidate decision is unsupported")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
            if row is None:
                raise ContractError("memory candidate not found")
            if row["status"] != "pending":
                if row["status"] == status and row["memory_id"] == memory_id:
                    return _candidate_row(row)
                raise ContractError("memory candidate is already decided")
            connection.execute("UPDATE memory_candidates SET status = ?, memory_id = ?, decided_at = ? WHERE candidate_id = ?", (status, memory_id, _utc_now(), candidate_id))
            updated = connection.execute("SELECT * FROM memory_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        assert updated is not None
        return _candidate_row(updated)


class TeamMemoryService:
    """Keep memory scope explicit and candidate text inert until a caller accepts it."""

    def __init__(self, database: TeamDatabase, context: ContextRepository, work: WorkRepository) -> None:
        self.database = database
        self.context = context
        self.work = work
        self.candidates = MemoryCandidateRepository(database)

    def propose_from_run(
        self,
        run_id: str,
        *,
        scope: ContextScope | str,
        scope_id: str,
        proposed_by_actor_id: str,
        extractor: Callable[[Mapping[str, Any]], Sequence[str | Mapping[str, Any]]],
        source_metadata: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        run = self.work.get_run(run_id)
        if run is None:
            raise ContractError("memory source run not found")
        request = self.work.get_request(run["request_id"])
        if request is None:
            raise ContractError("memory source request not found")
        scope = _scope(scope)
        # The extractor receives only durable, bounded metadata. It cannot ask
        # this service for another scope and its output never becomes authority.
        source = {
            "run_id": run_id,
            "request_id": request["request_id"],
            "task_id": request.get("task_id"),
            "status": run["status"],
        }
        if isinstance(source_metadata, Mapping):
            # The optional hook gets bounded, caller-supplied evidence only; it
            # never receives the database handle or another context scope.
            for key, value in source_metadata.items():
                if str(key) in {"response", "expected_output"}:
                    source[str(key)] = str(value or "")[:20_000]
        outputs = extractor(source)
        if isinstance(outputs, (str, bytes)) or not isinstance(outputs, Sequence):
            raise ContractError("memory extractor must return a sequence")
        result = []
        for item in outputs:
            if isinstance(item, Mapping):
                content = item.get("content", "")
                importance = item.get("importance", 0)
                conflict_key = item.get("conflict_key")
                retention_seconds = item.get("retention_seconds")
            else:
                content, importance, conflict_key, retention_seconds = item, 0, None, None
            result.append(self.candidates.create(
                new_domain_id("memory"), request["workspace_id"], scope, scope_id,
                str(content), proposed_by_actor_id, source_run_id=run_id,
                importance=importance, conflict_key=conflict_key, retention_seconds=retention_seconds,
            ))
        return result

    def accept(self, candidate_id: str, *, actor_id: str) -> dict[str, Any]:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ContractError("memory candidate not found")
        _require_member(self.database._conn, candidate["workspace_id"], actor_id)
        if candidate["status"] != "pending":
            return candidate
        if candidate.get("conflict_key"):
            accepted = self.database._conn.execute(
                "SELECT 1 FROM memory_candidates WHERE workspace_id = ? AND scope_kind = ? AND scope_id = ? AND conflict_key = ? AND status = 'accepted' AND content_hash <> ? LIMIT 1",
                (candidate["workspace_id"], candidate["scope_kind"], candidate["scope_id"], candidate["conflict_key"], candidate["content_hash"]),
            ).fetchone()
            if accepted is not None:
                raise MemoryConflict("candidate conflicts with an accepted memory")
        expires_at = None
        if candidate.get("retention_seconds"):
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(candidate["retention_seconds"]))).isoformat()
        memory = self.context.create_memory(
            new_domain_id("memory"), candidate["workspace_id"], candidate["scope_kind"], candidate["scope_id"],
            candidate["content"], actor_id, source_run_id=candidate.get("source_run_id"), expires_at=expires_at,
        )
        self.candidates.decide(candidate_id, "accepted", memory_id=memory["memory_id"])
        return memory

    def reject(self, candidate_id: str, *, actor_id: str) -> dict[str, Any]:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ContractError("memory candidate not found")
        _require_member(self.database._conn, candidate["workspace_id"], actor_id)
        return self.candidates.decide(candidate_id, "rejected")

    def export_scope(self, workspace_id: str, scope: ContextScope | str, scope_id: str) -> list[dict[str, Any]]:
        return self.context.list_memories(workspace_id, _scope(scope), _id(scope_id, "memory export scope id"), include_expired=True)


def _candidate_row(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _scope(value: ContextScope | str) -> ContextScope:
    try:
        return value if isinstance(value, ContextScope) else ContextScope(value)
    except (TypeError, ValueError) as error:
        raise ContractError("invalid memory scope") from error


def _id(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 128 or any(char in value for char in "\x00\r\n"):
        raise ContractError(f"{label} is malformed")
    return value


def _optional_id(value: Any) -> str | None:
    return None if value is None or not str(value).strip() else _id(value, "optional id")


def _content(value: Any) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 20_000:
        raise ContractError("memory candidate content is required and must be bounded")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_member(connection: Any, workspace_id: str, actor_id: str) -> None:
    row = connection.execute("SELECT actor_kind FROM actors WHERE actor_id = ?", (actor_id,)).fetchone()
    membership = connection.execute("SELECT 1 FROM workspace_memberships WHERE workspace_id = ? AND actor_id = ? AND status = 'active'", (workspace_id, actor_id)).fetchone()
    if row is None or membership is None:
        raise ContractError("actor is not an active workspace member")
