"""Bounded crew/DAG orchestration on top of canonical WorkRequests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .contracts import ActorKind, ActorRef, ContractError, HandoffContract, ResourceRef
from .coordinator import WorkCoordinator, WorkSubmission
from .persistence import HandoffRepository, WorkRepository


class CrewLimitExceeded(ContractError):
    """The durable crew graph would exceed an explicit graph or budget limit."""


@dataclass(frozen=True)
class ChildResultEnvelope:
    """Typed result passed from a child run back to its parent agent."""

    handoff_id: str
    child_run_id: str | None
    status: str
    summary: str = ""
    artifact_ids: tuple[str, ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.handoff_id or not self.status:
            raise ContractError("child result needs a handoff id and status")
        if self.status not in {"pending", "succeeded", "failed", "timed_out", "canceled"}:
            raise ContractError("unsupported child result status")
        if len(self.summary) > 20_000:
            raise ContractError("child result summary is too large")
        if self.failure_reason is not None and len(self.failure_reason) > 4_000:
            raise ContractError("child result failure reason is too large")
        if len(self.artifact_ids) > 100 or any(not str(item).strip() for item in self.artifact_ids):
            raise ContractError("child result artifact ids are malformed")
        if len(self.evidence) > 100:
            raise ContractError("child result evidence is too large")

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "child_run_id": self.child_run_id,
            "status": self.status,
            "summary": self.summary,
            "artifact_ids": list(self.artifact_ids),
            "evidence": [dict(item) for item in self.evidence],
            "failure_reason": self.failure_reason,
        }


class CrewOrchestrator:
    """Runtime owner for bounded handoff creation, result envelopes, and fan-in."""

    def __init__(
        self,
        coordinator: WorkCoordinator,
        work: WorkRepository,
        handoffs: HandoffRepository,
        *,
        max_depth: int = 8,
        max_handoffs: int = 64,
        max_fanout: int = 16,
    ) -> None:
        if min(max_depth, max_handoffs, max_fanout) <= 0:
            raise ContractError("crew limits must be positive")
        self.coordinator = coordinator
        self.work = work
        self.handoffs = handoffs
        self.max_depth = max_depth
        self.max_handoffs = max_handoffs
        self.max_fanout = max_fanout

    def submit_handoff(
        self,
        handoff: HandoffContract,
        context_by_agent: Mapping[str, Any],
        *,
        parent_run_id: str | None = None,
    ) -> WorkSubmission:
        if not isinstance(handoff, HandoffContract):
            raise ContractError("handoff must be a HandoffContract")
        if handoff.depth > self.max_depth:
            raise CrewLimitExceeded("handoff depth exceeds the crew limit")
        if len(self.handoffs.list_for_task(handoff.task_id, limit=self.max_handoffs + 1)) >= self.max_handoffs:
            raise CrewLimitExceeded("handoff count exceeds the crew limit")
        if parent_run_id is not None:
            parent = self.work.get_run(parent_run_id)
            if parent is None or parent["agent_id"] != handoff.sender_agent_id:
                raise ContractError("handoff sender must be the agent running the parent run")
            request = self.work.get_request(parent["request_id"])
            budget = request.get("budget") if request else {}
            max_handoffs = budget.get("max_handoffs") if isinstance(budget, Mapping) else None
            usage = self.work.get_usage(parent["request_id"])
            if max_handoffs is not None and int(usage.get("handoffs", 0)) >= int(max_handoffs):
                raise CrewLimitExceeded("parent work budget has no handoffs remaining")
            self.work.consume_usage(parent["request_id"], {"handoffs": 1})
        sibling_count = sum(
            1
            for item in self.handoffs.list_for_task(handoff.task_id, limit=self.max_handoffs)
            if item["agent_path"][:-1] == list(handoff.agent_path[:-1])
        )
        if sibling_count >= self.max_fanout:
            raise CrewLimitExceeded("handoff fan-out exceeds the crew limit")
        submission = self.coordinator.submit_handoff(handoff, context_by_agent, causation_id=parent_run_id)
        self.handoffs.transition(handoff.handoff_id, "accepted")
        return submission

    def record_child_result(
        self,
        result: ChildResultEnvelope,
        *,
        child_run_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        if not isinstance(result, ChildResultEnvelope):
            raise ContractError("child result must be a ChildResultEnvelope")
        handoff = self.handoffs.get(result.handoff_id)
        run = self.work.get_run(child_run_id)
        if handoff is None or run is None:
            raise ContractError("child result references a missing handoff or run")
        request = self.work.get_request(run["request_id"])
        if request is None or request["source_ref"]["kind"] != "handoff" or request["source_ref"]["id"] != result.handoff_id:
            raise ContractError("child result run is not the handoff execution")
        if run["agent_id"] != handoff["recipient_agent_id"]:
            raise ContractError("child result agent does not match handoff recipient")
        with self.work.database._lock:
            actor = self.work.database._conn.execute(
                "SELECT actor_id FROM agent_installations WHERE agent_id = ?",
                (run["agent_id"],),
            ).fetchone()
        if actor is None or actor["actor_id"] != actor_id:
            raise ContractError("child result must be recorded by the running child agent")
        payload = result.to_dict()
        event_id = _stable_id("run-event", f"child-result:{result.handoff_id}")
        persisted = self.work.record_run_event(
            event_id,
            child_run_id,
            "handoff.child_result",
            actor_id=actor_id,
            payload=payload,
        )
        terminal = "completed" if result.status == "succeeded" else "failed" if result.status in {"failed", "timed_out", "canceled"} else "accepted"
        self.handoffs.transition(result.handoff_id, terminal)
        return persisted

    def get_child_result(self, handoff_id: str) -> ChildResultEnvelope:
        handoff = self.handoffs.get(handoff_id)
        if handoff is None:
            raise ContractError("handoff not found")
        request_id = _stable_id("request", handoff_id)
        request = self.work.get_request(request_id)
        if request is None:
            return ChildResultEnvelope(handoff_id=handoff_id, child_run_id=None, status="pending")
        runs = self.work.list_runs_for_request(request_id)
        for run in reversed(runs):
            events = self.work.list_run_events(run["run_id"], limit=200)
            for event in reversed(events):
                if event["event_type"] == "handoff.child_result":
                    return ChildResultEnvelope(**event["payload"])
        latest = runs[-1] if runs else None
        status = "pending" if latest is None else _result_status(latest["status"])
        return ChildResultEnvelope(handoff_id=handoff_id, child_run_id=latest["run_id"] if latest else None, status=status)

    def aggregate_task(self, task_id: str) -> dict[str, Any]:
        handoffs = self.handoffs.list_for_task(task_id, limit=self.max_handoffs)
        children = [self.get_child_result(item["handoff_id"]) for item in handoffs]
        children.sort(key=lambda item: item.handoff_id)
        if not children or any(item.status == "pending" for item in children):
            status = "pending"
        elif any(item.status != "succeeded" for item in children):
            status = "failed"
        else:
            status = "succeeded"
        return {
            "task_id": task_id,
            "status": status,
            "children": [item.to_dict() for item in children],
            "failed_handoffs": [item.handoff_id for item in children if item.status != "succeeded"],
        }

    def cancel_graph(self, task_id: str, *, reason: str = "parent task canceled") -> dict[str, Any]:
        request_ids = self.work.list_request_ids_for_scope("task", task_id)
        canceled = []
        for request_id in request_ids:
            canceled.append(self.coordinator.cancel_request(request_id, reason=reason))
        return {"task_id": task_id, "request_ids": request_ids, "canceled": canceled}


def _stable_id(prefix: str, value: str) -> str:
    # Keep this identical to WorkCoordinator's deterministic id format so a
    # result lookup can find the child request without a second source of truth.
    return f"{prefix}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


def _result_status(value: str) -> str:
    return {
        "queued": "pending",
        "running": "pending",
        "waiting_input": "pending",
        "waiting_approval": "pending",
        "succeeded": "succeeded",
        "failed": "failed",
        "timed_out": "timed_out",
        "canceled": "canceled",
    }.get(value, "failed")
