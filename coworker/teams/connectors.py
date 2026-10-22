"""Requester-aware connector authority for the canonical Team runtime.

This module is deliberately separate from the legacy connector tool catalog.
The catalog knows how to speak to Slack/GitHub/Gmail; this service decides
whether a particular Team agent may use a particular external identity for a
particular provider resource on behalf of a particular requester.  Credentials
are resolved by the connector adapter, never by a prompt or Team record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .contracts import (
    ActorKind,
    ActorRef,
    ContractError,
    DomainIdPrefix,
    ResourceRef,
    WorkBudget,
    WorkRequest,
    WorkSource,
    new_domain_id,
)
from .coordinator import WorkCoordinator, WorkSubmission
from .permissions import redact_sensitive
from .persistence import (
    ConnectorRepository,
    PersistenceConflictError,
)


class ConnectorAuthorityError(ContractError):
    """A connector identity, grant, or external action is not authorized."""


ConnectorActionExecutor = Callable[
    [Mapping[str, Any], str, str, Mapping[str, Any], str], Mapping[str, Any]
]
ConnectorOutboundSender = Callable[
    [Mapping[str, Any], Mapping[str, Any], str, str], Mapping[str, Any]
]
PermissionContextFactory = Callable[[str, ActorRef], Any]


@dataclass(frozen=True)
class ConnectorEventSubmission:
    event: dict[str, Any]
    submission: Optional[WorkSubmission]
    replayed: bool = False


class TeamConnectorService:
    """Own external identity authority and connector event delivery state."""

    _ACTIONS = frozenset({"connector.read", "connector.send", "connector.write"})

    def __init__(
        self,
        repository: ConnectorRepository,
        coordinator: WorkCoordinator,
        *,
        workspace_id: str,
        actor_id: str,
        context_factory: PermissionContextFactory,
        event_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        action_executor: Optional[ConnectorActionExecutor] = None,
        outbound_sender: Optional[ConnectorOutboundSender] = None,
    ) -> None:
        self.repository = repository
        self.coordinator = coordinator
        self.workspace_id = str(workspace_id).strip()
        self.actor_id = str(actor_id).strip()
        if not self.workspace_id or not self.actor_id:
            raise ConnectorAuthorityError("connector service requires workspace and actor")
        self.context_factory = context_factory
        self.event_sink = event_sink
        self.action_executor = action_executor
        self.outbound_sender = outbound_sender

    def register_identity(
        self,
        *,
        connector_kind: str,
        external_workspace_id: str,
        external_identity_id: str,
        account_ref: str = "",
        display_name: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Register provider metadata without importing any credential material."""

        self._require_human_requester()
        connector_kind = str(connector_kind or "").strip().lower()
        if not connector_kind:
            raise ConnectorAuthorityError("connector kind is required")
        immutable_key = ":".join(
            (self.workspace_id, connector_kind, str(external_workspace_id), str(external_identity_id))
        )
        record_id = record_id or _stable_id("external-identity", immutable_key)
        record = self.repository.create_external_identity(
            record_id,
            self.workspace_id,
            self.actor_id,
            connector_kind,
            external_workspace_id,
            external_identity_id,
            account_ref=account_ref,
            display_name=display_name,
            metadata=redact_sensitive(dict(metadata or {})),
        )
        self._emit("connector.identity_registered", "external_identity", record_id, {
            "connector_kind": connector_kind,
            "external_workspace_id": record["external_workspace_id"],
            "external_identity_id": record["external_identity_id"],
        })
        return record

    def list_identities(self, *, connector_kind: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        self._require_human_requester()
        return self.repository.list_external_identities(
            self.workspace_id, connector_kind=connector_kind, limit=limit
        )

    def get_identity(self, record_id: str) -> dict[str, Any]:
        self._require_human_requester()
        record = self.repository.get_external_identity(record_id)
        if record is None or record["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("external identity not found")
        return record

    def transition_identity(self, record_id: str, status: str) -> dict[str, Any]:
        self._require_human_requester()
        record = self.get_identity(record_id)
        if record["owner_actor_id"] != self.actor_id:
            raise ConnectorAuthorityError("only the external identity owner may change its status")
        updated = self.repository.transition_external_identity(record_id, status)
        self._emit(
            "connector.identity_status_changed",
            "external_identity",
            record_id,
            {"from": record["status"], "to": updated["status"]},
        )
        return updated

    def update_identity_health(
        self,
        record_id: str,
        health_status: str,
        *,
        error_detail: Optional[str] = None,
        reconnect: bool = False,
    ) -> dict[str, Any]:
        self._require_human_requester()
        record = self.get_identity(record_id)
        if record["owner_actor_id"] != self.actor_id:
            raise ConnectorAuthorityError("only the external identity owner may change health")
        updated = self.repository.update_external_identity_health(
            record_id,
            health_status,
            error_detail=error_detail,
            reconnect=reconnect,
        )
        self._emit(
            "connector.identity_health_changed",
            "external_identity",
            record_id,
            {
                "from": record.get("health_status", "unknown"),
                "to": updated["health_status"],
                "error": updated.get("last_health_error"),
                "reconnect": reconnect,
            },
        )
        return updated

    def grant_agent(
        self,
        *,
        external_identity_record_id: str,
        agent_id: str,
        actions: Sequence[str],
        resource_ids: Sequence[str],
        requester_actor_id: Optional[str] = None,
        expires_at: Optional[str] = None,
        grant_id: Optional[str] = None,
        binding_id: Optional[str] = None,
        destination_conversation_id: Optional[str] = None,
        destination_thread_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Grant one exact requester/agent/resource boundary and bind it.

        The grant is intentionally per requester.  A grant for the owner does
        not let an agent act for a different teammate, even when both use the
        same external account.
        """

        self._require_human_requester()
        identity = self.get_identity(external_identity_record_id)
        if identity["owner_actor_id"] != self.actor_id:
            raise ConnectorAuthorityError("only the external identity owner may grant its use")
        requester_actor_id = str(requester_actor_id or self.actor_id).strip()
        if not requester_actor_id:
            raise ConnectorAuthorityError("connector requester actor is required")
        grant_key = ":".join(
            (
                self.workspace_id,
                external_identity_record_id,
                requester_actor_id,
                str(agent_id),
            )
        )
        grant_id = grant_id or _stable_id("connector-grant", grant_key)
        binding_id = binding_id or _stable_id("connector-binding", grant_id)
        grant = self.repository.create_grant(
            grant_id,
            self.workspace_id,
            external_identity_record_id,
            self.actor_id,
            requester_actor_id,
            agent_id,
            actions,
            resource_ids,
            expires_at=expires_at,
        )
        binding = self.repository.bind_agent(
            binding_id,
            self.workspace_id,
            external_identity_record_id,
            agent_id,
            grant_id,
            destination_conversation_id=destination_conversation_id,
            destination_thread_id=destination_thread_id,
        )
        self._emit(
            "connector.grant_created",
            "connector_grant",
            grant_id,
            {
                "external_identity_record_id": external_identity_record_id,
                "agent_id": agent_id,
                "requester_actor_id": requester_actor_id,
                "actions": list(grant["actions"]),
                "resource_ids": list(grant["resource_ids"]),
            },
        )
        return {"grant": grant, "binding": binding}

    def list_grants(self, *, agent_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        self._require_human_requester()
        return self.repository.list_grants(self.workspace_id, agent_id=agent_id, limit=limit)

    def list_bindings(self, *, agent_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        self._require_human_requester()
        return self.repository.list_bindings(self.workspace_id, agent_id=agent_id, limit=limit)

    def revoke_grant(self, grant_id: str) -> dict[str, Any]:
        self._require_human_requester()
        grant = self.repository.get_grant(grant_id)
        if grant is None or grant["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("connector grant not found")
        if grant["grantor_actor_id"] != self.actor_id:
            raise ConnectorAuthorityError("only the connector grantor can revoke the grant")
        updated = self.repository.revoke_grant(grant_id, actor_id=self.actor_id)
        self._emit("connector.grant_revoked", "connector_grant", grant_id, {})
        return updated

    def revoke_binding(self, binding_id: str) -> dict[str, Any]:
        self._require_human_requester()
        binding = self.repository.get_binding(binding_id)
        if binding is None or binding["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("connector binding not found")
        grant = self.repository.get_grant(binding["connector_grant_id"])
        if grant is None or grant["grantor_actor_id"] != self.actor_id:
            raise ConnectorAuthorityError("only the connector grantor can revoke the binding")
        binding = self.repository.revoke_binding(binding_id)
        self._emit("connector.binding_revoked", "connector_binding", binding_id, {})
        return binding

    def authorize(
        self,
        *,
        external_identity_record_id: str,
        agent_id: str,
        requester_actor_id: str,
        resource_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Re-read the durable authority boundary immediately before use."""

        grant = self.repository.effective_grant(
            self.workspace_id,
            external_identity_record_id,
            agent_id,
            requester_actor_id,
            resource_id,
            action,
        )
        if grant is None:
            raise ConnectorAuthorityError(
                "connector action is not authorized for this requester, agent, and resource"
            )
        return grant

    def execute_action(
        self,
        *,
        external_identity_record_id: str,
        agent_id: str,
        requester_actor_id: str,
        action: str,
        resource_id: str,
        arguments: Mapping[str, Any],
        request_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Execute through an adapter only after a fresh grant check.

        Adapter callbacks receive provider metadata and redacted arguments, not
        a SecretStore or token.  They are responsible for resolving a short-lived
        credential using the identity's non-secret ``account_ref``.
        """

        if not isinstance(arguments, Mapping):
            return {"error": "connector action arguments must be an object"}
        try:
            grant = self.authorize(
                external_identity_record_id=external_identity_record_id,
                agent_id=agent_id,
                requester_actor_id=requester_actor_id,
                resource_id=resource_id,
                action=action,
            )
        except ConnectorAuthorityError as error:
            result = {"error": str(error), "authorized": False}
            self._audit_action(
                "connector.action_denied",
                external_identity_record_id,
                agent_id,
                requester_actor_id,
                action,
                resource_id,
                request_id,
                run_id,
                result,
            )
            return result
        identity = self.repository.get_external_identity(external_identity_record_id)
        if identity is None or identity["workspace_id"] != self.workspace_id:
            result = {"error": "external identity not found", "authorized": False}
            self._audit_action(
                "connector.action_denied",
                external_identity_record_id,
                agent_id,
                requester_actor_id,
                action,
                resource_id,
                request_id,
                run_id,
                result,
            )
            return result
        if self.action_executor is None:
            result = {
                "error": "connector adapter is unavailable",
                "authorized": True,
                "connector_kind": identity["connector_kind"],
            }
            self._audit_action(
                "connector.action_failed",
                external_identity_record_id,
                agent_id,
                requester_actor_id,
                action,
                resource_id,
                request_id,
                run_id,
                result,
            )
            return result
        safe_arguments = redact_sensitive(dict(arguments))
        key = _stable_id(
            "connector-action",
            f"{self.workspace_id}:{request_id}:{run_id}:{action}:{resource_id}",
        )
        try:
            result = self.action_executor(identity, action, resource_id, safe_arguments, key)
        except Exception as error:
            failed = {"error": f"connector adapter failed: {error}", "authorized": True}
            self._audit_action(
                "connector.action_failed",
                external_identity_record_id,
                agent_id,
                requester_actor_id,
                action,
                resource_id,
                request_id,
                run_id,
                failed,
                arguments=safe_arguments,
            )
            return failed
        if not isinstance(result, Mapping):
            failed = {"error": "connector adapter returned an invalid result", "authorized": True}
            self._audit_action(
                "connector.action_failed",
                external_identity_record_id,
                agent_id,
                requester_actor_id,
                action,
                resource_id,
                request_id,
                run_id,
                failed,
                arguments=safe_arguments,
            )
            return failed
        output = {
            "authorized": True,
            "connector_kind": identity["connector_kind"],
            "external_workspace_id": identity["external_workspace_id"],
            "grant_id": grant["connector_grant_id"],
            **dict(redact_sensitive(result)),
        }
        self._audit_action(
            "connector.action_succeeded",
            external_identity_record_id,
            agent_id,
            requester_actor_id,
            action,
            resource_id,
            request_id,
            run_id,
            output,
            arguments=safe_arguments,
        )
        return output

    def ingest_event(
        self,
        *,
        external_identity_record_id: str,
        external_event_id: str,
        external_resource_id: str,
        target_agent_id: str,
        instruction: str,
        payload: Mapping[str, Any],
        event_type: str = "message",
        external_thread_id: str = "",
        destination_conversation_id: Optional[str] = None,
        destination_thread_id: Optional[str] = None,
        requester_actor_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        budget: Optional[WorkBudget] = None,
        priority: int = 0,
    ) -> ConnectorEventSubmission:
        """Persist an inbound event before deterministic authorization/routing."""

        identity = self.repository.get_external_identity(external_identity_record_id)
        if identity is None or identity["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("external identity not found")
        requester_actor_id = str(requester_actor_id or identity["owner_actor_id"]).strip()
        if requester_actor_id != identity["owner_actor_id"]:
            raise ConnectorAuthorityError(
                "inbound connector events must be attributed to the identity owner"
            )
        requester = self._actor(requester_actor_id)
        if requester.kind is not ActorKind.HUMAN:
            raise ConnectorAuthorityError("connector event requester must be a human owner")
        instruction = str(instruction or "").strip()
        if not instruction or len(instruction) > 32_000:
            raise ConnectorAuthorityError("connector event instruction is required and bounded")
        safe_payload = redact_sensitive(dict(payload or {}))
        if budget is None:
            budget = WorkBudget()
        elif not isinstance(budget, WorkBudget):
            budget = WorkBudget.from_dict(budget)
        key = str(idempotency_key or "").strip()
        event_id = _stable_id(
            "connector-event",
            f"{self.workspace_id}:{external_identity_record_id}:{external_event_id}",
        )
        event = self.repository.create_event(
            event_id,
            self.workspace_id,
            external_identity_record_id,
            external_event_id,
            requester_actor_id,
            target_agent_id,
            event_type,
            instruction,
            safe_payload,
            external_thread_id=external_thread_id,
            external_resource_id=external_resource_id,
            destination_conversation_id=destination_conversation_id,
            destination_thread_id=destination_thread_id,
            idempotency_key=key or event_id,
        )
        if event["status"] in {"routed", "completed", "ignored"}:
            existing_request = (
                self.coordinator.work.get_request(event["request_id"])
                if event.get("request_id")
                else None
            )
            return ConnectorEventSubmission(
                event,
                WorkSubmission(existing_request) if existing_request else None,
                replayed=True,
            )

        # Persist the source event before checking the current grant.  An
        # unauthorized, expired, or temporarily unavailable route becomes an
        # explicit ignored record instead of disappearing at the transport
        # boundary.  Replaying the same webhook is then harmless and observable.
        grant = self.repository.effective_grant(
            self.workspace_id,
            external_identity_record_id,
            target_agent_id,
            requester_actor_id,
            external_resource_id,
            "connector.read",
        )
        if grant is None:
            ignored = self.repository.transition_event(
                event_id,
                "ignored",
                error_detail="connector event target has no active read grant for this resource",
            )
            self._emit(
                "connector.event_ignored",
                "connector_event",
                event_id,
                {"reason": ignored.get("error_detail")},
            )
            return ConnectorEventSubmission(ignored, None, replayed=False)

        try:
            context = self.context_factory(target_agent_id, requester)
            request = WorkRequest(
                request_id=_stable_id("request", event_id),
                workspace_id=self.workspace_id,
                source=WorkSource.CONNECTOR,
                source_ref=ResourceRef("connector_event", event_id),
                requester=requester,
                target_agent_ids=(target_agent_id,),
                instruction=instruction,
                idempotency_key=_stable_id("connector-request", event["idempotency_key"]),
                correlation_id=_stable_id("connector-correlation", event_id),
                conversation_id=destination_conversation_id,
                thread_id=destination_thread_id,
                permission_scope={
                    "connector": {
                        "external_identity_record_id": external_identity_record_id,
                        "connector_grant_id": grant["connector_grant_id"],
                        "resource_id": external_resource_id,
                        "action": "connector.read",
                    }
                },
                budget=budget,
                priority=priority,
            )
            submitted = self.coordinator.submit_request(request, {target_agent_id: context})
        except Exception as error:
            failed = self.repository.transition_event(
                event_id, "failed", error_detail=str(error)[:2_000]
            )
            self._emit("connector.event_failed", "connector_event", event_id, {"error": str(error)[:500]})
            raise ConnectorAuthorityError("connector event could not be routed") from error
        routed = self.repository.transition_event(
            event_id, "routed", request_id=submitted.request["request_id"]
        )
        self._emit(
            "connector.event_routed",
            "connector_event",
            event_id,
            {"request_id": submitted.request["request_id"], "delivery_ids": [d["delivery_id"] for d in submitted.deliveries]},
        )
        return ConnectorEventSubmission(routed, submitted)

    def queue_result(
        self, *, run_id: str, text: str, idempotency_key: Optional[str] = None
    ) -> dict[str, Any]:
        """Create a durable outbound item from a successful connector run."""

        run = self.coordinator.work.get_run(run_id)
        if run is None or run["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("connector result run not found")
        request = self.coordinator.work.get_request(run["request_id"])
        if request is None or request["source_kind"] != WorkSource.CONNECTOR.value:
            raise ConnectorAuthorityError("run is not sourced from a connector event")
        event_id = request["source_ref_id"]
        event = self.repository.get_event(event_id)
        if event is None or event["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("connector source event not found")
        identity = self.repository.get_external_identity(event["external_identity_record_id"])
        if identity is None or identity["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("connector source identity not found")
        if event["request_id"] not in {None, request["request_id"]}:
            raise ConnectorAuthorityError("connector event request binding is inconsistent")
        # This is a fresh send check, separate from the read grant used to wake
        # the agent.  Revoke therefore prevents a result from being sent even if
        # the run was already in flight.
        send_grant = self.authorize(
            external_identity_record_id=event["external_identity_record_id"],
            agent_id=event["target_agent_id"],
            requester_actor_id=event["requester_actor_id"],
            resource_id=event["external_resource_id"],
            action="connector.send",
        )
        text = str(text or "").strip()
        if not text or len(text) > 32_000:
            raise ConnectorAuthorityError("connector result text is required and bounded")
        # One inbound connector event has one logical outbound result.  Run IDs
        # are attempt-scoped, so using one here would create a second external
        # send whenever the worker retries after a crash.
        delivery_id = _stable_id(
            "connector-delivery", f"{self.workspace_id}:{event_id}:result"
        )
        outbox = self.repository.create_outbox(
            delivery_id,
            self.workspace_id,
            event_id,
            event["external_identity_record_id"],
            {"text": text},
            external_resource_id=event["external_resource_id"],
            external_thread_id=event["external_thread_id"],
            run_id=run_id,
            idempotency_key=idempotency_key or delivery_id,
        )
        self._emit(
            "connector.outbound_queued",
            "connector_delivery",
            delivery_id,
            self._outbox_audit_payload(
                outbox,
                event,
                identity=identity,
                action="connector.send",
                grant_id=send_grant["connector_grant_id"],
                result={"status": "queued"},
            ),
        )
        return outbox

    def send_outbox(self, delivery_id: str) -> dict[str, Any]:
        """Attempt one outbound item with a durable sending boundary."""

        outbox = self.repository.get_outbox(delivery_id)
        if outbox is None or outbox["workspace_id"] != self.workspace_id:
            raise ConnectorAuthorityError("connector delivery not found")
        if outbox["status"] == "sent":
            return outbox
        if outbox["status"] == "canceled":
            return outbox
        # A process that already owns the sending boundary may still be inside
        # the provider call.  Returning here prevents a concurrent worker from
        # issuing the same external side effect twice.
        if outbox["status"] == "sending":
            return outbox
        event = self.repository.get_event(outbox["connector_event_id"])
        identity = self.repository.get_external_identity(outbox["external_identity_record_id"])
        if event is None or identity is None or identity["status"] != "active":
            failed = self.repository.transition_outbox(
                delivery_id, "failed", last_error="external identity or event is unavailable"
            )
            self._emit(
                "connector.outbound_failed",
                "connector_delivery",
                delivery_id,
                self._outbox_audit_payload(
                    outbox,
                    event,
                    identity=identity,
                    action="connector.send",
                    result={"status": "failed", "error": failed.get("last_error")},
                ),
            )
            return failed
        if identity.get("health_status") in {"offline", "error"}:
            failed = self.repository.transition_outbox(
                delivery_id,
                "failed",
                last_error=f"external connector health is {identity['health_status']}",
            )
            self._emit(
                "connector.outbound_failed",
                "connector_delivery",
                delivery_id,
                self._outbox_audit_payload(
                    outbox,
                    event,
                    identity=identity,
                    action="connector.send",
                    result={"status": "failed", "error": failed.get("last_error")},
                ),
            )
            return failed
        try:
            grant = self.authorize(
                external_identity_record_id=event["external_identity_record_id"],
                agent_id=event["target_agent_id"],
                requester_actor_id=event["requester_actor_id"],
                resource_id=event["external_resource_id"],
                action="connector.send",
            )
        except ConnectorAuthorityError as error:
            failed = self.repository.transition_outbox(delivery_id, "failed", last_error=str(error))
            self._emit(
                "connector.outbound_failed",
                "connector_delivery",
                delivery_id,
                self._outbox_audit_payload(
                    outbox,
                    event,
                    identity=identity,
                    action="connector.send",
                    result={"status": "failed", "error": str(error)},
                ),
            )
            return failed
        if self.outbound_sender is None:
            failed = self.repository.transition_outbox(
                delivery_id, "failed", last_error="connector outbound adapter is unavailable"
            )
            self._emit(
                "connector.outbound_failed",
                "connector_delivery",
                delivery_id,
                self._outbox_audit_payload(
                    outbox,
                    event,
                    identity=identity,
                    action="connector.send",
                    grant_id=grant["connector_grant_id"],
                    result={"status": "failed", "error": failed.get("last_error")},
                ),
            )
            return failed
        claimed = self.repository.claim_outbox(delivery_id)
        if claimed is None:
            # Another worker won the compare-and-set, or the item reached a
            # terminal state between the initial read and this claim.
            current = self.repository.get_outbox(delivery_id)
            if current is None:
                raise ConnectorAuthorityError("connector delivery disappeared before send")
            return current
        if claimed["status"] != "sending":
            return claimed
        outbox = claimed
        try:
            result = self.outbound_sender(
                identity,
                outbox["payload"],
                outbox["external_resource_id"],
                outbox["external_thread_id"],
            )
            if not isinstance(result, Mapping) or result.get("ok") is False:
                error = str((result or {}).get("error") if isinstance(result, Mapping) else "invalid sender result")
                failed = self.repository.transition_outbox(delivery_id, "failed", last_error=error)
                self._emit(
                    "connector.outbound_failed",
                    "connector_delivery",
                    delivery_id,
                    self._outbox_audit_payload(
                        outbox,
                        event,
                        identity=identity,
                        action="connector.send",
                        grant_id=grant["connector_grant_id"],
                        result={"status": "failed", "error": error},
                    ),
                )
                return failed
        except Exception as error:
            detail = str(error)[:2_000]
            failed = self.repository.transition_outbox(delivery_id, "failed", last_error=detail)
            self._emit(
                "connector.outbound_failed",
                "connector_delivery",
                delivery_id,
                self._outbox_audit_payload(
                    outbox,
                    event,
                    identity=identity,
                    action="connector.send",
                    grant_id=grant["connector_grant_id"],
                    result={"status": "failed", "error": detail},
                ),
            )
            return failed
        sent = self.repository.transition_outbox(delivery_id, "sent")
        self.repository.transition_event(event["connector_event_id"], "completed")
        self._emit(
            "connector.outbound_sent",
            "connector_delivery",
            delivery_id,
            self._outbox_audit_payload(
                outbox,
                event,
                identity=identity,
                action="connector.send",
                grant_id=grant["connector_grant_id"],
                result={
                    "status": "sent",
                    "message_id": result.get("message_id") if isinstance(result, Mapping) else None,
                },
            ),
        )
        return sent

    def list_events(self, *, status: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        self._require_human_requester()
        return self.repository.list_events(self.workspace_id, status=status, limit=limit)

    def list_outbox(self, *, statuses: Sequence[str] = (), limit: int = 100) -> list[dict[str, Any]]:
        self._require_human_requester()
        return self.repository.list_outbox(self.workspace_id, statuses=statuses, limit=limit)

    def _require_human_requester(self) -> None:
        actor = self._actor(self.actor_id)
        if actor.kind is not ActorKind.HUMAN:
            raise ConnectorAuthorityError("only a human Team requester may manage connectors")

    def _actor(self, actor_id: str) -> ActorRef:
        with self.repository.database._lock:
            row = self.repository.database._conn.execute(
                "SELECT actor_id, actor_kind, display_name FROM actors WHERE actor_id = ?",
                (str(actor_id).strip(),),
            ).fetchone()
        if row is None:
            raise ConnectorAuthorityError("connector actor identity is missing")
        return ActorRef(row["actor_id"], ActorKind(row["actor_kind"]), row["display_name"])

    def _emit(self, event_type: str, aggregate_type: str, aggregate_id: str, payload: Mapping[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink({
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "payload": dict(redact_sensitive(payload)),
            })

    def _audit_action(
        self,
        event_type: str,
        identity_id: str,
        agent_id: str,
        requester_actor_id: str,
        action: str,
        resource_id: str,
        request_id: str,
        run_id: str,
        result: Mapping[str, Any],
        *,
        arguments: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._emit(
            event_type,
            "external_identity",
            identity_id,
            {
                "requester_actor_id": requester_actor_id,
                "agent_id": agent_id,
                "external_identity_record_id": identity_id,
                "action": action,
                "resource_id": resource_id,
                "request_id": request_id,
                "run_id": run_id,
                "arguments": dict(arguments or {}),
                "result": dict(result),
            },
        )

    @staticmethod
    def _outbox_audit_payload(
        outbox: Mapping[str, Any],
        event: Optional[Mapping[str, Any]],
        *,
        identity: Optional[Mapping[str, Any]],
        action: str,
        result: Mapping[str, Any],
        grant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Build an audit-safe outbound event without message/credential bodies."""

        event = event or {}
        identity = identity or {}
        return {
            "requester_actor_id": event.get("requester_actor_id"),
            "agent_id": event.get("target_agent_id"),
            "external_identity_record_id": outbox.get("external_identity_record_id"),
            "connector_kind": identity.get("connector_kind"),
            "external_workspace_id": identity.get("external_workspace_id"),
            "resource_id": outbox.get("external_resource_id"),
            "external_thread_id": outbox.get("external_thread_id"),
            "action": action,
            "grant_id": grant_id,
            "run_id": outbox.get("run_id"),
            "result": dict(result),
        }


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:32]}"
