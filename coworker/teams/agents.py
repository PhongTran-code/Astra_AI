"""Persistent custom-agent lifecycle service.

Agent definitions and revisions are immutable inputs to runs.  The service
updates an installation's active revision for future work while preserving the
revision id already captured by an in-flight run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from .contracts import (
    ActorKind,
    ActorRef,
    AgentRole,
    AgentStatus,
    ContractError,
    DomainIdPrefix,
    new_domain_id,
    normalize_agent_avatar_id,
)
from .permissions import redact_sensitive, review_capability_install
from .persistence import AgentRepository, IdentityRepository
from .sandbox import SandboxExecutionResult, SandboxManager


class AgentLifecycleError(ContractError):
    """A custom-agent lifecycle command cannot be completed."""


@dataclass(frozen=True)
class AgentImportReview:
    installed: bool
    requires_approval: bool
    agent_id: Optional[str]
    reasons: tuple[str, ...] = ()
    package: Optional[dict[str, Any]] = None


class AgentLifecycleService:
    """Create and manage persistent agents without bypassing sandbox governance."""

    def __init__(
        self,
        identities: IdentityRepository,
        agents: AgentRepository,
        sandboxes: SandboxManager,
        *,
        computers: Optional[Any] = None,
    ) -> None:
        self.identities = identities
        self.agents = agents
        self.sandboxes = sandboxes
        self.computers = computers

    def create(
        self,
        *,
        workspace_id: str,
        requester: ActorRef,
        display_name: str,
        handle: str,
        instructions: str,
        base_role: AgentRole | str = AgentRole.BUILDER,
        description: str = "",
        avatar_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_actor_id: Optional[str] = None,
        definition_id: Optional[str] = None,
        revision_id: Optional[str] = None,
        model_policy: Optional[Mapping[str, Any]] = None,
        sandbox_policy: Optional[Mapping[str, Any]] = None,
        memory_policy: Optional[Mapping[str, Any]] = None,
        reply_policy: Optional[Mapping[str, Any]] = None,
        budget_policy: Optional[Mapping[str, Any]] = None,
        approval_policy: Optional[Mapping[str, Any]] = None,
        capabilities: Sequence[Mapping[str, Any]] = (),
        max_concurrency: int = 1,
    ) -> dict[str, Any]:
        self._require_human(requester)
        workspace_id = _required_text(workspace_id, "workspace id")
        role = _as_role(base_role)
        agent_id = agent_id or new_domain_id(DomainIdPrefix.AGENT)
        agent_actor_id = agent_actor_id or new_domain_id(DomainIdPrefix.ACTOR)
        definition_id = definition_id or new_domain_id(DomainIdPrefix.AGENT_DEFINITION)
        revision_id = revision_id or new_domain_id(DomainIdPrefix.AGENT_REVISION)
        actor = ActorRef(agent_actor_id, ActorKind.AGENT, display_name)
        self.identities.create_actor(actor, handle=handle)
        self.identities.add_membership(
            new_domain_id(DomainIdPrefix.MEMBERSHIP),
            workspace_id,
            agent_actor_id,
            "member",
        )
        self.agents.create_definition(
            definition_id,
            workspace_id,
            requester.id,
            display_name,
            handle,
            role.value,
            description=description,
            avatar_id=avatar_id,
        )
        self.agents.create_revision(
            revision_id,
            definition_id,
            1,
            instructions,
            requester.id,
            model_policy=model_policy,
            sandbox_policy=sandbox_policy,
            memory_policy=memory_policy,
            reply_policy=reply_policy,
            budget_policy=budget_policy,
            approval_policy=approval_policy,
            capabilities=capabilities,
        )
        installation = self.agents.install(
            agent_id,
            workspace_id,
            agent_actor_id,
            definition_id,
            revision_id,
            status=AgentStatus.PROVISIONING,
            max_concurrency=max_concurrency,
        )
        sandbox_id = new_domain_id(DomainIdPrefix.SANDBOX)
        try:
            sandbox = self.sandboxes.provision(sandbox_id, workspace_id, agent_id)
            ready = self.agents.set_status(agent_id, AgentStatus.READY)
        except Exception as error:
            # Durable identity is retained as an explicit failed provisioning
            # record, so a restart/operator can diagnose and retry it; callers
            # never see a half-created agent pretending to be ready.
            try:
                self.agents.set_status(agent_id, AgentStatus.ERROR)
            except Exception:
                pass
            raise AgentLifecycleError("agent sandbox provisioning failed") from error
        return self._snapshot(ready, sandbox=sandbox)

    def edit(
        self,
        agent_id: str,
        *,
        requester: ActorRef,
        instructions: Optional[str] = None,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
        description: Optional[str] = None,
        avatar_id: Optional[str] = None,
        model_policy: Optional[Mapping[str, Any]] = None,
        sandbox_policy: Optional[Mapping[str, Any]] = None,
        memory_policy: Optional[Mapping[str, Any]] = None,
        reply_policy: Optional[Mapping[str, Any]] = None,
        budget_policy: Optional[Mapping[str, Any]] = None,
        approval_policy: Optional[Mapping[str, Any]] = None,
        capabilities: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> dict[str, Any]:
        self._require_human(requester)
        installation = self._require_installation(agent_id)
        definition = self.agents.get_definition(installation["agent_definition_id"])
        current = self.agents.get_revision(installation["active_revision_id"])
        if definition is None or current is None:
            raise AgentLifecycleError("agent definition or active revision is missing")
        updated_identity = None
        if display_name is not None or handle is not None or description is not None or avatar_id is not None:
            definition = self.agents.update_identity(
                agent_id,
                owner_actor_id=requester.id,
                display_name=display_name,
                handle=handle,
                description=description,
                avatar_id=avatar_id,
            )
            updated_identity = {
                "actor_id": installation["actor_id"],
                "display_name": definition["display_name"],
                "handle": definition["handle"],
                "description": definition["description"],
                "avatar_id": definition["avatar_id"],
            }
        normalized_instructions = instructions if instructions is not None else current["instructions"]
        policies = {
            "model_policy": current["model_policy"] if model_policy is None else model_policy,
            "sandbox_policy": current["sandbox_policy"] if sandbox_policy is None else sandbox_policy,
            "memory_policy": current["memory_policy"] if memory_policy is None else memory_policy,
            "reply_policy": current["reply_policy"] if reply_policy is None else reply_policy,
            "budget_policy": current["budget_policy"] if budget_policy is None else budget_policy,
            "approval_policy": current["approval_policy"] if approval_policy is None else approval_policy,
        }
        next_capabilities = (
            capabilities
            if capabilities is not None
            else tuple(
                {
                    "capability_kind": item["capability_kind"],
                    "capability_ref": item["capability_ref"],
                    "required": bool(item.get("required", False)),
                    "config": item.get("config", {}),
                }
                for item in current["capabilities"]
            )
        )
        revision_id = new_domain_id(DomainIdPrefix.AGENT_REVISION)
        revision = self.agents.create_revision(
            revision_id,
            definition["agent_definition_id"],
            int(current["revision_number"]) + 1,
            normalized_instructions,
            requester.id,
            model_policy=policies["model_policy"],
            sandbox_policy=policies["sandbox_policy"],
            memory_policy=policies["memory_policy"],
            reply_policy=policies["reply_policy"],
            budget_policy=policies["budget_policy"],
            approval_policy=policies["approval_policy"],
            capabilities=next_capabilities,
        )
        revision = self.agents.get_revision(revision_id) or revision
        updated = self.agents.activate_revision(agent_id, revision_id)
        return {"installation": updated, "revision": revision, "identity": updated_identity if (display_name is not None or handle is not None or description is not None or avatar_id is not None) else None, "definition": definition}

    def clone(
        self,
        agent_id: str,
        *,
        requester: ActorRef,
        display_name: Optional[str] = None,
        handle: Optional[str] = None,
    ) -> dict[str, Any]:
        self._require_human(requester)
        installation = self._require_installation(agent_id)
        definition = self.agents.get_definition(installation["agent_definition_id"])
        revision = self.agents.get_revision(installation["active_revision_id"])
        if definition is None or revision is None:
            raise AgentLifecycleError("agent definition or active revision is missing")
        return self.create(
            workspace_id=installation["workspace_id"],
            requester=requester,
            display_name=display_name or definition["display_name"],
            handle=handle or f"{definition['handle']}-copy",
            instructions=revision["instructions"],
            base_role=definition["base_role"],
            description=definition["description"],
            avatar_id=definition["avatar_id"],
            model_policy=revision["model_policy"],
            sandbox_policy=revision["sandbox_policy"],
            memory_policy=revision["memory_policy"],
            reply_policy=revision["reply_policy"],
            budget_policy=revision["budget_policy"],
            approval_policy=revision["approval_policy"],
            capabilities=revision["capabilities"],
            max_concurrency=installation["max_concurrency"],
        )

    def export(self, agent_id: str) -> dict[str, Any]:
        installation = self._require_installation(agent_id)
        definition = self.agents.get_definition(installation["agent_definition_id"])
        revision = self.agents.get_revision(installation["active_revision_id"])
        sandbox = self.sandboxes.repository.get_sandbox_for_agent(agent_id) if hasattr(self.sandboxes.repository, "get_sandbox_for_agent") else None
        if definition is None or revision is None:
            raise AgentLifecycleError("agent definition or active revision is missing")
        capabilities = []
        for capability in revision["capabilities"]:
            capabilities.append(
                redact_sensitive(
                    {
                        "capability_kind": capability["capability_kind"],
                        "capability_ref": capability["capability_ref"],
                        "required": bool(capability["required"]),
                        "config": capability.get("config", {}),
                    }
                )
            )
        return {
            "format": "astra-agent-package",
            "version": 1,
            "definition": {
                "display_name": definition["display_name"],
                "handle": definition["handle"],
                "description": definition["description"],
                "avatar_id": definition["avatar_id"],
                "base_role": definition["base_role"],
            },
            "revision": {
                "instructions": revision["instructions"],
                "model_policy": redact_sensitive(revision["model_policy"]),
                "sandbox_policy": redact_sensitive(revision["sandbox_policy"]),
                "memory_policy": redact_sensitive(revision["memory_policy"]),
                "reply_policy": redact_sensitive(revision["reply_policy"]),
                "budget_policy": redact_sensitive(revision["budget_policy"]),
                "approval_policy": redact_sensitive(revision["approval_policy"]),
                "capabilities": capabilities,
            },
            "installation": {"max_concurrency": installation["max_concurrency"]},
            "sandbox": {
                "isolation_level": sandbox["isolation_level"] if sandbox else "unknown",
                "mounts": [
                    {"target_path": mount["target_path"], "access_mode": mount["access_mode"]}
                    for mount in (sandbox or {}).get("mounts", [])
                ],
            },
        }

    def import_package(
        self,
        package: Mapping[str, Any],
        *,
        workspace_id: str,
        requester: ActorRef,
        human_approved: bool = False,
        handle: Optional[str] = None,
    ) -> AgentImportReview:
        self._require_human(requester)
        normalized = _validate_package(package)
        capabilities = normalized["revision"]["capabilities"]
        reviews = [
            review_capability_install(
                requester,
                item["capability_kind"],
                item["capability_ref"],
                human_approved=human_approved,
            )
            for item in capabilities
        ]
        denied = tuple(review.reason for review in reviews if not review.allowed)
        if denied:
            return AgentImportReview(False, True, None, denied, dict(normalized))
        definition = normalized["definition"]
        revision = normalized["revision"]
        created = self.create(
            workspace_id=workspace_id,
            requester=requester,
            display_name=definition["display_name"],
            handle=handle or definition["handle"],
            instructions=revision["instructions"],
            base_role=definition["base_role"],
            description=definition["description"],
            avatar_id=definition["avatar_id"],
            model_policy=revision["model_policy"],
            sandbox_policy=revision["sandbox_policy"],
            memory_policy=revision["memory_policy"],
            reply_policy=revision["reply_policy"],
            budget_policy=revision["budget_policy"],
            approval_policy=revision["approval_policy"],
            capabilities=capabilities,
            max_concurrency=normalized["installation"]["max_concurrency"],
        )
        return AgentImportReview(True, False, created["agent_id"], package=dict(normalized))

    def test_playground(
        self,
        agent_id: str,
        command: Sequence[str],
        *,
        environment: Optional[Mapping[str, str]] = None,
        resource_policy: Optional[Mapping[str, Any]] = None,
    ) -> SandboxExecutionResult:
        installation = self._require_installation(agent_id)
        sandbox = self._sandbox_for_agent(agent_id)
        return self.sandboxes.execute(
            sandbox["sandbox_id"],
            new_domain_id(DomainIdPrefix.RUN),
            command,
            environment=environment,
            resource_policy=resource_policy,
        )

    def sandbox_health(self, agent_id: str) -> dict[str, Any]:
        """Return the provider's safe health summary for one agent sandbox."""

        sandbox = self._sandbox_for_agent(agent_id)
        return {
            "agent_id": agent_id,
            "sandbox_id": sandbox["sandbox_id"],
            **dict(self.sandboxes.health(sandbox["sandbox_id"])),
        }

    def pause(self, agent_id: str) -> dict[str, Any]:
        installation = self._require_installation(agent_id)
        if installation["status"] == AgentStatus.PAUSED.value:
            return installation
        self.sandboxes.suspend(self._sandbox_for_agent(agent_id)["sandbox_id"])
        try:
            return self.agents.set_status(agent_id, AgentStatus.PAUSED)
        except ContractError as error:
            raise AgentLifecycleError(str(error)) from error

    def stop(self, agent_id: str) -> dict[str, Any]:
        """Stop new work and suspend the sandbox; callers cancel live runs first."""

        return self.pause(agent_id)

    def resume(self, agent_id: str) -> dict[str, Any]:
        installation = self._require_installation(agent_id)
        if installation["status"] == AgentStatus.READY.value:
            return installation
        sandbox = self._sandbox_for_agent(agent_id)
        if installation["status"] == AgentStatus.ERROR.value or sandbox["status"] == "error":
            return self.restart(agent_id)
        self.sandboxes.resume(sandbox["sandbox_id"])
        try:
            return self.agents.set_status(agent_id, AgentStatus.READY)
        except ContractError as error:
            raise AgentLifecycleError(str(error)) from error

    def restart(self, agent_id: str) -> dict[str, Any]:
        """Recover the sandbox and installation through provisioning when needed."""

        installation = self._require_installation(agent_id)
        if installation["status"] in {
            AgentStatus.WORKING.value,
            AgentStatus.WAITING.value,
        }:
            raise AgentLifecycleError("stop the agent before restarting an active run")
        sandbox = self.sandboxes.restart(self._sandbox_for_agent(agent_id)["sandbox_id"])
        current = AgentStatus(installation["status"])
        try:
            if current in {AgentStatus.ARCHIVED, AgentStatus.ERROR}:
                self.agents.set_status(agent_id, AgentStatus.PROVISIONING)
            updated = self.agents.set_status(agent_id, AgentStatus.READY)
        except ContractError as error:
            raise AgentLifecycleError(str(error)) from error
        return {**updated, "sandbox_id": sandbox["sandbox_id"], "sandbox_status": sandbox["status"]}

    def archive(self, agent_id: str) -> dict[str, Any]:
        self._require_installation(agent_id)
        sandbox = self._sandbox_for_agent(agent_id)
        self.sandboxes.archive(sandbox["sandbox_id"])
        try:
            return self.agents.set_status(agent_id, AgentStatus.ARCHIVED)
        except ContractError as error:
            raise AgentLifecycleError(str(error)) from error

    def reset(self, agent_id: str) -> dict[str, Any]:
        self._require_installation(agent_id)
        sandbox = self._sandbox_for_agent(agent_id)
        if self.computers is not None:
            try:
                computer = self.computers.repository.get_for_agent(agent_id)
                if computer is not None:
                    self.computers.provider.destroy(computer["computer_id"])
            except Exception as err:
                logger.warning("Failed to reset computer container for agent %s: %s", agent_id, err)
        return self.sandboxes.reset(sandbox["sandbox_id"])

    def delete(self, agent_id: str, *, requester: Optional[ActorRef] = None) -> dict[str, Any]:
        """Soft-delete an archived agent while retaining historical references."""

        installation = self._require_installation(agent_id)
        if installation["status"] != AgentStatus.ARCHIVED.value:
            raise AgentLifecycleError("agent must be archived before deletion")
        sandbox = self._sandbox_for_agent(agent_id)
        if sandbox["status"] != "archived":
            raise AgentLifecycleError("agent sandbox must be archived before deletion")
        definition = self.agents.get_definition(installation["agent_definition_id"])
        if definition is None:
            raise AgentLifecycleError("agent definition is missing")
        if requester is not None:
            self._require_human(requester)
            if requester.id != definition["owner_actor_id"]:
                raise AgentLifecycleError("only the agent owner can delete it")
        return self.agents.mark_deleted(
            agent_id,
            requested_by_actor_id=str(definition["owner_actor_id"]),
        )

    def _sandbox_for_agent(self, agent_id: str) -> dict[str, Any]:
        sandboxes = self.sandboxes.repository.list_for_agent(agent_id)
        if not sandboxes:
            raise AgentLifecycleError("agent sandbox is missing")
        return sandboxes[0]

    def _require_installation(self, agent_id: str) -> dict[str, Any]:
        installation = self.agents.get_installation(agent_id)
        if installation is None:
            raise AgentLifecycleError(f"agent installation not found: {agent_id}")
        if self.agents.is_deleted(agent_id):
            raise AgentLifecycleError(f"agent installation is deleted: {agent_id}")
        return installation

    def _snapshot(self, installation: dict[str, Any], *, sandbox: dict[str, Any]) -> dict[str, Any]:
        return {
            "agent_id": installation["agent_id"],
            "agent_actor_id": installation["actor_id"],
            "agent_definition_id": installation["agent_definition_id"],
            "active_revision_id": installation["active_revision_id"],
            "status": installation["status"],
            "sandbox_id": sandbox["sandbox_id"],
            "isolation_level": sandbox["isolation_level"],
        }

    @staticmethod
    def _require_human(requester: ActorRef) -> None:
        if not isinstance(requester, ActorRef) or requester.kind is not ActorKind.HUMAN:
            raise AgentLifecycleError("only a human can manage persistent agents")


def _as_role(value: AgentRole | str) -> AgentRole:
    try:
        return value if isinstance(value, AgentRole) else AgentRole(value)
    except (TypeError, ValueError) as error:
        raise AgentLifecycleError("base role is not supported") from error


def _required_text(value: Any, label: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise AgentLifecycleError(f"{label} is required")
    return value


def _validate_package(package: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(package, Mapping) or package.get("format") != "astra-agent-package":
        raise AgentLifecycleError("agent package format is unsupported")
    definition = package.get("definition")
    revision = package.get("revision")
    installation = package.get("installation") or {}
    if not isinstance(definition, Mapping) or not isinstance(revision, Mapping):
        raise AgentLifecycleError("agent package is missing definition or revision")
    role = _as_role(definition.get("base_role"))
    capabilities = revision.get("capabilities") or []
    if not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes)):
        raise AgentLifecycleError("agent package capabilities must be a list")
    normalized_caps = []
    for item in capabilities:
        if not isinstance(item, Mapping):
            raise AgentLifecycleError("agent package capability is malformed")
        normalized_caps.append(
            {
                "capability_kind": _required_text(item.get("capability_kind"), "capability kind"),
                "capability_ref": _required_text(item.get("capability_ref"), "capability reference"),
                "required": bool(item.get("required", False)),
                "config": item.get("config") or {},
            }
        )
    max_concurrency = installation.get("max_concurrency", 1)
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency <= 0:
        raise AgentLifecycleError("agent package max_concurrency must be positive")
    return {
        "format": "astra-agent-package",
        "version": package.get("version", 1),
        "definition": {
            "display_name": _required_text(definition.get("display_name"), "display name"),
            "handle": _required_text(definition.get("handle"), "agent handle"),
            "description": str(definition.get("description") or "").strip(),
            "avatar_id": normalize_agent_avatar_id(definition.get("avatar_id")),
            "base_role": role.value,
        },
        "revision": {
            "instructions": _required_text(revision.get("instructions"), "agent instructions"),
            "model_policy": revision.get("model_policy") or {},
            "sandbox_policy": revision.get("sandbox_policy") or {},
            "memory_policy": revision.get("memory_policy") or {},
            "reply_policy": revision.get("reply_policy") or {},
            "budget_policy": revision.get("budget_policy") or {},
            "approval_policy": revision.get("approval_policy") or {},
            "capabilities": normalized_caps,
        },
        "installation": {"max_concurrency": max_concurrency},
    }
