"""Team memory candidates stay scoped, reviewable, and revision-bound."""

from __future__ import annotations

import pytest

from coworker.teams import ContextRepository, ContextScope, MemoryConflict, TeamMemoryService
from test_team_orchestrator import _handoff, _stack


def test_memory_candidate_is_deduplicated_then_explicitly_accepted(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    try:
        orchestrator.submit_handoff(_handoff(), context)
        delivery = coordinator.claim_next("agent_b", "worker_b")
        assert delivery is not None
        coordinator.start_delivery(
            delivery, run_id="run_child", agent_revision_id="revision_b", sandbox_id="sandbox_b",
        )
        service = TeamMemoryService(database, ContextRepository(database), work)
        extractor = lambda _source: [{"content": "Use the stable API", "importance": 90, "conflict_key": "api"}]
        first = service.propose_from_run(
            "run_child", scope=ContextScope.AGENT_PRIVATE, scope_id="agent_b",
            proposed_by_actor_id="actor_owner", extractor=extractor,
        )
        duplicate = service.propose_from_run(
            "run_child", scope=ContextScope.AGENT_PRIVATE, scope_id="agent_b",
            proposed_by_actor_id="actor_owner", extractor=extractor,
        )
        assert first[0]["candidate_id"] == duplicate[0]["candidate_id"]
        memory = service.accept(first[0]["candidate_id"], actor_id="actor_owner")
        assert memory["scope_kind"] == ContextScope.AGENT_PRIVATE.value
        assert service.export_scope("workspace_1", ContextScope.AGENT_PRIVATE, "agent_b")[0]["memory_id"] == memory["memory_id"]
    finally:
        database.close()


def test_conflicting_fact_is_not_silently_overwritten(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    try:
        orchestrator.submit_handoff(_handoff(), context)
        delivery = coordinator.claim_next("agent_b", "worker_b")
        assert delivery is not None
        coordinator.start_delivery(
            delivery, run_id="run_child", agent_revision_id="revision_b", sandbox_id="sandbox_b",
        )
        service = TeamMemoryService(database, ContextRepository(database), work)
        first = service.propose_from_run(
            "run_child", scope="workspace", scope_id="workspace_1", proposed_by_actor_id="actor_owner",
            extractor=lambda _source: [{"content": "Use v1", "conflict_key": "api"}],
        )[0]
        service.accept(first["candidate_id"], actor_id="actor_owner")
        second = service.propose_from_run(
            "run_child", scope="workspace", scope_id="workspace_1", proposed_by_actor_id="actor_owner",
            extractor=lambda _source: [{"content": "Use v2", "conflict_key": "api"}],
        )[0]
        with pytest.raises(MemoryConflict):
            service.accept(second["candidate_id"], actor_id="actor_owner")
    finally:
        database.close()
