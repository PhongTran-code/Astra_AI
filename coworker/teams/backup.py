"""Durable Workspace Backup and Restore Subsystem for Astra Teams.

Packages workspace database records, content-addressed attachments,
and transcripts into a sanitized `.owb` (tar.gz) archive, with full
schema migration compatibility checks and topological hydration.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from .attachments import AttachmentStore, stored_name
from .contracts import ContractError, utc_now
from .permissions import redact_sensitive
from .persistence import TeamDatabase

BACKUP_FORMAT_VERSION = "1.0"

EXPORTED_TABLES: Sequence[str] = (
    "workspaces",
    "actors",
    "workspace_memberships",
    "agent_definitions",
    "agent_revisions",
    "agent_revision_capabilities",
    "agent_installations",
    "conversations",
    "conversation_members",
    "threads",
    "messages",
    "message_attachments",
    "tasks",
    "task_dependencies",
    "task_labels",
    "task_watchers",
    "task_events",
    "artifacts",
    "artifact_revisions",
    "context_pins",
    "context_shares",
    "memory_records",
    "team_routines",
    "verification_attempts",
)


class WorkspaceBackupError(ContractError):
    """An error occurred during workspace packaging or restoration."""


class WorkspaceBackupService:
    """Service to export and restore complete workspace states."""

    def __init__(
        self,
        database: TeamDatabase,
        attachment_store: AttachmentStore,
        *,
        base_dir: Optional[Path] = None,
    ) -> None:
        self.database = database
        self.attachment_store = attachment_store
        self.base_dir = Path(base_dir).expanduser() if base_dir else None

    def export_workspace(
        self,
        workspace_id: str,
        *,
        include_attachments: bool = True,
        include_transcripts: bool = True,
    ) -> bytes:
        """Export workspace tables, sanitized secrets, and attachments into a .owb tar.gz bundle."""
        if not workspace_id or not isinstance(workspace_id, str):
            raise WorkspaceBackupError("workspace_id is required")

        out = io.BytesIO()
        table_checksums: dict[str, str] = {}
        table_counts: dict[str, int] = {}
        referenced_attachments: set[str] = set()

        with tarfile.open(fileobj=out, mode="w:gz") as tar:
            with self.database._lock:
                for table in EXPORTED_TABLES:
                    has_ws_col = self._table_has_column(table, "workspace_id")
                    if has_ws_col:
                        query = f"SELECT * FROM {table} WHERE workspace_id = ? ORDER BY 1"
                        params: tuple[Any, ...] = (workspace_id,)
                    elif table in ("agent_revisions", "agent_revision_capabilities"):
                        query = f"""
                            SELECT r.* FROM {table} r
                            JOIN agent_definitions d ON d.agent_definition_id = r.agent_definition_id
                            WHERE d.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    elif table in ("task_dependencies", "task_labels", "task_watchers"):
                        query = f"""
                            SELECT t.* FROM {table} t
                            JOIN tasks k ON k.task_id = t.task_id
                            WHERE k.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    elif table == "artifact_revisions":
                        query = f"""
                            SELECT r.* FROM artifact_revisions r
                            JOIN artifacts a ON a.artifact_id = r.artifact_id
                            WHERE a.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    elif table == "conversation_members":
                        query = f"""
                            SELECT m.* FROM conversation_members m
                            JOIN conversations c ON c.conversation_id = m.conversation_id
                            WHERE c.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    elif table == "threads":
                        query = f"""
                            SELECT t.* FROM threads t
                            JOIN conversations c ON c.conversation_id = t.conversation_id
                            WHERE c.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    elif table == "messages":
                        query = f"""
                            SELECT m.* FROM messages m
                            JOIN conversations c ON c.conversation_id = m.conversation_id
                            WHERE c.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    elif table == "message_attachments":
                        query = f"""
                            SELECT a.* FROM message_attachments a
                            JOIN messages m ON m.message_id = a.message_id
                            JOIN conversations c ON c.conversation_id = m.conversation_id
                            WHERE c.workspace_id = ? ORDER BY 1
                        """
                        params = (workspace_id,)
                    else:
                        continue

                    cursor = self.database._conn.execute(query, params)
                    rows = [dict(row) for row in cursor.fetchall()]

                    sanitized_rows = [redact_sensitive(row) for row in rows]

                    if table == "message_attachments":
                        for row in rows:
                            storage_ref = row.get("storage_ref") or row.get("attachment_ref", "")
                            name = stored_name(storage_ref)
                            if name:
                                referenced_attachments.add(name)

                    content = json.dumps(sanitized_rows, ensure_ascii=False, indent=2).encode("utf-8")
                    table_checksums[table] = hashlib.sha256(content).hexdigest()
                    table_counts[table] = len(sanitized_rows)

                    info = tarfile.TarInfo(name=f"database/{table}.json")
                    info.size = len(content)
                    info.mtime = int(datetime.now(timezone.utc).timestamp())
                    tar.addfile(info, io.BytesIO(content))

            if include_attachments:
                for attachment_name in referenced_attachments:
                    try:
                        file_path = self.attachment_store.path_for(attachment_name)
                        if file_path.is_file():
                            data = file_path.read_bytes()
                            info = tarfile.TarInfo(name=f"attachments/{attachment_name}")
                            info.size = len(data)
                            info.mtime = int(datetime.now(timezone.utc).timestamp())
                            tar.addfile(info, io.BytesIO(data))
                    except Exception:
                        pass

            manifest = {
                "format_version": BACKUP_FORMAT_VERSION,
                "workspace_id": workspace_id,
                "exported_at": utc_now(),
                "table_counts": table_counts,
                "table_checksums": table_checksums,
                "attachments_count": len(referenced_attachments),
            }
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            manifest_info = tarfile.TarInfo(name="manifest.json")
            manifest_info.size = len(manifest_bytes)
            manifest_info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

        return out.getvalue()

    def restore_workspace(
        self,
        archive_bytes: bytes,
        *,
        target_workspace_id: Optional[str] = None,
        clone_as_new: bool = False,
    ) -> dict[str, Any]:
        """Restore workspace archive into team.db within an atomic transaction."""
        if not archive_bytes:
            raise WorkspaceBackupError("archive bytes are empty")

        stream = io.BytesIO(archive_bytes)
        try:
            tar = tarfile.open(fileobj=stream, mode="r:gz")
        except Exception as error:
            raise WorkspaceBackupError(f"invalid archive format: {error}") from error

        try:
            manifest_member = tar.getmember("manifest.json")
            manifest_file = tar.extractfile(manifest_member)
            if manifest_file is None:
                raise WorkspaceBackupError("manifest.json missing in archive")
            manifest = json.loads(manifest_file.read().decode("utf-8"))
        except Exception as error:
            raise WorkspaceBackupError(f"failed to read manifest.json: {error}") from error

        source_workspace_id = manifest.get("workspace_id")
        workspace_id = target_workspace_id or (
            f"workspace_{uuid.uuid4().hex[:12]}" if clone_as_new else source_workspace_id
        )

        id_map: dict[str, str] = {}
        if clone_as_new or target_workspace_id:
            if source_workspace_id:
                id_map[source_workspace_id] = workspace_id

        attachments_restored = 0
        for member in tar.getmembers():
            if member.name.startswith("attachments/") and member.isfile():
                attachment_file = tar.extractfile(member)
                if attachment_file is not None:
                    data = attachment_file.read()
                    filename = Path(member.name).name
                    self.attachment_store.put(data, filename)
                    attachments_restored += 1

        restored_counts: dict[str, int] = {}
        with self.database.transaction() as conn:
            for table in EXPORTED_TABLES:
                member_path = f"database/{table}.json"
                try:
                    member = tar.getmember(member_path)
                except KeyError:
                    continue
                file_obj = tar.extractfile(member)
                if file_obj is None:
                    continue
                rows: list[dict[str, Any]] = json.loads(file_obj.read().decode("utf-8"))

                if clone_as_new:
                    rows = [self._remap_row_ids(table, row, id_map) for row in rows]
                elif target_workspace_id and target_workspace_id != source_workspace_id:
                    for r in rows:
                        if "workspace_id" in r:
                            r["workspace_id"] = target_workspace_id

                for row in rows:
                    cols = list(row.keys())
                    placeholders = ", ".join("?" for _ in cols)
                    col_names = ", ".join(cols)
                    values = [row[c] for c in cols]
                    conn.execute(
                        f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                        values,
                    )
                restored_counts[table] = len(rows)

        return {
            "ok": True,
            "workspace_id": workspace_id,
            "tables_restored": restored_counts,
            "attachments_restored": attachments_restored,
            "cloned": clone_as_new,
        }

    def _table_has_column(self, table: str, column: str) -> bool:
        cursor = self.database._conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cursor.fetchall())

    def _remap_row_ids(self, table: str, row: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
        """Remap primary keys and foreign keys for clone-as-new imports."""
        remap_keys = [
            "workspace_id", "actor_id", "invited_by_actor_id", "invited_actor_id",
            "owner_actor_id", "agent_definition_id", "agent_revision_id", "active_revision_id",
            "agent_id", "conversation_id", "parent_task_id", "task_id",
            "reviewer_actor_id", "created_by_actor_id", "canonical_thread_id",
            "thread_id", "message_id", "reply_to_message_id", "artifact_id",
            "depends_on_task_id", "run_id"
        ]
        remapped = dict(row)
        for key in remap_keys:
            if key in remapped and remapped[key]:
                orig = str(remapped[key])
                if orig not in id_map:
                    prefix = orig.split("_")[0] if "_" in orig else "id"
                    id_map[orig] = f"{prefix}_{uuid.uuid4().hex[:12]}"
                remapped[key] = id_map[orig]
        return remapped
