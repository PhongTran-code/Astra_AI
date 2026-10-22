"""Durable audit boundary for Team tool execution.

``TurnEngine`` still owns tool proposal and permission UX.  Once a Team tool is
authorized, this gateway is the last boundary before the callable runs.  It
records a redacted, arguments-hashed start marker before execution and a
terminal marker after execution.  A start marker without a terminal marker is
treated as unknown on replay; the worker must reconcile it instead of blindly
duplicating a possible side effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .permissions import redact_sensitive
from .contracts import ResourceRef
from .persistence import PersistenceConflictError


class ToolExecutionReconciliationRequired(RuntimeError):
    """A previous attempt may have run a tool without a durable terminal result."""


class ToolGatewayError(RuntimeError):
    """A Team tool call is missing the stable execution context it requires."""


class ToolApprovalRequired(ToolGatewayError):
    """The durable governance record must be resolved before a tool can run."""

    pause_turn = True

    def __init__(self, approval_id: str, tool_name: str) -> None:
        self.approval_id = _required_text(approval_id, "approval id")
        self.tool_name = _required_text(tool_name, "tool name")
        super().__init__(f"tool approval is pending: {self.approval_id}")


class ToolGateway:
    """Registry-compatible facade that adds Team-scoped execution audit."""

    def __init__(
        self,
        registry: Any,
        *,
        coordinator: Any,
        run_id: str,
        agent_id: str,
        actor_id: str,
        requester_actor_id: str,
        governance: Any | None = None,
        workspace_id: str | None = None,
        request_id: str | None = None,
        approval_policy: Mapping[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self.coordinator = coordinator
        self.run_id = _required_text(run_id, "run id")
        self.agent_id = _required_text(agent_id, "agent id")
        self.actor_id = _required_text(actor_id, "tool actor id")
        self.requester_actor_id = _required_text(
            requester_actor_id, "requester actor id"
        )
        self.governance = governance
        self.workspace_id = str(workspace_id or "").strip()
        self.request_id = str(request_id or "").strip()
        self.approval_policy = dict(approval_policy or {})

    def get(self, name: str) -> Any:
        return self.registry.get(name)

    def names(self) -> list[str]:
        return self.registry.names()

    def schemas(self) -> list[dict[str, Any]]:
        return self.registry.schemas()

    def execute(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        """Reject unscoped calls instead of providing an audit bypass."""

        raise ToolGatewayError(
            "Team tools require execute_with_context and a stable tool call id"
        )

    def execute_with_context(
        self,
        tool_call_id: str,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> Any:
        tool_call_id = _required_text(tool_call_id, "tool call id")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool name is required")
        if arguments is not None and not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be an object")
        normalized_arguments = dict(arguments or {})
        arguments_hash = _arguments_hash(normalized_arguments)
        self._ensure_approval(
            tool_call_id,
            name,
            arguments_hash=arguments_hash,
        )
        started_id = _event_id(self.run_id, tool_call_id, "started")
        finished_id = _event_id(self.run_id, tool_call_id, "finished")

        for event_id, description in (
            (finished_id, "has a terminal audit record"),
            (started_id, "may have started"),
        ):
            previous = self.coordinator.work.get_run_event(event_id)
            if previous is not None:
                previous_payload = previous.get("payload") or {}
                if previous_payload.get("arguments_sha256") == arguments_hash:
                    raise ToolExecutionReconciliationRequired(
                        f"tool call {description}: {tool_call_id}"
                    )
                raise RuntimeError("tool call id was reused with different arguments")

        audit_base = {
            "tool_call_id": tool_call_id,
            "tool_name": name,
            "arguments_sha256": arguments_hash,
            "agent_id": self.agent_id,
            "requester_actor_id": self.requester_actor_id,
        }
        self.coordinator.record_tool_execution_event(
            event_id=started_id,
            run_id=self.run_id,
            agent_id=self.agent_id,
            actor_id=self.actor_id,
            event_type="tool.execution.started",
            payload={**audit_base, "status": "started"},
            occurred_at=_utc_now(),
        )

        try:
            result = self.registry.execute(name, normalized_arguments)
        except Exception as error:
            self._record_finished(
                finished_id,
                audit_base,
                status="failed",
                error=error,
            )
            raise

        self._record_finished(finished_id, audit_base, status="succeeded")
        return result

    def _ensure_approval(
        self,
        tool_call_id: str,
        name: str,
        *,
        arguments_hash: str,
    ) -> None:
        """Apply the durable Team approval gate before any side effect marker.

        The legacy engine may be configured to bypass its interactive approval
        callback in a background worker.  That setting is intentionally not an
        authority grant here: Team governance is checked at the final execution
        boundary and records only a digest of arguments, never their contents.
        """

        spec = self.registry.get(name)
        metadata = getattr(spec, "metadata", None)
        # Existing Team revisions may intentionally run under the legacy
        # installation-grant behavior.  Durable approval is opt-in per pinned
        # revision during rollout; once a revision declares the policy, the
        # metadata risk flag can never turn that requirement off.
        requires_approval = bool(
            self.approval_policy.get("require_tool_approval", False)
            or self.approval_policy.get("durable_approval", False)
        ) and bool(getattr(metadata, "requires_approval", False))
        if not requires_approval:
            return
        if self.governance is None or not self.workspace_id or not self.request_id:
            raise ToolGatewayError(
                "Team tool approval is unavailable for a consequential tool"
            )

        approval_id = _approval_id(self.run_id, tool_call_id)
        existing = self.governance.get_approval(approval_id)
        if existing is not None:
            status = str(existing.get("status") or "")
            if status == "approved" and self.governance.approval_is_valid(approval_id):
                return
            if status == "pending":
                raise ToolApprovalRequired(approval_id, name)
            raise ToolGatewayError(f"tool approval is {status or 'unavailable'}: {approval_id}")

        reason = (
            f"Tool '{name}' requires human approval; arguments digest "
            f"{arguments_hash}"
        )
        try:
            self.governance.create_approval(
                approval_id,
                self.workspace_id,
                self.requester_actor_id,
                "tool.execute",
                ResourceRef("tool_execution", _approval_resource_id(self.run_id, tool_call_id)),
                reason,
                request_id=self.request_id,
                run_id=self.run_id,
                expires_at=(datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
            )
        except PersistenceConflictError:
            # Another worker may have created the same deterministic approval.
            # Re-read the row and fail closed if it is not already approved.
            existing = self.governance.get_approval(approval_id)
            if existing is None:
                raise
            status = str(existing.get("status") or "")
            if status == "approved" and self.governance.approval_is_valid(approval_id):
                return
            if status == "pending":
                raise ToolApprovalRequired(approval_id, name)
            raise ToolGatewayError(f"tool approval is {status or 'unavailable'}: {approval_id}")
        raise ToolApprovalRequired(approval_id, name)

    def _record_finished(
        self,
        event_id: str,
        audit_base: Mapping[str, Any],
        *,
        status: str,
        error: Exception | None = None,
    ) -> None:
        payload: dict[str, Any] = {**audit_base, "status": status}
        if error is not None:
            payload["error"] = redact_sensitive(str(error))[:2_000]
        try:
            self.coordinator.record_tool_execution_event(
                event_id=event_id,
                run_id=self.run_id,
                agent_id=self.agent_id,
                actor_id=self.actor_id,
                event_type="tool.execution.finished",
                payload=payload,
                occurred_at=_utc_now(),
            )
        except Exception as persistence_error:
            raise ToolExecutionReconciliationRequired(
                f"tool execution outcome could not be durably recorded: {persistence_error}"
            ) from persistence_error


def _event_id(run_id: str, tool_call_id: str, phase: str) -> str:
    digest = hashlib.sha256(
        f"{run_id}:{tool_call_id}:{phase}".encode("utf-8")
    ).hexdigest()[:32]
    return f"tool-event:{digest}"


def _approval_id(run_id: str, tool_call_id: str) -> str:
    return f"approval:{_approval_resource_id(run_id, tool_call_id)}"


def _approval_resource_id(run_id: str, tool_call_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{tool_call_id}".encode("utf-8")).hexdigest()[:32]
    return f"tool:{digest}"


def _arguments_hash(arguments: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(arguments).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    return text


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
