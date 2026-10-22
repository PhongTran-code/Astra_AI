"""Explicit, idempotent import of the pre-Team stores into canonical Team.

The old board, chat, session, registry, and scheduler files are compatibility
inputs only.  This module never opens them in write mode.  Import is deliberately
an operator action: it takes a byte-for-byte backup first, records every imported
source identity in ``legacy_id_mappings``, and can be safely retried after a
process crash.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .contracts import ActorKind, ActorRef, AgentRole, AgentStatus, ContractError, ConversationKind, DomainEvent
from .lifecycle import TaskStatus
from .persistence import (
    AgentRepository,
    ConversationRepository,
    EventRepository,
    IdentityRepository,
    PersistenceConflictError,
    TaskRepository,
    TeamDatabase,
)
from .routine import RoutineRepository
from .sharing import HistoryPolicy


class LegacyCutoverError(ContractError):
    """The legacy snapshot is unsafe to import or is internally inconsistent."""


@dataclass(frozen=True)
class LegacyCutoverReport:
    """Operator-visible evidence for one preflight or import attempt."""

    legacy_counts: Mapping[str, int]
    canonical_counts: Mapping[str, int]
    comparisons: Mapping[str, int]
    hash_chain_status: Mapping[str, str]
    foreign_key_errors: tuple[tuple[Any, ...], ...]
    corrupt_sources: tuple[str, ...]
    mapping_count: int = 0
    imported: bool = False
    backup_path: Optional[str] = None

    @property
    def is_usable(self) -> bool:
        return not self.corrupt_sources and not self.foreign_key_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy_counts": dict(self.legacy_counts),
            "canonical_counts": dict(self.canonical_counts),
            "comparisons": dict(self.comparisons),
            "hash_chain_status": dict(self.hash_chain_status),
            "foreign_key_errors": [list(item) for item in self.foreign_key_errors],
            "corrupt_sources": list(self.corrupt_sources),
            "mapping_count": self.mapping_count,
            "imported": self.imported,
            "backup_path": self.backup_path,
            "is_usable": self.is_usable,
        }


class LegacyCutoverService:
    """Import legacy durable state without creating executable work.

    The service uses the canonical repositories for identity, conversations,
    tasks, routines, and domain events.  The few raw inserts are limited to
    immutable task-event and import-ledger rows because those records have no
    public command yet.  Every raw insert is FK checked by the same Team DB.
    """

    _DATABASES = ("coworker.db", "teams.db", "chat.db", "automation.db")
    _COPY_FILES = _DATABASES + ("teams.json",)
    _SECRET_KEYS = re.compile(r"(?:token|secret|password|api[_-]?key|cookie|authorization)", re.I)

    def __init__(
        self,
        database: TeamDatabase,
        workspace_id: str,
        requester_actor_id: str,
        *,
        backup_root: str | Path | None = None,
    ) -> None:
        self.database = database
        self.workspace_id = str(workspace_id).strip()
        self.requester_actor_id = str(requester_actor_id).strip()
        if not self.workspace_id or not self.requester_actor_id:
            raise LegacyCutoverError("workspace_id and requester_actor_id are required")
        self.backup_root = Path(backup_root).expanduser() if backup_root else database.backup_dir / "legacy-cutover"
        self.identity = IdentityRepository(database)
        self.agents = AgentRepository(database)
        self.conversations = ConversationRepository(database)
        self.tasks = TaskRepository(database)
        self.routines = RoutineRepository(database)
        self.events = EventRepository(database)
        self._agent_by_key: dict[str, tuple[str, str]] = {}
        self._actor_by_legacy: dict[str, str] = {
            "user": requester_actor_id,
            "owner": requester_actor_id,
            requester_actor_id: requester_actor_id,
        }

    # ------------------------------------------------------------------ public API

    def preflight(self, legacy_base: str | Path) -> LegacyCutoverReport:
        """Read all legacy inputs and return evidence without changing either side."""

        base = self._source_dir(legacy_base)
        parity = self.database.parity_report(base)
        counts = dict(parity.legacy_counts)
        corrupt = list(parity.corrupt_sources)
        self._scan_json_sqlite(base / "coworker.db", ("sessions",), counts, corrupt, self._validate_session_row)
        self._scan_json_sqlite(base / "teams.db", ("team_events", "team_items", "team_links"), counts, corrupt, self._validate_board_row)
        self._scan_json_sqlite(base / "chat.db", ("chat_groups", "chat_messages"), counts, corrupt, self._validate_chat_row)
        self._scan_json_sqlite(base / "automation.db", ("scheduled_tasks", "task_runs"), counts, corrupt, self._validate_automation_row)
        registry = base / "teams.json"
        if registry.exists():
            try:
                value = json.loads(registry.read_text(encoding="utf-8"))
                if not isinstance(value, Mapping) or not isinstance(value.get("teams", []), list):
                    raise ValueError("teams.json must contain a teams list")
                counts["registry_teams"] = len(value.get("teams", []))
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                corrupt.append(f"{registry}: {error}")
        else:
            counts.setdefault("registry_teams", 0)
        counts.setdefault("automation_tasks", 0)
        counts.setdefault("automation_runs", 0)
        return LegacyCutoverReport(
            legacy_counts=counts,
            canonical_counts=self._canonical_counts(),
            comparisons=dict(parity.comparisons),
            hash_chain_status=dict(parity.hash_chain_status),
            foreign_key_errors=parity.foreign_key_errors,
            corrupt_sources=tuple(dict.fromkeys(corrupt)),
            mapping_count=self._mapping_count(),
        )

    def backup(self, legacy_base: str | Path, destination: str | Path | None = None) -> Path:
        """Copy known legacy stores and transcripts to a validated immutable snapshot."""

        source = self._source_dir(legacy_base)
        root = Path(destination).expanduser() if destination else self.backup_root / f"snapshot-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        root = root.resolve()
        if root == source or source in root.parents:
            raise LegacyCutoverError("backup destination must be outside the legacy source")
        if root.exists():
            raise LegacyCutoverError(f"backup destination already exists: {root}")
        root.mkdir(parents=True, exist_ok=False)
        copied: list[dict[str, Any]] = []
        for name in self._COPY_FILES:
            path = source / name
            if path.is_file():
                target = root / name
                shutil.copy2(path, target)
                copied.append({"path": name, "sha256": _sha256(path), "size": path.stat().st_size})
        for directory_name in ("conversations", "attachments", "team-attachments"):
            path = source / directory_name
            if path.is_dir():
                target = root / directory_name
                shutil.copytree(path, target)
                for child in sorted(target.rglob("*")):
                    if child.is_file():
                        relative = child.relative_to(root).as_posix()
                        copied.append({"path": relative, "sha256": _sha256(child), "size": child.stat().st_size})
        manifest = {
            "format": "astra-legacy-cutover-backup-v1",
            "source": str(source),
            "created_at": _utc_now(),
            "files": copied,
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return root

    def import_legacy(
        self,
        legacy_base: str | Path,
        *,
        create_backup: bool = True,
        backup_destination: str | Path | None = None,
        activate: bool = False,
    ) -> LegacyCutoverReport:
        """Import all readable legacy aggregates; rerunning is idempotent."""

        base = self._source_dir(legacy_base)
        report = self.preflight(base)
        if not report.is_usable:
            raise LegacyCutoverError("legacy preflight failed: " + "; ".join(report.corrupt_sources))
        backup_path = self.backup(base, backup_destination) if create_backup else None
        self._require_requester()
        self._import_sessions(base)
        self._import_board(base)
        self._import_chat(base)
        self._import_registry(base)
        self._import_automations(base)
        verified = self.verify(base)
        if activate:
            self.activate(base)
            verified = self.verify(base)
        return LegacyCutoverReport(
            **{**verified.__dict__, "imported": True, "backup_path": str(backup_path) if backup_path else None}
        )

    def verify(self, legacy_base: str | Path) -> LegacyCutoverReport:
        """Verify source health, import ledger coverage, and canonical FK integrity."""

        report = self.preflight(legacy_base)
        missing: list[str] = []
        for source, kind, identifier in self._expected_mapping_keys(legacy_base):
            if self._mapping(source, kind, identifier) is None:
                missing.append(f"{source}:{kind}:{identifier}")
        if missing:
            report = LegacyCutoverReport(
                **{**report.__dict__, "corrupt_sources": tuple(report.corrupt_sources) + tuple("unmapped " + item for item in missing)}
            )
        return report

    def activate(self, legacy_base: str | Path) -> LegacyCutoverReport:
        """Atomically mark the canonical compatibility adapter as authoritative.

        Activation is separate from import so an operator can rehearse, restart,
        and verify before legacy HTTP writes are fenced.  It is an append-only
        ledger marker and never mutates a legacy file.
        """

        report = self.verify(legacy_base)
        if not report.is_usable:
            raise LegacyCutoverError("cannot activate an unverified legacy import")
        self._record_mapping(
            "legacy_cutover",
            "state",
            "active",
            "cutover",
            "cutover_" + _stable("cutover", self.workspace_id)[:24],
        )
        return self.verify(legacy_base)

    def is_active(self) -> bool:
        return self._mapping("legacy_cutover", "state", "active") is not None

    # -------------------------------------------------------------- source readers

    @staticmethod
    def _source_dir(value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise LegacyCutoverError(f"legacy state directory does not exist: {path}")
        return path

    def _scan_json_sqlite(
        self,
        path: Path,
        tables: Iterable[str],
        counts: dict[str, int],
        corrupt: list[str],
        validator,
    ) -> None:
        if not path.exists():
            return
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            for table in tables:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
                if exists is None:
                    continue
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                if table == "scheduled_tasks":
                    counts["automation_tasks"] = len(rows)
                elif table == "task_runs":
                    counts["automation_runs"] = len(rows)
                for row in rows:
                    try:
                        validator(table, dict(row))
                    except (TypeError, ValueError, json.JSONDecodeError) as error:
                        corrupt.append(f"{path}:{table}: {error}")
        except (sqlite3.DatabaseError, OSError) as error:
            corrupt.append(f"{path}: {error}")
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate_session_row(table: str, row: Mapping[str, Any]) -> None:
        for key in ("extra_roots", "grants", "compaction", "team", "bindings", "messages"):
            if row.get(key):
                json.loads(row[key])

    @staticmethod
    def _validate_board_row(table: str, row: Mapping[str, Any]) -> None:
        if table == "team_events":
            json.loads(row.get("payload") or "{}")
        elif table == "team_items":
            json.loads(row.get("refs") or "[]")
            if str(row.get("state") or "open") not in {"open", "in_progress", "blocked", "review", "done", "canceled"}:
                raise ValueError("board item state is unsupported")

    @staticmethod
    def _validate_chat_row(table: str, row: Mapping[str, Any]) -> None:
        if table == "chat_groups":
            json.loads(row.get("members") or "[]")
        elif table == "chat_messages":
            json.loads(row.get("mentions") or "[]")

    @staticmethod
    def _validate_automation_row(table: str, row: Mapping[str, Any]) -> None:
        json.loads(row.get("data") or "{}")

    def _rows(self, base: Path, filename: str, table: str) -> list[dict[str, Any]]:
        path = base / filename
        if not path.exists():
            return []
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()] if exists else []
        finally:
            connection.close()

    def _session_messages(self, base: Path, session_id: str, inline: Any) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            raise LegacyCutoverError(f"unsafe legacy session id: {session_id!r}")
        path = base / "conversations" / f"{session_id}.jsonl"
        messages: list[dict[str, Any]] = []
        if path.exists():
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise LegacyCutoverError(f"{path}:{line_number}: message must be an object")
                messages.append(dict(value))
        elif inline:
            value = json.loads(inline) if isinstance(inline, str) else inline
            if isinstance(value, list):
                messages = [dict(item) for item in value if isinstance(item, Mapping)]
        return messages

    # ---------------------------------------------------------- canonical helpers

    def _require_requester(self) -> None:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT 1 FROM workspace_memberships WHERE workspace_id=? AND actor_id=? AND status='active'",
                (self.workspace_id, self.requester_actor_id),
            ).fetchone()
        if row is None:
            raise LegacyCutoverError("requester must be an active workspace member")

    def _ensure_system(self) -> str:
        actor_id = "actor_" + _stable("system", self.workspace_id)[:24]
        with self.database._lock:
            exists = self.database._conn.execute("SELECT 1 FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
        if exists is None:
            try:
                self.identity.create_actor(ActorRef(actor_id, ActorKind.SYSTEM, "Legacy import system"))
            except PersistenceConflictError:
                pass
        self._record_mapping("legacy_actors", "actor", "system", "actor", actor_id)
        return actor_id

    def _ensure_agent(self, legacy_actor: str, *, persona: str = "legacy", model: str = "", role: str = "worker") -> tuple[str, str]:
        legacy_actor = str(legacy_actor or "legacy").strip() or "legacy"
        if legacy_actor in self._actor_by_legacy and self._actor_by_legacy[legacy_actor] != self.requester_actor_id:
            actor_id = self._actor_by_legacy[legacy_actor]
            with self.database._lock:
                row = self.database._conn.execute("SELECT agent_id FROM agent_installations WHERE actor_id=?", (actor_id,)).fetchone()
            if row is not None:
                return actor_id, row["agent_id"]
        key = f"{legacy_actor}\x00{persona}\x00{model}\x00{role}"
        existing = self._agent_by_key.get(key)
        if existing:
            return existing
        digest = _stable("agent", f"{self.workspace_id}:{key}")[:24]
        actor_id, definition_id, revision_id, agent_id = (f"{prefix}_{digest}" for prefix in ("actor", "agent_definition", "agent_revision", "agent"))
        display = str(persona or legacy_actor or "Legacy agent").strip()[:120] or "Legacy agent"
        handle = _handle(persona or legacy_actor, digest)
        agent_role = _agent_role(role)
        actor = ActorRef(actor_id, ActorKind.AGENT, display)
        try:
            self.identity.create_actor(actor, handle=handle)
        except PersistenceConflictError:
            pass
        try:
            self.identity.add_membership(f"membership_{digest}", self.workspace_id, actor_id, "member")
        except PersistenceConflictError:
            pass
        try:
            self.agents.create_definition(definition_id, self.workspace_id, self.requester_actor_id, display, handle, agent_role, description="Imported legacy teammate")
        except PersistenceConflictError:
            pass
        try:
            self.agents.create_revision(revision_id, definition_id, 1, "Imported legacy teammate. Preserve historical context; wait for a new canonical work request before executing.", self.requester_actor_id, model_policy={"legacy_model": model} if model else {})
        except PersistenceConflictError:
            pass
        if self.agents.get_installation(agent_id) is None:
            try:
                self.agents.install(agent_id, self.workspace_id, actor_id, definition_id, revision_id, status=AgentStatus.READY)
            except PersistenceConflictError:
                pass
        self._actor_by_legacy[legacy_actor] = actor_id
        self._agent_by_key[key] = (actor_id, agent_id)
        # Keep the legacy actor identity resolvable after restart.  The in-memory
        # cache above is only an import optimization; the mapping ledger is the
        # durable authority used by the compatibility board adapter.
        self._record_mapping("legacy_actors", "actor", legacy_actor, "actor", actor_id)
        return actor_id, agent_id

    def _actor_for(self, legacy_actor: str, *, persona: str = "legacy", model: str = "", role: str = "worker") -> tuple[str, Optional[str]]:
        key = str(legacy_actor or "").strip()
        if key in {"user", "owner", self.requester_actor_id}:
            return self.requester_actor_id, None
        if str(role).lower() == "system" or key == "system":
            return self._ensure_system(), None
        return self._ensure_agent(key or persona, persona=persona, model=model, role=role)

    def _mapping(self, source: str, kind: str, legacy_id: str) -> Optional[dict[str, Any]]:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM legacy_id_mappings WHERE source_system=? AND legacy_kind=? AND legacy_id=?",
                (source, kind, str(legacy_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def _record_mapping(self, source: str, kind: str, legacy_id: str, aggregate_kind: str, aggregate_id: str) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT aggregate_kind, aggregate_id FROM legacy_id_mappings WHERE source_system=? AND legacy_kind=? AND legacy_id=?",
                (source, kind, str(legacy_id)),
            ).fetchone()
            if existing is not None:
                if existing["aggregate_kind"] != aggregate_kind or existing["aggregate_id"] != aggregate_id:
                    raise LegacyCutoverError(f"legacy mapping conflict for {source}:{kind}:{legacy_id}")
                return
            try:
                connection.execute(
                    "INSERT INTO legacy_id_mappings (source_system, legacy_kind, legacy_id, aggregate_kind, aggregate_id, imported_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (source, kind, str(legacy_id), aggregate_kind, aggregate_id, now),
                )
            except sqlite3.IntegrityError as error:
                raise LegacyCutoverError(f"legacy mapping collision for {aggregate_kind}:{aggregate_id}") from error

    def _mapping_count(self) -> int:
        with self.database._lock:
            return int(self.database._conn.execute("SELECT COUNT(*) FROM legacy_id_mappings").fetchone()[0])

    def _canonical_counts(self) -> dict[str, int]:
        counts = self.database.table_counts()
        with self.database._lock:
            for table in ("crew_runs", "task_events", "team_routines", "routine_occurrences", "legacy_id_mappings"):
                counts[table] = int(self.database._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return counts

    def _emit_import_event(self, aggregate_kind: str, aggregate_id: str, source: str, legacy_id: str, metadata: Mapping[str, Any] | None = None) -> None:
        event_id = _stable("event", f"legacy:{source}:{legacy_id}")
        with self.database._lock:
            existing = self.database._conn.execute("SELECT occurred_at FROM domain_events WHERE event_id=?", (event_id,)).fetchone()
        self.events.append(DomainEvent(
            event_id=event_id,
            event_type="legacy.imported",
            workspace_id=self.workspace_id,
            actor_id=self.requester_actor_id,
            aggregate_type=aggregate_kind,
            aggregate_id=aggregate_id,
            correlation_id=_stable("correlation", f"legacy:{source}:{legacy_id}"),
            payload={"source_system": source, "legacy_id": str(legacy_id), "metadata": _safe_json(metadata or {})},
            occurred_at=existing["occurred_at"] if existing is not None else _utc_now(),
        ))

    # -------------------------------------------------------------- import lanes

    def _import_sessions(self, base: Path) -> None:
        rows = self._rows(base, "coworker.db", "sessions")
        known = {str(row.get("session_id")): row for row in rows if row.get("session_id")}
        conv_dir = base / "conversations"
        if conv_dir.is_dir():
            for path in sorted(conv_dir.glob("*.jsonl")):
                known.setdefault(path.stem, {"session_id": path.stem})
        for session_id, row in sorted(known.items()):
            conversation_id = "conversation_" + _stable("session", f"{self.workspace_id}:{session_id}")[:24]
            agent_actor, agent_id = self._ensure_agent(str(row.get("agent") or "legacy"), model=str(row.get("model") or ""), persona=str(row.get("agent") or "legacy"), role="worker")
            if self.conversations.get_conversation(conversation_id) is None:
                self.conversations.create_conversation(conversation_id, self.workspace_id, ConversationKind.DIRECT, self.requester_actor_id, title=str(row.get("title") or "Legacy session")[:160], reply_mode="coordinator", coordinator_agent_id=agent_id)
                self.conversations.add_member(conversation_id, self.requester_actor_id, member_role="owner", history_policy=HistoryPolicy.ALL_HISTORY)
                self.conversations.add_member(conversation_id, agent_actor, member_role="agent", history_policy=HistoryPolicy.ALL_HISTORY)
            system_actor = self._ensure_system()
            messages = self._session_messages(base, session_id, row.get("messages"))
            for index, message in enumerate(messages):
                role = str(message.get("role") or "user").lower()
                actor_id = self.requester_actor_id if role == "user" else agent_actor if role == "assistant" else system_actor
                actor_kind = ActorKind.HUMAN if actor_id == self.requester_actor_id else ActorKind.AGENT if actor_id == agent_actor else ActorKind.SYSTEM
                body = _body(message)
                message_id = "message_" + _stable("session-message", f"{session_id}:{index}:{body}")[:24]
                self.conversations.append_message(message_id, conversation_id, ActorRef(actor_id, actor_kind, "Legacy import"), body, _stable("message-idempotency", f"legacy-session:{session_id}:{index}:{body}"), _stable("correlation", f"legacy-session:{session_id}"), created_at=_timestamp(message.get("created_at") or message.get("timestamp") or row.get("updated_at")))
                self._record_mapping("legacy_sessions", "message", f"{session_id}:{index}", "message", message_id)
            self._record_mapping("legacy_sessions", "session", session_id, "conversation", conversation_id)
            self._emit_import_event("conversation", conversation_id, "legacy_sessions", session_id, {"model": row.get("model"), "mode": row.get("mode"), "metadata": {"extra_roots": _json_value(row.get("extra_roots"), []), "bindings": _json_value(row.get("bindings"), {}), "team": _json_value(row.get("team"), {})}})

    def _import_board(self, base: Path) -> None:
        items = self._rows(base, "teams.db", "team_items")
        task_ids: dict[tuple[str, int], str] = {}
        for item in sorted(items, key=lambda row: (str(row.get("space") or ""), int(row.get("id") or 0))):
            space, legacy_id = str(item.get("space") or "legacy"), int(item.get("id") or 0)
            task_id = "task_" + _stable("board-item", f"{self.workspace_id}:{space}:{legacy_id}")[:24]
            task_ids[(space, legacy_id)] = task_id
            actor, agent_id = self._actor_for(str(item.get("assignee") or ""), role="worker") if item.get("assignee") else (None, None)
            status = TaskStatus(str(item.get("state") or "open"))
            if self.tasks.get_task(task_id) is None:
                self.tasks.create_task(task_id, self.workspace_id, str(item.get("title") or f"Legacy item {legacy_id}")[:500], str(item.get("criteria") or "Imported legacy board item")[:4000], self.requester_actor_id, description=str(item.get("description") or "")[:20000], owner_agent_id=agent_id, status=status)
                with self.database.transaction() as connection:
                    created_at = _timestamp(item.get("created_ts"))
                    connection.execute("UPDATE tasks SET created_at=?, updated_at=? WHERE task_id=?", (created_at, created_at, task_id))
            self._record_mapping("legacy_board", "item", f"{space}:{legacy_id}", "task", task_id)
            self._emit_import_event("task", task_id, "legacy_board", f"{space}:{legacy_id}", {"refs": _json_value(item.get("refs"), [])})
        links = self._rows(base, "teams.db", "team_links")
        for link in links:
            space = str(link.get("space") or "legacy")
            src, dst, kind = int(link.get("src") or 0), int(link.get("dst") or 0), str(link.get("kind") or "")
            if (space, src) not in task_ids or (space, dst) not in task_ids:
                continue
            if kind == "blocks":
                self._ensure_dependency(task_ids[(space, dst)], task_ids[(space, src)])
            elif kind == "parent":
                with self.database.transaction() as connection:
                    connection.execute("UPDATE tasks SET parent_task_id=? WHERE task_id=?", (task_ids[(space, dst)], task_ids[(space, src)]))
            self._record_mapping("legacy_board", "link", f"{space}:{src}:{kind}:{dst}", "task", task_ids[(space, src)])
        for event in sorted(self._rows(base, "teams.db", "team_events"), key=lambda row: int(row.get("seq") or 0)):
            space, seq = str(event.get("space") or "legacy"), int(event.get("seq") or 0)
            task_id = task_ids.get((space, int(event["item_id"]))) if event.get("item_id") is not None else None
            actor, _ = self._actor_for(str(event.get("actor") or "legacy"), persona=str(event.get("persona") or "legacy"), model=str(event.get("model") or ""), role=str(event.get("actor_role") or "worker"))
            payload = {"legacy_seq": seq, "legacy_hash": event.get("hash"), "legacy_prev_hash": event.get("prev_hash"), "legacy_payload": _safe_json(_json_value(event.get("payload"), {})), "case_id": event.get("case_id"), "recipient": event.get("recipient"), "taint": bool(event.get("taint"))}
            if task_id is not None:
                event_id = "task_event_" + _stable("board-event", f"{self.workspace_id}:{space}:{seq}")[:24]
                with self.database.transaction() as connection:
                    connection.execute("INSERT OR IGNORE INTO task_events (task_event_id, task_id, workspace_id, event_type, actor_id, correlation_id, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (event_id, task_id, self.workspace_id, str(event.get("kind") or "legacy.event"), actor, _stable("correlation", f"legacy-board-event:{space}:{seq}"), json.dumps(payload, sort_keys=True, ensure_ascii=False), _timestamp(event.get("ts"))))
                self._record_mapping("legacy_board", "event", f"{space}:{seq}", "task_event", event_id)
            else:
                event_id = "event_" + _stable("board-event", f"{self.workspace_id}:{space}:{seq}")[:24]
                self.events.append(DomainEvent(event_id=event_id, event_type=f"legacy.board.{str(event.get('kind') or 'event')}", workspace_id=self.workspace_id, actor_id=actor, aggregate_type="workspace", aggregate_id=self.workspace_id, correlation_id=_stable("correlation", f"legacy-board-event:{space}:{seq}"), payload=payload, occurred_at=_timestamp(event.get("ts"))))
                self._record_mapping("legacy_board", "event", f"{space}:{seq}", "domain_event", event_id)

    def _ensure_dependency(self, task_id: str, depends_on: str) -> None:
        with self.database._lock:
            exists = self.database._conn.execute("SELECT 1 FROM task_dependencies WHERE task_id=? AND depends_on_task_id=?", (task_id, depends_on)).fetchone()
        if exists is None:
            self.tasks.add_dependency(task_id, depends_on)

    def _import_chat(self, base: Path) -> None:
        groups = self._rows(base, "chat.db", "chat_groups")
        group_handles: dict[str, dict[str, str]] = {}
        for group in groups:
            legacy_id = str(group.get("group_id") or "")
            conversation_id = "conversation_" + _stable("chat-group", f"{self.workspace_id}:{legacy_id}")[:24]
            members = _json_value(group.get("members"), [])
            if not isinstance(members, list):
                raise LegacyCutoverError(f"chat group {legacy_id} members must be a list")
            handle_map: dict[str, str] = {}
            agent_ids: list[str] = []
            for member in members:
                if not isinstance(member, Mapping):
                    continue
                handle = str(member.get("name") or "").strip()
                actor, agent_id = self._ensure_agent(handle or "legacy", persona=str(member.get("persona") or handle or "legacy"), role=str(member.get("role") or "worker"))
                handle_map[handle] = actor
                if agent_id:
                    agent_ids.append(agent_id)
            group_handles[legacy_id] = handle_map
            coordinator = agent_ids[0] if agent_ids else self._ensure_agent("legacy-chat", persona="legacy-chat")[1]
            if self.conversations.get_conversation(conversation_id) is None:
                self.conversations.create_conversation(conversation_id, self.workspace_id, ConversationKind.GROUP, self.requester_actor_id, title=str(group.get("name") or "Legacy group")[:160], reply_mode="mention_only", coordinator_agent_id=coordinator)
                self.conversations.add_member(conversation_id, self.requester_actor_id, member_role="owner", history_policy=HistoryPolicy.ALL_HISTORY)
                for actor in handle_map.values():
                    self.conversations.add_member(conversation_id, actor, history_policy=HistoryPolicy.ALL_HISTORY)
            self._record_mapping("legacy_chat", "group", legacy_id, "conversation", conversation_id)
            self._emit_import_event("conversation", conversation_id, "legacy_chat", legacy_id, {"name": group.get("name"), "members": members})
        last_by_group: dict[str, str] = {}
        for message in self._rows(base, "chat.db", "chat_messages"):
            group_id = str(message.get("group_id") or "")
            mapping = self._mapping("legacy_chat", "group", group_id)
            if mapping is None:
                raise LegacyCutoverError(f"chat message references missing group: {group_id}")
            handle_map = group_handles.get(group_id, {})
            author_name = str(message.get("author") or "")
            unknown_author = author_name not in handle_map and author_name not in {"", "user"}
            actor_id = self.requester_actor_id if str(message.get("author_role") or "") == "user" or author_name == "user" else handle_map.get(author_name) or self._ensure_agent(author_name or "legacy-chat")[0]
            if unknown_author:
                self.conversations.add_member(mapping["aggregate_id"], actor_id, history_policy=HistoryPolicy.ALL_HISTORY)
            actor_kind = ActorKind.HUMAN if actor_id == self.requester_actor_id else ActorKind.AGENT
            mentions = [handle_map[item] for item in (_json_value(message.get("mentions"), []) or []) if item in handle_map]
            seq = int(message.get("seq") or 0)
            body = str(message.get("text") or "")
            message_id = "message_" + _stable("chat-message", f"{self.workspace_id}:{group_id}:{seq}")[:24]
            self.conversations.append_message(message_id, mapping["aggregate_id"], ActorRef(actor_id, actor_kind, "Legacy chat"), body, _stable("message-idempotency", f"legacy-chat:{group_id}:{seq}"), _stable("correlation", f"legacy-chat:{group_id}"), mentions=mentions, created_at=_timestamp(message.get("ts")))
            last_by_group[group_id] = message_id
            self._record_mapping("legacy_chat", "message", f"{group_id}:{seq}", "message", message_id)
        for group_id, message_id in last_by_group.items():
            mapping = self._mapping("legacy_chat", "group", group_id)
            if mapping:
                self.conversations.set_read_cursor(mapping["aggregate_id"], self.requester_actor_id, message_id)

    def _import_registry(self, base: Path) -> None:
        path = base / "teams.json"
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        for team in payload.get("teams", []):
            if not isinstance(team, Mapping):
                continue
            legacy_id = str(team.get("team_id") or "")
            if not legacy_id:
                continue
            crew_id = "crew_run_" + _stable("legacy-team", f"{self.workspace_id}:{legacy_id}")[:24]
            agents: list[str] = []
            roles: dict[str, str] = {}
            _, lead_agent = self._ensure_agent(str(team.get("lead_actor") or "lead"), persona="legacy-lead", role="lead")
            if lead_agent:
                agents.append(lead_agent)
                roles[lead_agent] = "supervisor"
            for worker in team.get("workers", []) or []:
                if not isinstance(worker, Mapping):
                    continue
                _, agent_id = self._ensure_agent(str(worker.get("actor") or worker.get("persona") or "worker"), persona=str(worker.get("persona") or "legacy-worker"), model=str(worker.get("model") or ""), role="worker")
                if agent_id and agent_id not in agents:
                    agents.append(agent_id)
                    roles[agent_id] = "worker"
            self._ensure_crew(crew_id, agents, roles)
            self._record_mapping("legacy_registry", "team", legacy_id, "crew_run", crew_id)
            self._emit_import_event("crew_run", crew_id, "legacy_registry", legacy_id, {"space": team.get("space"), "lead_session": team.get("lead_session"), "paused": bool(team.get("paused")), "chat_group": team.get("chat_group")})

    def _ensure_crew(self, crew_id: str, agents: list[str], roles: Mapping[str, str]) -> None:
        with self.database._lock:
            exists = self.database._conn.execute("SELECT 1 FROM crew_runs WHERE crew_run_id=?", (crew_id,)).fetchone()
        if exists is None:
            from .persistence import WorkRepository
            WorkRepository(self.database).create_crew_run(crew_id, self.workspace_id, self.requester_actor_id, _stable("correlation", f"legacy-crew:{crew_id}"), agent_ids=agents, agent_roles=roles)

    def _import_automations(self, base: Path) -> None:
        for row in self._rows(base, "automation.db", "scheduled_tasks"):
            raw = _json_value(row.get("data"), {})
            if not isinstance(raw, Mapping):
                continue
            legacy_id = str(raw.get("id") or row.get("id") or "")
            if not legacy_id:
                continue
            routine_id = "routine_" + _stable("legacy-automation", f"{self.workspace_id}:{legacy_id}")[:24]
            _, agent_id = self._ensure_agent(str(raw.get("agent") or "legacy-automation"), persona=str(raw.get("agent") or "legacy-automation"), model=str(raw.get("model") or ""))
            schedule_raw = raw.get("schedule") if isinstance(raw.get("schedule"), Mapping) else {}
            schedule = {"cron": schedule_raw.get("cron")} if schedule_raw.get("cron") else {"at": schedule_raw.get("fire_at")} if schedule_raw.get("fire_at") else {}
            timezone_name = str(schedule_raw.get("timezone") or "UTC")
            if timezone_name == "local":
                timezone_name = "UTC"
                schedule["legacy_timezone"] = "local"
            instruction = str(raw.get("instructions") or "").strip()
            if len(instruction) < 20:
                instruction = "Imported legacy automation; review before enabling canonical execution."
            if self.routines.get(routine_id) is None:
                self.routines.create(routine_id, self.workspace_id, self.requester_actor_id, name=f"Legacy {legacy_id}: {str(raw.get('title') or legacy_id)[:125]}", trigger_kind="schedule", target_agent_ids=[agent_id], instruction=instruction[:20000], schedule=schedule, timezone_name=timezone_name, budget={}, approval_policy={"legacy_enabled": bool(raw.get("enabled", True))})
                if not bool(raw.get("enabled", True)):
                    self.routines.set_status(routine_id, "paused")
            self._record_mapping("legacy_automations", "task", legacy_id, "automation", routine_id)
            self._emit_import_event("automation", routine_id, "legacy_automations", legacy_id, {"origin_session_id": raw.get("origin_session_id"), "legacy_run_count": raw.get("run_count", 0), "enabled": bool(raw.get("enabled", True))})
        for row in self._rows(base, "automation.db", "task_runs"):
            raw = _json_value(row.get("data"), {})
            legacy_id = str(raw.get("run_id") or row.get("run_id") or "") if isinstance(raw, Mapping) else str(row.get("run_id") or "")
            if legacy_id:
                aggregate_id = "automation_run_" + _stable("legacy-run", legacy_id)[:24]
                self._record_mapping("legacy_automations", "run", legacy_id, "automation_run", aggregate_id)

    def _expected_mapping_keys(self, legacy_base: str | Path) -> list[tuple[str, str, str]]:
        base = self._source_dir(legacy_base)
        keys: list[tuple[str, str, str]] = []
        for row in self._rows(base, "coworker.db", "sessions"):
            if row.get("session_id"):
                keys.append(("legacy_sessions", "session", str(row["session_id"])))
        session_ids = {str(row.get("session_id")) for row in self._rows(base, "coworker.db", "sessions") if row.get("session_id")}
        conversation_dir = base / "conversations"
        if conversation_dir.is_dir():
            keys.extend(("legacy_sessions", "session", path.stem) for path in sorted(conversation_dir.glob("*.jsonl")) if path.stem not in session_ids)
        for row in self._rows(base, "teams.db", "team_items"):
            keys.append(("legacy_board", "item", f"{row.get('space') or 'legacy'}:{int(row.get('id') or 0)}"))
        for row in self._rows(base, "teams.db", "team_events"):
            if row.get("seq") is not None:
                keys.append(("legacy_board", "event", f"{row.get('space') or 'legacy'}:{int(row.get('seq') or 0)}"))
        for row in self._rows(base, "chat.db", "chat_groups"):
            keys.append(("legacy_chat", "group", str(row.get("group_id") or "")))
        for row in self._rows(base, "chat.db", "chat_messages"):
            if row.get("seq") is not None:
                keys.append(("legacy_chat", "message", f"{row.get('group_id') or ''}:{int(row.get('seq') or 0)}"))
        for row in self._rows(base, "automation.db", "scheduled_tasks"):
            raw = _json_value(row.get("data"), {})
            if isinstance(raw, Mapping) and (raw.get("id") or row.get("id")):
                keys.append(("legacy_automations", "task", str(raw.get("id") or row.get("id"))))
        for row in self._rows(base, "automation.db", "task_runs"):
            raw = _json_value(row.get("data"), {})
            run_id = raw.get("run_id") if isinstance(raw, Mapping) else None
            if run_id or row.get("run_id"):
                keys.append(("legacy_automations", "run", str(run_id or row.get("run_id"))))
        path = base / "teams.json"
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            keys.extend(("legacy_registry", "team", str(item.get("team_id"))) for item in value.get("teams", []) if isinstance(item, Mapping) and item.get("team_id"))
        return keys


def _stable(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\x00{value}".encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> str:
    candidate = str(value or "").strip()
    if candidate:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            pass
    return _utc_now()


def _json_value(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _body(message: Mapping[str, Any]) -> str:
    content = message.get("content", message.get("text", ""))
    if isinstance(content, str):
        return content or json.dumps(dict(message), ensure_ascii=False, sort_keys=True)
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def _handle(value: str, digest: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", str(value or "legacy").lower()).strip("-._")[:40] or "legacy"
    return f"{normalized}-{digest[:12]}"[:64]


def _agent_role(value: str) -> AgentRole:
    normalized = str(value or "worker").lower()
    return {
        "lead": AgentRole.SUPERVISOR,
        "supervisor": AgentRole.SUPERVISOR,
        "explorer": AgentRole.EXPLORER,
        "builder": AgentRole.BUILDER,
        "verifier": AgentRole.VERIFIER,
        "operator": AgentRole.OPERATOR,
    }.get(normalized, AgentRole.BUILDER)


def _safe_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in value.items() if not LegacyCutoverService._SECRET_KEYS.search(str(key))}
    if isinstance(value, list):
        return [_safe_json(item) for item in value[:200]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
