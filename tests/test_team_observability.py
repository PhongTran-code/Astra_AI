"""Read-only Team metrics and trace projections."""

from test_team_orchestrator import _handoff, _stack

from coworker.teams import TeamObservability


def test_metrics_and_trace_are_workspace_scoped_and_redacted(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    try:
        orchestrator.submit_handoff(_handoff(), context)
        lease = coordinator.claim_next("agent_b", "worker_b")
        assert lease is not None
        coordinator.start_delivery(
            lease,
            run_id="run_trace",
            agent_revision_id="revision_b",
            sandbox_id="sandbox_b",
        )
        work.record_run_event(
            "event_secret",
            "run_trace",
            "tool.execution.finished",
            actor_id="actor_b",
            payload={"status": "failed", "error": "api_token=abcdefghijklmnopqrstuvwxyz1234"},
        )
        observability = TeamObservability(database)
        metrics = observability.metrics("workspace_1")
        assert metrics["runs_by_status"]["running"] == 1
        assert metrics["tool_events_by_type"]["tool.execution.finished"] == 1
        trace = observability.trace("workspace_1", "run_trace")
        assert trace["request"]["request_id"] == lease["request_id"]
        tool_event = next(
            event for event in trace["events"] if event["event_type"] == "tool.execution.finished"
        )
        assert tool_event["payload"]["error"] == "[REDACTED]"
        assert "instruction" not in trace["request"]
    finally:
        database.close()
