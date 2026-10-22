"""Read-only metrics and trace projections for the durable Team pipeline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Optional

from .permissions import redact_sensitive
from .persistence import TeamDatabase


class TeamObservability:
    """Expose bounded, workspace-scoped operational evidence.

    This class deliberately reads durable tables only.  Live WebSocket events
    remain projections and can be rebuilt from the same source of truth.
    """

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def metrics(self, workspace_id: str) -> dict[str, Any]:
        workspace_id = _required(workspace_id, "workspace id")
        connection = self.database._conn
        with self.database._lock:
            runs = _counts(
                connection,
                "SELECT status, COUNT(*) AS count FROM runs WHERE workspace_id = ? GROUP BY status",
                (workspace_id,),
            )
            deliveries = _counts(
                connection,
                """
                SELECT d.status, COUNT(*) AS count
                FROM deliveries AS d JOIN work_requests AS r ON r.request_id = d.request_id
                WHERE r.workspace_id = ? GROUP BY d.status
                """,
                (workspace_id,),
            )
            requests = _counts(
                connection,
                "SELECT status, COUNT(*) AS count FROM work_requests WHERE workspace_id = ? GROUP BY status",
                (workspace_id,),
            )
            approvals = _counts(
                connection,
                "SELECT status, COUNT(*) AS count FROM approvals WHERE workspace_id = ? GROUP BY status",
                (workspace_id,),
            )
            verification = _counts(
                connection,
                "SELECT status, COUNT(*) AS count FROM verification_attempts WHERE workspace_id = ? GROUP BY status",
                (workspace_id,),
            )
            tool_events = _counts(
                connection,
                """
                SELECT re.event_type, COUNT(*) AS count
                FROM run_events AS re JOIN runs AS r ON r.run_id = re.run_id
                WHERE r.workspace_id = ? AND re.event_type LIKE 'tool.execution.%'
                GROUP BY re.event_type
                """,
                (workspace_id,),
            )
            retry_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM delivery_attempts AS da
                JOIN deliveries AS d ON d.delivery_id = da.delivery_id
                JOIN work_requests AS r ON r.request_id = d.request_id
                WHERE r.workspace_id = ? AND da.attempt_number > 1
                """,
                (workspace_id,),
            ).fetchone()["count"]
            queue_latency = connection.execute(
                """
                SELECT AVG((julianday(COALESCE(started_at, created_at)) - julianday(created_at)) * 86400.0) AS seconds
                FROM runs WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()["seconds"]
            approval_wait = connection.execute(
                """
                SELECT AVG((julianday(COALESCE(decided_at, ?)) - julianday(created_at)) * 86400.0) AS seconds
                FROM approvals WHERE workspace_id = ? AND status <> 'pending'
                """,
                (_utc_now(), workspace_id),
            ).fetchone()["seconds"]
            recovery_count = connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM run_events AS re JOIN runs AS r ON r.run_id = re.run_id
                WHERE r.workspace_id = ? AND re.event_type IN ('run.recovered', 'run.reconciled')
                """,
                (workspace_id,),
            ).fetchone()["count"]
        return {
            "workspace_id": workspace_id,
            "generated_at": _utc_now(),
            "requests_by_status": requests,
            "deliveries_by_status": deliveries,
            "runs_by_status": runs,
            "approvals_by_status": approvals,
            "verification_by_status": verification,
            "tool_events_by_type": tool_events,
            "retry_count": int(retry_count or 0),
            "recovery_count": int(recovery_count or 0),
            "average_queue_latency_seconds": _number(queue_latency),
            "average_approval_wait_seconds": _number(approval_wait),
        }

    def trace(self, workspace_id: str, run_id: str, *, limit: int = 200) -> dict[str, Any]:
        workspace_id = _required(workspace_id, "workspace id")
        run_id = _required(run_id, "run id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("trace limit must be an integer between 1 and 200")
        with self.database._lock:
            run = self.database._conn.execute(
                "SELECT * FROM runs WHERE run_id = ? AND workspace_id = ?",
                (run_id, workspace_id),
            ).fetchone()
            if run is None:
                raise LookupError("run not found")
            request = self.database._conn.execute(
                """
                SELECT request_id, source_kind, source_ref_kind, source_ref_id, requester_actor_id,
                       correlation_id, causation_id, conversation_id, task_id,
                       created_at, status, LENGTH(instruction) AS instruction_chars
                FROM work_requests WHERE request_id = ? AND workspace_id = ?
                """,
                (run["request_id"], workspace_id),
            ).fetchone()
            delivery = self.database._conn.execute(
                "SELECT * FROM deliveries WHERE delivery_id = ?", (run["delivery_id"],)
            ).fetchone()
            events = self.database._conn.execute(
                """
                SELECT run_event_id, event_type, actor_id, payload_json, occurred_at
                FROM run_events WHERE run_id = ? ORDER BY occurred_at, run_event_id LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return {
            "workspace_id": workspace_id,
            "run": _safe_run(run),
            "request": _safe_request(request),
            "delivery": dict(delivery) if delivery is not None else None,
            "events": [_safe_event(event) for event in events],
        }


def _counts(connection: Any, query: str, parameters: tuple[Any, ...]) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in connection.execute(query, parameters).fetchall()}


def _safe_run(row: Any) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "request_id": row["request_id"],
        "delivery_id": row["delivery_id"],
        "agent_id": row["agent_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _safe_request(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    source_ref = {"kind": row["source_ref_kind"], "id": row["source_ref_id"]}
    return {
        "request_id": row["request_id"],
        "source_kind": row["source_kind"],
        "source_ref": source_ref,
        "requester_actor_id": row["requester_actor_id"],
        "correlation_id": row["correlation_id"],
        "causation_id": row["causation_id"],
        "conversation_id": row["conversation_id"],
        "task_id": row["task_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "instruction_chars": int(row["instruction_chars"] or 0),
    }


def _safe_event(row: Any) -> dict[str, Any]:
    return {
        "run_event_id": row["run_event_id"],
        "event_type": row["event_type"],
        "actor_id": row["actor_id"],
        "payload": _redact(_json(row["payload_json"], {})),
        "occurred_at": row["occurred_at"],
    }


def _json(value: Any, default: Any) -> Any:
    try:
        decoded = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return decoded


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, Mapping):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _number(value: Any) -> float | None:
    return round(float(value), 3) if value is not None else None


def _required(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
