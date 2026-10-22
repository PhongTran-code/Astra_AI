"""First-class Team routines that adapt scheduler/connector triggers to WorkRequest."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import ActorKind, ActorRef, ContractError, WorkBudget
from .coordinator import WorkCoordinator, WorkSubmission
from .persistence import PersistenceConflictError, TeamDatabase


class RoutineError(ContractError):
    """A routine is invalid, paused, or already claimed for the occurrence."""


@dataclass(frozen=True)
class RoutineTriggerResult:
    occurrence: dict[str, Any]
    submission: WorkSubmission | None
    replayed: bool = False


class RoutineRepository:
    """Durable routine definitions and occurrence idempotency claims."""

    def __init__(self, database: TeamDatabase) -> None:
        self.database = database

    def create(
        self,
        routine_id: str,
        workspace_id: str,
        created_by_actor_id: str,
        *,
        name: str,
        trigger_kind: str,
        target_agent_ids: Sequence[str],
        instruction: str,
        schedule: Mapping[str, Any] | None = None,
        timezone_name: str = "UTC",
        conversation_id: str | None = None,
        task_id: str | None = None,
        expected_output: str = "",
        budget: Mapping[str, Any] | None = None,
        approval_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        routine_id = _id(routine_id, "routine id")
        workspace_id = _id(workspace_id, "routine workspace id")
        created_by_actor_id = _id(created_by_actor_id, "routine creator id")
        name = _bounded(name, "routine name", 160)
        trigger_kind = str(trigger_kind or "").strip().lower()
        if trigger_kind not in {"schedule", "connector", "manual"}:
            raise RoutineError("routine trigger kind is unsupported")
        agents = tuple(dict.fromkeys(_id(item, "routine agent id") for item in target_agent_ids))
        if not agents or len(agents) > 16:
            raise RoutineError("routine must target between 1 and 16 agents")
        instruction = _bounded(instruction, "routine instruction", 20_000)
        expected_output = str(expected_output or "").strip()[:4_000]
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise RoutineError("routine timezone is unknown") from error
        schedule = _json_object(schedule or {}, "routine schedule")
        budget = _json_object(budget or {}, "routine budget")
        approval_policy = _json_object(approval_policy or {}, "routine approval policy")
        now = _utc_now()
        with self.database.transaction() as connection:
            _require_member(connection, workspace_id, created_by_actor_id)
            for agent_id in agents:
                agent = connection.execute("SELECT workspace_id FROM agent_installations WHERE agent_id = ?", (agent_id,)).fetchone()
                if agent is None or agent["workspace_id"] != workspace_id:
                    raise RoutineError("routine target agent is missing or cross-workspace")
            for table, key, label in (("conversations", conversation_id, "conversation"), ("tasks", task_id, "task")):
                if key is not None:
                    row = connection.execute(f"SELECT workspace_id FROM {table} WHERE {('conversation_id' if table == 'conversations' else 'task_id')} = ?", (key,)).fetchone()
                    if row is None or row["workspace_id"] != workspace_id:
                        raise RoutineError(f"routine {label} is missing or cross-workspace")
            try:
                connection.execute(
                    """
                    INSERT INTO team_routines
                        (routine_id, workspace_id, created_by_actor_id, name, trigger_kind,
                         schedule_json, timezone, target_agent_ids_json, conversation_id,
                         task_id, instruction, expected_output, budget_json,
                         approval_policy_json, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (routine_id, workspace_id, created_by_actor_id, name, trigger_kind, _json(schedule), timezone_name,
                     _json(list(agents)), conversation_id, task_id, instruction, expected_output, _json(budget),
                     _json(approval_policy), now, now),
                )
            except sqlite3.IntegrityError as error:
                raise PersistenceConflictError("routine name or id already exists") from error
            row = connection.execute("SELECT * FROM team_routines WHERE routine_id = ?", (routine_id,)).fetchone()
        assert row is not None
        return _routine_row(row)

    def get(self, routine_id: str) -> dict[str, Any] | None:
        routine_id = _id(routine_id, "routine id")
        with self.database._lock:
            row = self.database._conn.execute("SELECT * FROM team_routines WHERE routine_id = ?", (routine_id,)).fetchone()
        return _routine_row(row) if row is not None else None

    def list_for_workspace(
        self,
        workspace_id: str,
        *,
        status: str | None = "active",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        workspace_id = _id(workspace_id, "routine workspace id")
        if status is not None and status not in {"active", "paused", "archived"}:
            raise RoutineError("routine status is unsupported")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise RoutineError("routine limit must be between 1 and 500")
        with self.database._lock:
            if status is None:
                rows = self.database._conn.execute(
                    "SELECT * FROM team_routines WHERE workspace_id = ? ORDER BY updated_at DESC, routine_id LIMIT ?",
                    (workspace_id, limit),
                ).fetchall()
            else:
                rows = self.database._conn.execute(
                    "SELECT * FROM team_routines WHERE workspace_id = ? AND status = ? ORDER BY updated_at DESC, routine_id LIMIT ?",
                    (workspace_id, status, limit),
                ).fetchall()
        return [_routine_row(row) for row in rows]

    def set_status(self, routine_id: str, status: str) -> dict[str, Any]:
        routine_id = _id(routine_id, "routine id")
        if status not in {"active", "paused", "archived"}:
            raise RoutineError("routine status is unsupported")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT status FROM team_routines WHERE routine_id = ?", (routine_id,)).fetchone()
            if row is None:
                raise RoutineError("routine not found")
            if row["status"] == "archived" and status != "archived":
                raise RoutineError("archived routine cannot be resumed")
            connection.execute("UPDATE team_routines SET status = ?, updated_at = ? WHERE routine_id = ?", (status, _utc_now(), routine_id))
            updated = connection.execute("SELECT * FROM team_routines WHERE routine_id = ?", (routine_id,)).fetchone()
        assert updated is not None
        return _routine_row(updated)

    def claim_occurrence(self, routine_id: str, workspace_id: str, occurrence_key: str, trigger_kind: str) -> tuple[dict[str, Any], bool]:
        routine_id = _id(routine_id, "routine id")
        workspace_id = _id(workspace_id, "routine occurrence workspace id")
        occurrence_key = _bounded(occurrence_key, "routine occurrence key", 300)
        trigger_kind = _bounded(trigger_kind, "routine occurrence trigger", 40)
        occurrence_id = _stable_id("occurrence", f"{routine_id}:{occurrence_key}")
        now = _utc_now()
        with self.database.transaction() as connection:
            routine = connection.execute("SELECT workspace_id, status FROM team_routines WHERE routine_id = ?", (routine_id,)).fetchone()
            if routine is None or routine["workspace_id"] != workspace_id:
                raise RoutineError("routine not found in workspace")
            existing = connection.execute("SELECT * FROM routine_occurrences WHERE routine_id = ? AND occurrence_key = ?", (routine_id, occurrence_key)).fetchone()
            if existing is not None:
                return dict(existing), True
            connection.execute(
                "INSERT INTO routine_occurrences (occurrence_id, routine_id, workspace_id, occurrence_key, trigger_kind, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'claimed', ?, ?)",
                (occurrence_id, routine_id, workspace_id, occurrence_key, trigger_kind, now, now),
            )
            row = connection.execute("SELECT * FROM routine_occurrences WHERE occurrence_id = ?", (occurrence_id,)).fetchone()
        assert row is not None
        return dict(row), False

    def finish_occurrence(self, occurrence_id: str, status: str, *, work_request_id: str | None = None) -> dict[str, Any]:
        occurrence_id = _id(occurrence_id, "occurrence id")
        if status not in {"queued", "failed", "completed"}:
            raise RoutineError("occurrence status is unsupported")
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM routine_occurrences WHERE occurrence_id = ?", (occurrence_id,)).fetchone()
            if row is None:
                raise RoutineError("occurrence not found")
            if row["status"] in {"completed", "failed"}:
                return dict(row)
            connection.execute("UPDATE routine_occurrences SET status = ?, work_request_id = COALESCE(?, work_request_id), updated_at = ? WHERE occurrence_id = ?", (status, work_request_id, _utc_now(), occurrence_id))
            updated = connection.execute("SELECT * FROM routine_occurrences WHERE occurrence_id = ?", (occurrence_id,)).fetchone()
        assert updated is not None
        return dict(updated)


class RoutineService:
    """Turn a schedule/connector occurrence into the same canonical automation WorkRequest."""

    def __init__(self, repository: RoutineRepository, coordinator: WorkCoordinator, *, workspace_id: str, actor_id: str) -> None:
        self.repository = repository
        self.coordinator = coordinator
        self.workspace_id = _id(workspace_id, "routine service workspace id")
        self.actor_id = _id(actor_id, "routine service actor id")

    def trigger(
        self,
        routine_id: str,
        *,
        occurrence_key: str,
        trigger_kind: str,
        context_by_agent: Mapping[str, Any],
        instruction_suffix: str = "",
    ) -> RoutineTriggerResult:
        routine = self.repository.get(routine_id)
        if routine is None or routine["workspace_id"] != self.workspace_id:
            raise RoutineError("routine not found")
        if routine["status"] != "active" and trigger_kind != "manual":
            raise RoutineError("paused routine cannot fire a scheduled occurrence")
        occurrence, replayed = self.repository.claim_occurrence(routine_id, self.workspace_id, occurrence_key, trigger_kind)
        if replayed:
            work_request_id = str(occurrence.get("work_request_id") or "").strip()
            if work_request_id:
                request = self.coordinator.work.get_request(work_request_id)
                if request is not None:
                    return RoutineTriggerResult(
                        occurrence=occurrence,
                        submission=WorkSubmission(
                            request=request,
                            deliveries=tuple(
                                self.coordinator.work.list_deliveries_for_request(work_request_id)
                            ),
                        ),
                        replayed=True,
                    )
            if occurrence.get("status") in {"failed", "completed"}:
                return RoutineTriggerResult(occurrence=occurrence, submission=None, replayed=True)
        agents = tuple(routine["target_agent_ids"])
        instruction = routine["instruction"]
        if instruction_suffix:
            instruction += "\n\nTrigger context:\n" + str(instruction_suffix)[:4_000]
        request_id = _stable_id("request", f"routine:{routine_id}:{occurrence_key}")
        try:
            submission = self.coordinator.submit_automation(
                request_id=request_id,
                workspace_id=self.workspace_id,
                automation_id=routine_id,
                requester=ActorRef(self.actor_id, ActorKind.HUMAN, "Routine owner"),
                target_agent_ids=agents,
                instruction=instruction,
                idempotency_key=_stable_id("routine-occurrence", f"{routine_id}:{occurrence_key}"),
                correlation_id=_stable_id("correlation", f"{routine_id}:{occurrence_key}"),
                context_by_agent=context_by_agent,
                budget=WorkBudget.from_dict(routine["budget"]),
                conversation_id=routine.get("conversation_id"),
                task_id=routine.get("task_id"),
                permission_scope={"routine_id": routine_id, "occurrence_id": occurrence["occurrence_id"], "approval_policy": routine["approval_policy"]},
                expected_output=routine.get("expected_output") or "",
            )
        except Exception:
            self.repository.finish_occurrence(occurrence["occurrence_id"], "failed")
            raise
        updated = self.repository.finish_occurrence(occurrence["occurrence_id"], "queued", work_request_id=submission.request["request_id"] if submission.request else request_id)
        return RoutineTriggerResult(occurrence=updated, submission=submission)


def scheduled_occurrence_key(
    routine: Mapping[str, Any], *, now: datetime | None = None
) -> str | None:
    """Return a stable key when an active schedule is due at ``now``.

    The scheduler intentionally supports a small, deterministic contract rather
    than importing a second cron engine.  Unsupported expressions are skipped;
    they never become an implicit every-minute trigger.
    """

    if str(routine.get("trigger_kind") or "") != "schedule":
        return None
    schedule = routine.get("schedule") or {}
    if not isinstance(schedule, Mapping):
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    try:
        local = current.astimezone(ZoneInfo(str(routine.get("timezone") or "UTC")))
    except (ZoneInfoNotFoundError, ValueError):
        return None

    interval = _positive_schedule_int(
        schedule.get("interval_seconds", schedule.get("every_seconds"))
    )
    if interval is None:
        every_minutes = _positive_schedule_int(schedule.get("every_minutes"))
        interval = every_minutes * 60 if every_minutes is not None else None
    if interval is not None:
        # UTC slots make a restart on a different local timezone harmless and
        # keep the claim key independent of wall-clock DST transitions.
        slot = int(current.timestamp()) // interval
        return f"interval:{interval}:{slot}"

    cron = schedule.get("cron")
    if isinstance(cron, str) and _cron_due(cron, local):
        return f"cron:{local.strftime('%Y-%m-%dT%H:%M:%S%z')[:16]}"

    hour = schedule.get("hour")
    minute = schedule.get("minute")
    at = schedule.get("at")
    if isinstance(at, str) and ":" in at:
        raw_hour, raw_minute = at.split(":", 1)
        hour, minute = raw_hour, raw_minute
    if _clock_part(hour, 0, 23) == local.hour and _clock_part(minute, 0, 59) == local.minute:
        return f"daily:{local.date().isoformat()}:{local.hour:02d}:{local.minute:02d}"
    return None


def _routine_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in ("schedule_json", "budget_json", "approval_policy_json", "target_agent_ids_json"):
        output_key = key.removesuffix("_json")
        result[output_key] = json.loads(result.pop(key) or ("[]" if key == "target_agent_ids_json" else "{}"))
    return result


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RoutineError(f"{label} must be an object")
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 128 or any(char in value for char in "\x00\r\n"):
        raise RoutineError(f"{label} is malformed")
    return value


def _bounded(value: Any, label: str, limit: int) -> str:
    value = str(value or "").strip()
    if not value or len(value) > limit:
        raise RoutineError(f"{label} is required and bounded")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def _require_member(connection: Any, workspace_id: str, actor_id: str) -> None:
    row = connection.execute("SELECT 1 FROM workspace_memberships WHERE workspace_id = ? AND actor_id = ? AND status = 'active'", (workspace_id, actor_id)).fetchone()
    if row is None:
        raise RoutineError("actor is not an active workspace member")


def _positive_schedule_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    # A single occurrence may not claim an absurdly long-lived interval that
    # makes schedule configuration effectively impossible to reason about.
    return value if value <= 31 * 24 * 60 * 60 else None


def _clock_part(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _cron_due(expression: str, local: datetime) -> bool:
    fields = expression.split()
    if len(fields) != 5:
        return False
    values = (local.minute, local.hour, local.day, local.month, (local.weekday() + 1) % 7)
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
    return all(_cron_field_matches(field, value, bounds) for field, value, bounds in zip(fields, values, ranges))


def _cron_field_matches(field: str, value: int, bounds: tuple[int, int]) -> bool:
    field = field.strip()
    if not field or any(char not in "0123456789*/, -" for char in field):
        return False
    for item in field.split(","):
        item = item.strip()
        if not item:
            return False
        base, slash, step_text = item.partition("/")
        if slash:
            try:
                step = int(step_text)
            except ValueError:
                return False
            if step <= 0:
                return False
        else:
            step = 1
        if base == "*":
            start, end = bounds
        elif "-" in base:
            left, right = base.split("-", 1)
            try:
                start, end = int(left), int(right)
            except ValueError:
                return False
        else:
            try:
                start = end = int(base)
            except ValueError:
                return False
        if start < bounds[0] or end > bounds[1] or start > end:
            return False
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False
