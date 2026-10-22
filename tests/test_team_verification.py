"""Acceptance contracts cannot be self-approved and persist safe evidence."""

from __future__ import annotations

import pytest

from coworker.teams import (
    AcceptanceContract,
    VerificationError,
    VerificationRepository,
    VerificationService,
)
from test_team_orchestrator import AGENT_B, _handoff, _stack


def _run(tmp_path):
    database, work, coordinator, orchestrator, _tasks, context = _stack(tmp_path)
    orchestrator.submit_handoff(_handoff(), context)
    delivery = coordinator.claim_next("agent_b", "worker_b")
    assert delivery is not None
    coordinator.start_delivery(
        delivery, run_id="run_child", agent_revision_id="revision_b", sandbox_id="sandbox_b",
    )
    return database, work, "run_child"


def test_missing_evidence_is_a_failed_durable_attempt(tmp_path):
    database, work, run_id = _run(tmp_path)
    try:
        service = VerificationService(work, VerificationRepository(database))
        contract = AcceptanceContract(
            "contract_1", "coding", ({"kind": "test", "check_id": "unit"},),
        )
        attempt = service.verify(
            contract, run_id=run_id, verifier_actor_id="actor_owner", checkers={},
        )
        assert attempt.status == "failed"
        assert service.repository.list_for_run(run_id)[0]["status"] == "failed"
        with pytest.raises(VerificationError, match="not passed"):
            service.require_verified(contract, run_id)
    finally:
        database.close()


def test_independent_checker_passes_and_builder_cannot_verify(tmp_path):
    database, work, run_id = _run(tmp_path)
    try:
        service = VerificationService(work, VerificationRepository(database))
        contract = AcceptanceContract(
            "contract_2", "coding", ({"kind": "test", "check_id": "unit"},),
        )
        with pytest.raises(VerificationError, match="builder"):
            service.verify(
                contract, run_id=run_id, verifier_actor_id=AGENT_B.id,
                checkers={"test": lambda _check: True},
            )
        attempt = service.verify(
            contract, run_id=run_id, verifier_actor_id="actor_owner",
            checkers={"test": lambda check: {"passed": True, "evidence_ref": check["check_id"]}},
        )
        assert attempt.status == "passed"
        service.require_verified(contract, run_id)
        assert service.repository.latest_passed(run_id, "contract_2")["contract"]["contract_id"] == "contract_2"
    finally:
        database.close()
