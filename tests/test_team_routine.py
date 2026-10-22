"""Routine occurrence idempotency and canonical WorkRequest dispatch."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from coworker.teams import (
    RoutineError,
    RoutineRepository,
    RoutineService,
    scheduled_occurrence_key,
)
from test_team_orchestrator import _stack


def test_routine_trigger_creates_one_canonical_request_per_occurrence(tmp_path):
    database, _work, _coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    try:
        repository = RoutineRepository(database)
        repository.create(
            "routine_daily", "workspace_1", "actor_owner", name="Daily review",
            trigger_kind="schedule", target_agent_ids=("agent_b",),
            instruction="Review the latest changes", schedule={"cron": "0 9 * * *"},
        )
        service = RoutineService(
            repository, _coordinator, workspace_id="workspace_1", actor_id="actor_owner",
        )
        first = service.trigger(
            "routine_daily", occurrence_key="2026-09-01T09:00:00+00:00", trigger_kind="schedule",
            context_by_agent=context,
        )
        replay = service.trigger(
            "routine_daily", occurrence_key="2026-09-01T09:00:00+00:00", trigger_kind="schedule",
            context_by_agent=context,
        )
        assert first.replayed is False
        assert replay.replayed is True
        assert replay.occurrence["occurrence_id"] == first.occurrence["occurrence_id"]
        assert first.submission is not None and first.submission.request is not None
        assert first.submission.request["source_kind"] == "automation"
    finally:
        database.close()


def test_routine_retries_a_claimed_occurrence_after_crash_without_duplicate_request(tmp_path):
    database, work, coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    try:
        repository = RoutineRepository(database)
        repository.create(
            "routine_recover", "workspace_1", "actor_owner", name="Recovery review",
            trigger_kind="schedule", target_agent_ids=("agent_b",),
            instruction="Review after a worker restart", schedule={"every_minutes": 5},
        )
        claimed, replayed = repository.claim_occurrence(
            "routine_recover", "workspace_1", "crash-slot", "schedule"
        )
        assert replayed is False and claimed["work_request_id"] is None
        service = RoutineService(
            repository, coordinator, workspace_id="workspace_1", actor_id="actor_owner",
        )
        recovered = service.trigger(
            "routine_recover", occurrence_key="crash-slot", trigger_kind="schedule",
            context_by_agent=context,
        )
        replay = service.trigger(
            "routine_recover", occurrence_key="crash-slot", trigger_kind="schedule",
            context_by_agent=context,
        )
        assert recovered.submission is not None
        assert replay.replayed is True and replay.submission is not None
        assert replay.submission.request["request_id"] == recovered.submission.request["request_id"]
        assert len(work.list_deliveries_for_request(recovered.submission.request["request_id"])) == 1
    finally:
        database.close()


def test_paused_routine_rejects_scheduled_trigger_but_allows_explicit_manual_trigger(tmp_path):
    database, _work, _coordinator, _orchestrator, _tasks, context = _stack(tmp_path)
    try:
        repository = RoutineRepository(database)
        repository.create(
            "routine_manual", "workspace_1", "actor_owner", name="Manual review",
            trigger_kind="manual", target_agent_ids=("agent_b",), instruction="Review",
        )
        repository.set_status("routine_manual", "paused")
        service = RoutineService(repository, _coordinator, workspace_id="workspace_1", actor_id="actor_owner")
        with pytest.raises(RoutineError, match="paused"):
            service.trigger("routine_manual", occurrence_key="schedule-1", trigger_kind="schedule", context_by_agent=context)
        result = service.trigger("routine_manual", occurrence_key="manual-1", trigger_kind="manual", context_by_agent=context)
        assert result.submission is not None
    finally:
        database.close()


def test_scheduled_occurrence_key_is_restart_safe_and_does_not_guess_cron(tmp_path):
    now = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    routine = {
        "trigger_kind": "schedule",
        "timezone": "UTC",
        "schedule": {"interval_seconds": 60},
    }
    assert scheduled_occurrence_key(routine, now=now) == scheduled_occurrence_key(
        routine, now=now
    )
    assert scheduled_occurrence_key(
        routine, now=now.replace(second=59)
    ) == scheduled_occurrence_key(routine, now=now)
    assert scheduled_occurrence_key(
        routine, now=now.replace(minute=1)
    ) != scheduled_occurrence_key(routine, now=now)
    unsupported = {
        "trigger_kind": "schedule",
        "timezone": "UTC",
        "schedule": {"cron": "not-a-cron"},
    }
    assert scheduled_occurrence_key(unsupported, now=now) is None
