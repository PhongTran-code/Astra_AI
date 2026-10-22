"""Provider-neutral execution events for the canonical Team run stream.

The existing TurnEngine has a surface-oriented event vocabulary. Team persistence
needs a smaller, stable vocabulary that can be emitted by any future provider
driver without leaking chain-of-thought or credentials.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from ..events import Event, EventType
from .contracts import ContractError, utc_now
from .permissions import redact_sensitive


class ExecutionEventType(str, Enum):
    TURN_STARTED = "agent.turn_started"
    CONTENT_DELTA = "agent.content.delta"
    REASONING_DELTA = "agent.reasoning.delta"
    CONTENT_MESSAGE = "agent.content.message"
    TOOL_PROPOSED = "tool.proposed"
    APPROVAL_REQUESTED = "approval.requested"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    ITERATION_COMPLETED = "agent.iteration.completed"
    TURN_COMPLETED = "agent.turn_completed"
    CONTEXT_COMPACTING = "agent.context.compacting"
    CONTEXT_COMPACTED = "agent.context.compacted"
    RUN_ERROR = "run.error"
    RUN_INTERRUPTED = "run.interrupted"


_TURN_EVENT_TYPES: dict[EventType, ExecutionEventType] = {
    EventType.TURN_START: ExecutionEventType.TURN_STARTED,
    EventType.ASSISTANT_DELTA: ExecutionEventType.CONTENT_DELTA,
    EventType.ASSISTANT_MESSAGE: ExecutionEventType.CONTENT_MESSAGE,
    EventType.TOOL_PROPOSED: ExecutionEventType.TOOL_PROPOSED,
    EventType.PERMISSION_REQUIRED: ExecutionEventType.APPROVAL_REQUESTED,
    EventType.TOOL_STARTED: ExecutionEventType.TOOL_STARTED,
    EventType.TOOL_FINISHED: ExecutionEventType.TOOL_FINISHED,
    EventType.ITERATION_END: ExecutionEventType.ITERATION_COMPLETED,
    EventType.TURN_END: ExecutionEventType.TURN_COMPLETED,
    EventType.COMPACTING: ExecutionEventType.CONTEXT_COMPACTING,
    EventType.COMPACTED: ExecutionEventType.CONTEXT_COMPACTED,
    EventType.ERROR: ExecutionEventType.RUN_ERROR,
    EventType.INTERRUPTED: ExecutionEventType.RUN_INTERRUPTED,
}


@dataclass(frozen=True)
class AgentExecutionEvent:
    """One safe, ordered event emitted by an AgentDriver."""

    event_id: str
    run_id: str
    agent_id: str
    actor_id: str
    event_type: ExecutionEventType | str
    ordinal: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name in ("event_id", "run_id", "agent_id", "actor_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ContractError(f"execution event {name} is required")
            object.__setattr__(self, name, value)
        try:
            event_type = (
                self.event_type
                if isinstance(self.event_type, ExecutionEventType)
                else ExecutionEventType(str(self.event_type))
            )
        except (TypeError, ValueError) as error:
            raise ContractError("execution event type is unsupported") from error
        object.__setattr__(self, "event_type", event_type)
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal <= 0:
            raise ContractError("execution event ordinal must be a positive integer")
        if not isinstance(self.payload, Mapping):
            raise ContractError("execution event payload must be an object")
        # Redaction is part of normalization, so callers cannot accidentally
        # persist a provider credential in an otherwise valid event.
        object.__setattr__(self, "payload", redact_sensitive(dict(self.payload)))

    @classmethod
    def from_turn_event(
        cls,
        *,
        run_id: str,
        agent_id: str,
        actor_id: str,
        ordinal: int,
        event: Event,
        stream_key: str = "",
    ) -> Optional["AgentExecutionEvent"]:
        """Normalize a TurnEngine event into an AgentExecutionEvent.

        A resumed approval/takeover run starts a fresh driver stream but keeps the
        same durable run id.  ``stream_key`` keeps that stream's idempotency keys
        distinct while preserving the original ids for first-attempt events.
        """

        if not isinstance(event, Event):
            raise ContractError("TurnEngine event must be an Event")
        event_type = _TURN_EVENT_TYPES.get(event.type)
        if event_type is None:
            return None
        event_key = (
            f"{run_id}:{stream_key}:{ordinal}:{event.type.value}"
            if stream_key
            else f"{run_id}:{ordinal}:{event.type.value}"
        )
        digest = hashlib.sha256(event_key.encode("utf-8")).hexdigest()[:32]
        return cls(
            event_id=f"run-event:{digest}",
            run_id=run_id,
            agent_id=agent_id,
            actor_id=actor_id,
            event_type=event_type,
            ordinal=ordinal,
            payload=event.data or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "actor_id": self.actor_id,
            "event_type": self.event_type.value,
            "ordinal": self.ordinal,
            "payload": dict(self.payload),
            "occurred_at": self.occurred_at,
        }
