"""Durable Astra Team work coordination.

The coordinator is the narrow orchestration boundary between conversation/task
events and the persistent work queue.  It does not run a model or execute tools;
workers claim a delivery, start a sandbox-backed run, and settle that run here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .contracts import (
    ActorKind,
    ActorRef,
    ContractError,
    ConversationKind,
    DeliveryStatus,
    DomainEvent,
    HandoffContract,
    ResourceRef,
    RunStatus,
    WorkBudget,
    WorkRequest,
    WorkSource,
    utc_now,
)
from .execution_events import AgentExecutionEvent
from .permissions import PermissionContext, PermissionDecision, TeamPermissionService, reject_prompt_policy_mutation
from .persistence import (
    BudgetExceededError,
    ConversationRepository,
    EventRepository,
    HandoffRepository,
    PersistenceConflictError,
    TaskRepository,
    WorkRepository,
)
from .routing import ConversationRoutePolicy, RoutingDecision, route_message


class CoordinatorError(ContractError):
    """A work submission or run lifecycle command cannot be accepted."""


class CoordinatorAuthorizationError(CoordinatorError):
    """At least one target failed the full Team authorization intersection."""

    def __init__(self, decisions: tuple[PermissionDecision, ...]) -> None:
        self.decisions = decisions
        reasons = "; ".join(
            f"{decision.action}: {decision.reason}" for decision in decisions
        )
        super().__init__(f"work request authorization denied: {reasons}")


@dataclass(frozen=True)
class WorkSubmission:
    request: Optional[dict[str, Any]]
    deliveries: tuple[dict[str, Any], ...] = ()
    message: Optional[dict[str, Any]] = None
    routing: Optional[RoutingDecision] = None
    denied: tuple[PermissionDecision, ...] = ()

    @property
    def dispatchable(self) -> bool:
        return self.request is not None and bool(self.deliveries)

    @property
    def requires_confirmation(self) -> bool:
        return bool(self.routing and self.routing.requires_confirmation)


class WorkCoordinator:
    """Coordinate durable work without coupling it to ``SessionManager``."""

    def __init__(
        self,
        work: WorkRepository,
        conversations: ConversationRepository,
        permissions: TeamPermissionService,
        *,
        events: Optional[EventRepository] = None,
        tasks: Optional[TaskRepository] = None,
        handoffs: Optional[HandoffRepository] = None,
        event_sink: Optional[Any] = None,
        snapshot_builder: Optional[
            Callable[[WorkRequest, Mapping[str, PermissionContext]], WorkRequest]
        ] = None,
    ) -> None:
        self.work = work
        self.conversations = conversations
        self.permissions = permissions
        self.events = events
        self.tasks = tasks
        self.handoffs = handoffs
        self.event_sink = event_sink
        # The application service owns the concrete agent/context repositories.
        # Keeping this callback optional preserves the low-level coordinator's
        # usefulness in isolated tests and compatibility imports.
        self.snapshot_builder = snapshot_builder

    def submit_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        sender: ActorRef,
        body: str,
        context_by_agent: Mapping[str, PermissionContext],
        message_idempotency_key: Optional[str] = None,
        request_id: Optional[str] = None,
        request_idempotency_key: Optional[str] = None,
        correlation_id: str,
        thread_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        quote_message_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        mention_actor_ids: tuple[str, ...] = (),
        at_all: bool = False,
        fanout_confirmed: bool = False,
        budget: Optional[WorkBudget] = None,
        priority: int = 0,
    ) -> WorkSubmission:
        """Persist a message, route it, then authorize and enqueue its recipients."""

        if not isinstance(sender, ActorRef):
            raise CoordinatorError("message sender must be an ActorRef")
        conversation = self.conversations.get_conversation(conversation_id)
        if conversation is None:
            raise CoordinatorError(f"conversation not found: {conversation_id}")
        policy = ConversationRoutePolicy(
            conversation_id=conversation_id,
            kind=conversation["conversation_kind"],
            member_actor_ids=tuple(member["actor_id"] for member in conversation["members"]),
            agent_member_ids=tuple(conversation["agent_member_ids"]),
            agent_actor_ids=tuple(conversation["agent_actor_ids"]),
            dispatchable_agent_ids=tuple(
                conversation.get("dispatchable_agent_ids", conversation["agent_member_ids"])
            ),
            dispatchable_agent_actor_ids=tuple(
                conversation.get("dispatchable_agent_actor_ids", conversation["agent_actor_ids"])
            ),
            reply_mode=conversation["reply_mode"],
            coordinator_agent_id=conversation["coordinator_agent_id"],
            allow_at_all=conversation["allow_at_all"],
            max_fanout=conversation["max_fanout"],
        )
        message = self.conversations.append_message(
            message_id,
            conversation_id,
            sender,
            body,
            message_idempotency_key or _stable_id("message", message_id),
            correlation_id,
            thread_id=thread_id,
            parent_message_id=parent_message_id,
            quote_message_id=quote_message_id,
            causation_id=causation_id,
            mentions=mention_actor_ids,
        )
        # The source event is durable before recipient routing and authorization.
        # A crashed coordinator can replay the same message event without inventing
        # a second source message or delivery.
        self._record_domain_event(
            "message.created",
            conversation["workspace_id"],
            sender.id,
            "message",
            message_id,
            correlation_id,
            {"conversation_id": conversation_id, "thread_id": thread_id},
            causation_id=causation_id,
        )
        decision = route_message(
            policy,
            sender,
            mention_actor_ids=mention_actor_ids,
            at_all=at_all,
            fanout_confirmed=fanout_confirmed,
        )
        if not decision.dispatchable:
            return WorkSubmission(None, message=message, routing=decision)
        source = (
            WorkSource.DIRECT_MESSAGE
            if policy.kind is ConversationKind.DIRECT
            else WorkSource.GROUP_MESSAGE
        )
        request = WorkRequest(
            request_id=request_id or _stable_id("request", message_id),
            workspace_id=conversation["workspace_id"],
            source=source,
            source_ref=ResourceRef("message", message_id),
            requester=sender,
            target_agent_ids=decision.target_agent_ids,
            instruction=body,
            idempotency_key=request_idempotency_key or _stable_id("dispatch", message_id),
            correlation_id=correlation_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            budget=budget or WorkBudget(),
            priority=priority,
            causation_id=causation_id,
        )
        submitted = self._submit_authorized(request, context_by_agent)
        return WorkSubmission(
            submitted.request,
            submitted.deliveries,
            message=message,
            routing=decision,
            denied=submitted.denied,
        )

    def submit_request(
        self,
        request: WorkRequest,
        context_by_agent: Mapping[str, PermissionContext],
    ) -> WorkSubmission:
        """Authorize and enqueue a pre-built request from a task-like source."""

        return self._submit_authorized(request, context_by_agent)

    def submit_task(
        self,
        *,
        request_id: str,
        workspace_id: str,
        task_id: str,
        requester: ActorRef,
        target_agent_ids: tuple[str, ...],
        instruction: str,
        idempotency_key: str,
        correlation_id: str,
        context_by_agent: Mapping[str, PermissionContext],
        expected_output: str = "",
        budget: Optional[WorkBudget] = None,
        priority: int = 0,
        crew_run_id: Optional[str] = None,
    ) -> WorkSubmission:
        if self.tasks is not None:
            task = self.tasks.get_task(task_id)
            if task is None or task["workspace_id"] != workspace_id:
                raise CoordinatorError("task work references a missing task")
            if task["status"] in {"done", "canceled"}:
                raise CoordinatorError("completed or canceled tasks cannot be dispatched")
            if any(item["status"] != "done" for item in task["dependencies"]):
                raise CoordinatorError("task dependencies are incomplete")
        request = WorkRequest(
            request_id=request_id,
            workspace_id=workspace_id,
            source=WorkSource.TASK,
            source_ref=ResourceRef("task", task_id),
            requester=requester,
            target_agent_ids=target_agent_ids,
            instruction=instruction,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            task_id=task_id,
            expected_output=expected_output,
            budget=budget or WorkBudget(),
            priority=priority,
        )
        return self._submit_authorized(
            request,
            context_by_agent,
            crew_run_id=crew_run_id,
        )

    def submit_automation(
        self,
        *,
        request_id: str,
        workspace_id: str,
        automation_id: str,
        requester: ActorRef,
        target_agent_ids: tuple[str, ...],
        instruction: str,
        idempotency_key: str,
        correlation_id: str,
        context_by_agent: Mapping[str, PermissionContext],
        budget: Optional[WorkBudget] = None,
        priority: int = 0,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        task_id: Optional[str] = None,
        permission_scope: Optional[Mapping[str, Any]] = None,
        expected_output: str = "",
    ) -> WorkSubmission:
        request = WorkRequest(
            request_id=request_id,
            workspace_id=workspace_id,
            source=WorkSource.AUTOMATION,
            source_ref=ResourceRef("automation", automation_id),
            requester=requester,
            target_agent_ids=target_agent_ids,
            instruction=instruction,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            task_id=task_id,
            expected_output=expected_output,
            permission_scope=permission_scope or {},
            budget=budget or WorkBudget(),
            priority=priority,
        )
        return self._submit_authorized(request, context_by_agent)

    def submit_connector(
        self,
        *,
        request_id: str,
        workspace_id: str,
        connector_event_id: str,
        requester: ActorRef,
        target_agent_ids: tuple[str, ...],
        instruction: str,
        idempotency_key: str,
        correlation_id: str,
        context_by_agent: Mapping[str, PermissionContext],
        budget: Optional[WorkBudget] = None,
        priority: int = 0,
    ) -> WorkSubmission:
        request = WorkRequest(
            request_id=request_id,
            workspace_id=workspace_id,
            source=WorkSource.CONNECTOR,
            source_ref=ResourceRef("connector_event", connector_event_id),
            requester=requester,
            target_agent_ids=target_agent_ids,
            instruction=instruction,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            budget=budget or WorkBudget(),
            priority=priority,
        )
        return self._submit_authorized(request, context_by_agent)

    def submit_handoff(
        self,
        handoff: HandoffContract,
        context_by_agent: Mapping[str, PermissionContext],
        *,
        causation_id: Optional[str] = None,
    ) -> WorkSubmission:
        if not isinstance(handoff, HandoffContract):
            raise CoordinatorError("handoff must be a HandoffContract")
        if self.tasks is not None:
            task = self.tasks.get_task(handoff.task_id)
            if task is None:
                raise CoordinatorError("handoff task is missing")
            if task["status"] in {"done", "canceled"}:
                raise CoordinatorError("completed or canceled tasks cannot receive a handoff")
            if any(item["status"] != "done" for item in task["dependencies"]):
                raise CoordinatorError("task dependencies are incomplete")
        if self.handoffs is not None:
            self.handoffs.create(handoff)
        self._record_domain_event(
            "handoff.created",
            handoff.workspace_id,
            handoff.requester.id,
            "handoff",
            handoff.handoff_id,
            handoff.correlation_id,
            {
                "task_id": handoff.task_id,
                "sender_agent_id": handoff.sender_agent_id,
                "recipient_agent_id": handoff.recipient_agent_id,
            },
        )
        request_id = _stable_id("request", handoff.handoff_id)
        existing_request = self.work.get_request(request_id)
        # A replay may arrive from a later worker run. Preserve the original
        # causal run recorded on the durable request; otherwise the same
        # idempotency key would look like a conflicting payload merely because
        # the retry has a new runtime identity.
        request_causation_id = (
            (existing_request or {}).get("causation_id")
            or causation_id
            or handoff.sender_agent_id
        )
        request = WorkRequest(
            request_id=request_id,
            workspace_id=handoff.workspace_id,
            source=WorkSource.HANDOFF,
            source_ref=ResourceRef("handoff", handoff.handoff_id),
            requester=handoff.requester,
            target_agent_ids=(handoff.recipient_agent_id,),
            instruction=handoff.purpose,
            idempotency_key=handoff.idempotency_key,
            correlation_id=handoff.correlation_id,
            task_id=handoff.task_id,
            expected_output=handoff.expected_output,
            input_artifact_ids=handoff.artifact_ids,
            budget=handoff.budget,
            causation_id=request_causation_id,
        )
        return self._submit_authorized(request, context_by_agent)

    def claim_next(
        self,
        agent_id: str,
        holder_id: str,
        *,
        now: Optional[str] = None,
        lease_seconds: int = 60,
        scan_limit: int = 50,
    ) -> Optional[dict[str, Any]]:
        """Recover abandoned leases, then claim the highest-priority ready delivery."""

        now = now or utc_now()
        self.work.recover_expired_leases(now=now)
        if not self.work.agent_can_claim(agent_id):
            return None
        if not self.work.agent_capacity_available(agent_id):
            return None
        for candidate in self.work.list_ready_deliveries(agent_id, now=now, limit=scan_limit):
            try:
                return self.work.lease_delivery(
                    candidate["delivery_id"],
                    holder_id,
                    lease_seconds=lease_seconds,
                    now=now,
                )
            except PersistenceConflictError:
                continue
        return None

    def release_delivery_lease(
        self,
        leased_delivery: Mapping[str, Any],
        *,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Release a lease that was claimed but never dispatched to a run."""

        delivery_id = str(leased_delivery.get("delivery_id") or "")
        lease_token = str(leased_delivery.get("lease_token") or "")
        if not delivery_id or not lease_token:
            raise CoordinatorError("leased delivery is missing its lease identity")
        return self.work.release_lease(delivery_id, lease_token, now=now)

    def heartbeat_delivery_lease(
        self,
        leased_delivery: Mapping[str, Any],
        *,
        lease_seconds: int = 60,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Extend a live worker lease while model/tool execution is in progress."""

        delivery_id = str(leased_delivery.get("delivery_id") or "")
        lease_token = str(leased_delivery.get("lease_token") or "")
        if not delivery_id or not lease_token:
            raise CoordinatorError("leased delivery is missing its lease identity")
        return self.work.heartbeat_lease(
            delivery_id,
            lease_token,
            lease_seconds=lease_seconds,
            now=now,
        )

    def start_delivery(
        self,
        leased_delivery: Mapping[str, Any],
        *,
        run_id: str,
        agent_revision_id: str,
        sandbox_id: str,
        budget: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Bind a claimed delivery to a sandbox-backed run and mark it running."""

        required = ("delivery_id", "request_id", "workspace_id", "target_agent_id", "lease_token")
        if any(not leased_delivery.get(key) for key in required):
            raise CoordinatorError("leased delivery is missing dispatch fields")
        now = now or utc_now()
        request = self.work.get_request(str(leased_delivery["request_id"]))
        if request is None:
            raise CoordinatorError("delivery request no longer exists")
        resume_run_id = str(leased_delivery.get("resume_run_id") or "").strip()
        if resume_run_id:
            if resume_run_id != run_id:
                raise CoordinatorError("approval resume run does not match the delivery")
            takeover_id = str(leased_delivery.get("resume_takeover_id") or "").strip()
            if takeover_id:
                running = self.work.resume_run_after_takeover(
                    run_id,
                    takeover_id,
                    str(leased_delivery["lease_token"]),
                    now=now,
                )
            else:
                running = self.work.resume_run_after_approval(
                    run_id,
                    str(leased_delivery.get("resume_approval_id") or ""),
                    str(leased_delivery["lease_token"]),
                    now=now,
                )
            self._record_run_event(
                run_id,
                "run.resumed",
                None,
                {
                    "delivery_id": running["delivery_id"],
                    **(
                        {"takeover_request_id": takeover_id}
                        if takeover_id
                        else {"approval_id": leased_delivery.get("resume_approval_id")}
                    ),
                },
            )
        else:
            run = self.work.dispatch_run(
                run_id,
                str(leased_delivery["delivery_id"]),
                str(leased_delivery["request_id"]),
                str(leased_delivery["workspace_id"]),
                str(leased_delivery["target_agent_id"]),
                agent_revision_id,
                sandbox_id,
                str(leased_delivery["lease_token"]),
                budget=budget if budget is not None else request["budget"],
                now=now,
            )
            running = self.work.transition_run(run_id, RunStatus.RUNNING)
            self._record_run_event(run_id, "run.started", None, {"delivery_id": running["delivery_id"]})
        return {
            "run": running,
            "delivery": self.work.get_delivery(str(leased_delivery["delivery_id"])),
        }

    def park_run_for_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        """Move a running delivery into durable approval wait state."""

        run = self.work.park_run_for_approval(run_id, approval_id, lease_token)
        self._record_run_event(
            run_id,
            "run.waiting_approval",
            None,
            {"approval_id": approval_id},
        )
        return {
            "run": run,
            "delivery": self.work.get_delivery(run["delivery_id"]),
        }

    def park_run_for_takeover(
        self,
        run_id: str,
        takeover_request_id: str,
        *,
        lease_token: str,
    ) -> dict[str, Any]:
        """Move a running delivery into durable human takeover wait state."""

        run = self.work.park_run_for_takeover(run_id, takeover_request_id, lease_token)
        self._record_run_event(
            run_id,
            "run.waiting_human",
            None,
            {"takeover_request_id": takeover_request_id},
        )
        return {
            "run": run,
            "delivery": self.work.get_delivery(run["delivery_id"]),
        }

    def resolve_takeover(self, takeover: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Apply a denied/canceled takeover decision to its parked run."""

        if str(takeover.get("status") or "") == "approved":
            return None
        run_id = str(takeover.get("run_id") or "").strip()
        takeover_id = str(takeover.get("takeover_request_id") or "").strip()
        if not run_id or not takeover_id:
            return None
        run = self.work.get_run(run_id)
        if run is None or run["status"] != RunStatus.WAITING_INPUT.value:
            return None
        result = self.work.fail_waiting_takeover(
            run_id,
            takeover_id,
            error_code="HumanTakeoverDenied",
            error_detail=f"human takeover was {takeover.get('status') or 'resolved'}",
        )
        self._record_run_event(
            run_id,
            "run.failed",
            None,
            {
                "error_code": "HumanTakeoverDenied",
                "takeover_request_id": takeover_id,
                "takeover_status": takeover.get("status"),
            },
        )
        result["request"] = self.work.mark_request_completed_if_settled(run["request_id"])
        return result

    def resolve_approval(self, approval: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Apply a denied/expired/canceled decision to its parked run."""

        if str(approval.get("status") or "") == "approved":
            return None
        run_id = str(approval.get("run_id") or "").strip()
        approval_id = str(approval.get("approval_id") or "").strip()
        if not run_id or not approval_id:
            return None
        run = self.work.get_run(run_id)
        if run is None or run["status"] != RunStatus.WAITING_APPROVAL.value:
            return None
        result = self.work.fail_waiting_approval(
            run_id,
            approval_id,
            error_code="ApprovalDenied",
            error_detail=f"tool approval was {approval.get('status') or 'resolved'}",
        )
        self._record_run_event(
            run_id,
            "run.failed",
            None,
            {
                "error_code": "ApprovalDenied",
                "approval_id": approval_id,
                "approval_status": approval.get("status"),
            },
        )
        result["request"] = self.work.mark_request_completed_if_settled(run["request_id"])
        return result

    def settle_run(
        self,
        run_id: str,
        outcome: RunStatus | str,
        *,
        lease_token: Optional[str] = None,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        retry_policy: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> dict[str, Any]:
        """Settle a run and its delivery with retry/dead-letter semantics."""

        outcome = _terminal_status(outcome)
        policy = _retry_policy(retry_policy)
        now = now or utc_now()
        run = self.work.get_run(run_id)
        if run is None:
            raise CoordinatorError(f"run not found: {run_id}")
        if run["status"] not in {status.value for status in _terminal_statuses()}:
            run = self.work.transition_run(run_id, outcome)
        elif run["status"] != outcome.value:
            raise CoordinatorError("run is already settled with a different outcome")
        self._record_run_event(run_id, f"run.{outcome.value}", None, {
            "error_code": error_code,
            "error_detail": error_detail,
        })
        delivery = self.work.get_delivery(run["delivery_id"])
        if delivery is None:
            raise CoordinatorError("run delivery no longer exists")
        attempt_outcome = {
            RunStatus.SUCCEEDED: "accepted",
            RunStatus.FAILED: "failed",
            RunStatus.TIMED_OUT: "timed_out",
            RunStatus.CANCELED: "canceled",
        }[outcome]
        self.work.finish_attempt(
            run["delivery_id"],
            attempt_outcome,
            error_code=error_code,
            error_detail=error_detail,
            finished_at=now,
        )

        if outcome is RunStatus.SUCCEEDED:
            if delivery["status"] == DeliveryStatus.DISPATCHED.value:
                if not lease_token:
                    raise CoordinatorError("successful settlement requires the delivery lease")
                delivery = self.work.acknowledge(run["delivery_id"], lease_token, now=now)
        elif delivery["status"] == DeliveryStatus.DISPATCHED.value:
            if not lease_token:
                raise CoordinatorError("failed settlement requires the delivery lease")
            retryable = outcome is not RunStatus.CANCELED and policy["retryable"]
            if retryable and int(delivery["attempt_count"]) < policy["max_attempts"]:
                delay = min(
                    policy["max_delay_seconds"],
                    policy["base_delay_seconds"] * (2 ** max(0, int(delivery["attempt_count"]) - 1)),
                )
                next_attempt = _shift_timestamp(now, delay)
                delivery = self.work.finish_leased_delivery(
                    run["delivery_id"],
                    lease_token,
                    DeliveryStatus.RETRY_WAIT,
                    next_attempt_at=next_attempt,
                    now=now,
                )
            else:
                delivery = self.work.finish_leased_delivery(
                    run["delivery_id"],
                    lease_token,
                    DeliveryStatus.CANCELED if outcome is RunStatus.CANCELED else DeliveryStatus.DEAD_LETTER,
                    now=now,
                )
        request = self.work.mark_request_completed_if_settled(run["request_id"])
        return {"run": run, "delivery": delivery, "request": request}

    def record_run_output(
        self,
        run_id: str,
        *,
        actor_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Persist the worker's output before its run is settled."""

        run = self.work.get_run(run_id)
        if run is None:
            raise CoordinatorError(f"run not found: {run_id}")
        text = str(text or "").strip()
        if not text:
            raise CoordinatorError("run output cannot be empty")
        return self.work.record_run_event(
            _stable_id("run-event", f"{run_id}:output"),
            run_id,
            "run.output",
            actor_id=actor_id,
            payload={"text": text},
        )

    def record_execution_event(self, event: AgentExecutionEvent) -> dict[str, Any]:
        """Persist one normalized driver event and project it to Team events."""

        if not isinstance(event, AgentExecutionEvent):
            raise CoordinatorError("execution event must be an AgentExecutionEvent")
        run = self.work.get_run(event.run_id)
        if run is None:
            raise CoordinatorError(f"run not found: {event.run_id}")
        if run["agent_id"] != event.agent_id:
            raise CoordinatorError("execution event agent does not match its run")
        persisted = self.work.record_run_event(
            event.event_id,
            event.run_id,
            event.event_type.value,
            actor_id=event.actor_id,
            payload={"ordinal": event.ordinal, **dict(event.payload)},
            occurred_at=event.occurred_at,
        )
        request = self.work.get_request(run["request_id"])
        if request is not None:
            self._record_domain_event(
                event.event_type.value,
                run["workspace_id"],
                event.actor_id,
                "run",
                event.run_id,
                request["correlation_id"],
                {
                    "run_event_id": event.event_id,
                    "ordinal": event.ordinal,
                    **dict(event.payload),
                },
                causation_id=request["request_id"],
                event_id=_stable_id("execution-event", event.event_id),
                occurred_at=event.occurred_at,
            )
        return persisted

    def record_tool_execution_event(
        self,
        *,
        event_id: str,
        run_id: str,
        agent_id: str,
        actor_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        occurred_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Persist a tool-gateway audit event and project it to the event journal."""

        run = self.work.get_run(run_id)
        if run is None:
            raise CoordinatorError(f"run not found: {run_id}")
        if run["agent_id"] != agent_id:
            raise CoordinatorError("tool execution agent does not match its run")
        persisted = self.work.record_run_event(
            event_id,
            run_id,
            event_type,
            actor_id=actor_id,
            payload=payload,
            occurred_at=occurred_at,
        )
        request = self.work.get_request(run["request_id"])
        if request is not None:
            self._record_domain_event(
                event_type,
                run["workspace_id"],
                actor_id,
                "run",
                run_id,
                request["correlation_id"],
                {"run_event_id": event_id, **dict(payload)},
                causation_id=request["request_id"],
                event_id=_stable_id("tool-execution", event_id),
                occurred_at=occurred_at,
            )
        return persisted

    def cancel_request(self, request_id: str, *, reason: str = "canceled by requester") -> dict[str, Any]:
        return self.work.cancel_request(request_id, reason=reason)

    def cancel_delivery(self, delivery_id: str, *, reason: str = "canceled by requester") -> dict[str, Any]:
        delivery = self.work.cancel_delivery(delivery_id, reason=reason)
        return delivery

    def cancel_run(self, run_id: str, *, reason: str = "canceled by requester") -> dict[str, Any]:
        result = self.work.cancel_run(run_id, reason=reason)
        run = result["run"]
        result["request"] = self.work.mark_request_completed_if_settled(run["request_id"])
        return result

    def cancel_scope(
        self,
        scope_kind: str,
        scope_id: str,
        *,
        reason: str = "canceled by requester",
    ) -> dict[str, Any]:
        """Cancel all descendant requests and report terminal work untouched."""

        request_ids = self.work.list_request_ids_for_scope(scope_kind, scope_id)
        active_run_ids = self.work.list_active_run_ids_for_requests(request_ids)
        canceled: list[str] = []
        already_canceled: list[str] = []
        untouched_terminal: list[str] = []
        for request_id in request_ids:
            request = self.work.get_request(request_id)
            if request is None:
                continue
            if request["status"] == "active":
                self.work.cancel_request(request_id, reason=reason)
                canceled.append(request_id)
            elif request["status"] == "canceled":
                already_canceled.append(request_id)
            else:
                # Completed requests remain immutable, but the caller still gets
                # an explicit report of work that could not be undone.
                untouched_terminal.append(request_id)
        return {
            "scope_kind": scope_kind,
            "scope_id": scope_id,
            "canceled_request_ids": canceled,
            "already_canceled_request_ids": already_canceled,
            "untouched_terminal_request_ids": untouched_terminal,
            "canceled_run_ids": active_run_ids,
            "request_count": len(request_ids),
        }

    def consume_usage(self, request_id: str, increments: Mapping[str, int]) -> dict[str, Any]:
        try:
            return self.work.consume_usage(request_id, increments)
        except BudgetExceededError as error:
            if error.request_id is not None:
                self.work.pause_request_budget(
                    error.request_id,
                    reason=f"budget exhausted: {', '.join(error.fields)}",
                )
            raise

    def resume_request(self, request_id: str) -> dict[str, Any]:
        return self.work.resume_request(request_id)

    def _submit_authorized(
        self,
        request: WorkRequest,
        context_by_agent: Mapping[str, PermissionContext],
        *,
        crew_run_id: Optional[str] = None,
    ) -> WorkSubmission:
        if not isinstance(context_by_agent, Mapping):
            raise CoordinatorError("context_by_agent must be a mapping")
        reject_prompt_policy_mutation(request.instruction)
        if request.budget.max_fanout is not None and len(request.target_agent_ids) > request.budget.max_fanout:
            raise CoordinatorError("work request fan-out exceeds its budget")
        denied: list[PermissionDecision] = []
        for agent_id in request.target_agent_ids:
            context = context_by_agent.get(agent_id)
            if context is None:
                raise CoordinatorError(f"missing authorization context for target agent: {agent_id}")
            if context.agent_id != agent_id:
                raise CoordinatorError("authorization context is bound to a different target agent")
            decision = self.permissions.authorize_request(request, context)
            if not decision.allowed:
                denied.append(decision)
        if denied:
            raise CoordinatorAuthorizationError(tuple(denied))
        if self.snapshot_builder is not None:
            request = self.snapshot_builder(request, context_by_agent)
        persisted = self.work.create_request(request, crew_run_id=crew_run_id)
        deliveries = tuple(self.work.create_deliveries(request.request_id))
        return WorkSubmission(persisted, deliveries, denied=())

    def _record_run_event(
        self,
        run_id: str,
        event_type: str,
        actor_id: Optional[str],
        payload: Mapping[str, Any],
    ) -> None:
        try:
            self.work.record_run_event(
                _stable_id("run-event", f"{run_id}:{event_type}"),
                run_id,
                event_type,
                actor_id=actor_id,
                payload=payload,
            )
        except PersistenceConflictError:
            pass

    def _record_domain_event(
        self,
        event_type: str,
        workspace_id: str,
        actor_id: str,
        aggregate_type: str,
        aggregate_id: str,
        correlation_id: str,
        payload: Mapping[str, Any],
        *,
        causation_id: Optional[str] = None,
        event_id: Optional[str] = None,
        occurred_at: Optional[str] = None,
    ) -> None:
        if self.events is None:
            return
        try:
            persisted = self.events.append(
                DomainEvent(
                    event_id=event_id or _stable_id("event", f"{event_type}:{aggregate_id}"),
                    event_type=event_type,
                    workspace_id=workspace_id,
                    actor_id=actor_id,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    payload=payload,
                    occurred_at=occurred_at or utc_now(),
                )
            )
            if self.event_sink is not None:
                self.event_sink(persisted)
        except PersistenceConflictError:
            # Replays must observe the original source event.
            pass


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"


def _terminal_status(value: RunStatus | str) -> RunStatus:
    if isinstance(value, str) and value in {"success", "succeeded"}:
        return RunStatus.SUCCEEDED
    if isinstance(value, str) and value in {"failure", "failed"}:
        return RunStatus.FAILED
    if isinstance(value, str) and value in {"timeout", "timed_out"}:
        return RunStatus.TIMED_OUT
    if isinstance(value, str) and value == "canceled":
        return RunStatus.CANCELED
    if isinstance(value, RunStatus) and value in _terminal_statuses():
        return value
    raise CoordinatorError("run settlement requires succeeded, failed, timed_out, or canceled")


def _terminal_statuses() -> frozenset[RunStatus]:
    return frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELED})


def _retry_policy(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    values = dict(raw or {})
    unknown = set(values) - {"max_attempts", "base_delay_seconds", "max_delay_seconds", "retryable"}
    if unknown:
        raise CoordinatorError(f"retry policy contains unknown fields: {', '.join(sorted(unknown))}")
    result = {
        "max_attempts": values.get("max_attempts", 3),
        "base_delay_seconds": values.get("base_delay_seconds", 1),
        "max_delay_seconds": values.get("max_delay_seconds", 300),
        "retryable": values.get("retryable", True),
    }
    for name in ("max_attempts", "base_delay_seconds", "max_delay_seconds"):
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CoordinatorError(f"retry policy {name} must be a positive integer")
    if result["max_delay_seconds"] < result["base_delay_seconds"]:
        raise CoordinatorError("retry policy max_delay_seconds must cover base_delay_seconds")
    if not isinstance(result["retryable"], bool):
        raise CoordinatorError("retry policy retryable must be boolean")
    return result


def _shift_timestamp(value: str, seconds: int) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise CoordinatorError("timestamp is malformed") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat()
