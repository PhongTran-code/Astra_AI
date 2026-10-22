"""Capability-bound tools for persistent Team agents.

Persistent agents do not inherit the desktop persona toolset.  A revision names
the exact built-in capabilities it is allowed to expose, and this module binds
only those capabilities to the agent's own sandbox.  Unknown capabilities fail
closed when required; optional capabilities are omitted and reported to the
agent instead of silently becoming something else.
"""

from __future__ import annotations

import os
import tempfile
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .sandbox import SandboxManager, SandboxPolicyViolation
from .computer import HumanTakeoverRequired
from .secrets import (
    EphemeralSecretBroker,
    SandboxSecretRef,
    SecretBrokerError,
    redact_secret_output,
)
from .permissions import (
    AGENT_ROLE_ACTIONS,
    WORKSPACE_ROLE_ACTIONS,
    PermissionContext,
    SandboxPolicy,
    authorize_action,
)


class CapabilityBindingError(ValueError):
    """A required Team capability cannot be safely bound for this run."""


_TOOL_ALIASES = {
    "shell": "run_shell",
    "sandbox_exec": "run_shell",
    "search": "grep",
    "send_team_message": "team_send_message",
    "team_message": "team_send_message",
    "handoff": "team_handoff",
    "team_delegate": "team_handoff",
    "ask_teammate": "team_ask_teammate",
    "team_ask_teammate": "team_ask_teammate",
    "blackboard": "team_blackboard",
    "team_blackboard": "team_blackboard",
    "patch": "apply_patch",
    "diff": "apply_unified_diff",
    "replace": "replace_in_file",
    "browser": "computer",
    "computer_browser": "computer",
}
_TOOL_ACTIONS = {
    "read_file": "sandbox.read",
    "list_files": "sandbox.read",
    "grep": "sandbox.read",
    "write_file": "sandbox.write",
    "replace_in_file": "sandbox.write",
    "apply_patch": "sandbox.write",
    "apply_unified_diff": "sandbox.write",
    "run_shell": "run.execute",
    "git_status": "sandbox.read",
    "git_diff": "sandbox.read",
    "git_log": "sandbox.read",
    "todo_write": "sandbox.write",
    "web_search": "sandbox.read",
    "web_fetch": "sandbox.read",
    "team_send_message": "conversation.send",
    "team_handoff": "task.assign",
    "team_ask_teammate": "conversation.send",
    "team_blackboard": "sandbox.read",
}
_COMPUTER_REFS = frozenset(
    {
        "computer",
        "computer_observe",
        "computer_screenshot",
        "computer_navigate",
        "computer_action",
        "computer_checkpoint",
        "computer_request_takeover",
    }
)
_COMPUTER_CONTROL_REFS = frozenset(
    {"computer", "computer_navigate", "computer_action", "computer_request_takeover"}
)
_SANDBOX_ACTIONS = frozenset(
    {"sandbox.read", "sandbox.write", "sandbox.mount", "sandbox.network", "run.execute"}
)
_CONNECTOR_ACTIONS = frozenset(
    {"connector.read", "connector.send", "connector.write"}
)

_RESOURCE_KEYS = frozenset(
    {
        "max_wall_seconds",
        "max_output_bytes",
        "max_processes",
        "cpu_millis",
        "memory_mib",
        "disk_mib",
        "network_policy",
        "network_domains",
    }
)
_DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_PATH_CHARS = 4096


def _tool_metadata(**kwargs: Any) -> Any:
    """Load aisuite only when a Team tool is actually being bound."""
    try:
        import aisuite as ai
    except ImportError as error:
        raise CapabilityBindingError(
            "aisuite is required to bind an executable Team capability"
        ) from error
    return ai.ToolMetadata(**kwargs)


def build_team_tools(
    capabilities: Sequence[Mapping[str, Any]],
    *,
    sandbox: Mapping[str, Any],
    sandbox_policy: Mapping[str, Any] | None,
    sandbox_runtime: SandboxManager,
    secret_broker: EphemeralSecretBroker | None = None,
    run_id: str,
    permission_context: PermissionContext,
    team_service: Any | None = None,
    connector_service: Any | None = None,
    computer_manager: Any | None = None,
    computer_session_id: str | None = None,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
    task_id: str | None = None,
    parent_handoff_id: str | None = None,
) -> tuple[list[Any], tuple[str, ...], dict[str, Any]]:
    """Return ``(tools, unavailable_optional, effective_policy)`` for one run.

    The returned callables close over the immutable dispatch snapshot and the
    run's sandbox.  They never receive a workspace root or host environment from
    the caller, and they cannot address another agent's sandbox by id.
    """

    policy = _sandbox_policy(sandbox_policy)
    resource_policy = _resource_policy(sandbox, sandbox_policy, policy)
    secret_refs = _secret_references(sandbox_policy)
    if secret_refs and secret_broker is None:
        raise CapabilityBindingError(
            "sandbox secret references require an ephemeral Team secret broker"
        )
    if not isinstance(permission_context, PermissionContext):
        raise CapabilityBindingError("Team permission context is required")
    redactor = (
        (lambda value: secret_broker.redact_for_run(run_id, value))
        if secret_broker is not None and secret_refs
        else None
    )
    tools: list[Any] = []
    unavailable: list[str] = []
    seen: set[str] = set()

    for item in capabilities or ():
        if not isinstance(item, Mapping):
            raise CapabilityBindingError("agent capability is malformed")
        kind = str(item.get("capability_kind") or "").strip().lower()
        raw_ref = str(item.get("capability_ref") or "").strip()
        ref = _normalise_ref(raw_ref)
        required = bool(item.get("required", False))
        config = item.get("config") or {}
        if not isinstance(config, Mapping):
            raise CapabilityBindingError(f"capability config is malformed: {raw_ref}")

        if kind == "connector":
            identity_id = str(config.get("external_identity_record_id") or "").strip()
            if not identity_id or not ref:
                if required:
                    raise CapabilityBindingError(
                        "required connector capability needs a ref and external_identity_record_id"
                    )
                unavailable.append(f"connector:{raw_ref} (identity scope unavailable)")
                continue
            raw_actions = config.get("actions", config.get("allowed_actions"))
            if raw_actions is None:
                raw_actions = ("connector.read",)
            if isinstance(raw_actions, (str, bytes)) or not isinstance(raw_actions, Sequence):
                raise CapabilityBindingError(
                    f"connector capability actions are malformed: {raw_ref}"
                )
            actions = tuple(str(action or "").strip() for action in raw_actions)
            if not actions or len(set(actions)) != len(actions) or any(
                action not in _CONNECTOR_ACTIONS for action in actions
            ):
                raise CapabilityBindingError(
                    f"connector capability actions are unsupported: {raw_ref}"
                )
            connector_runtime = connector_service or getattr(team_service, "connectors", None)
            if connector_runtime is None or not agent_id or not request_id:
                if required:
                    raise CapabilityBindingError(
                        "connector capability requires a worker request context"
                    )
                unavailable.append(f"connector:{raw_ref} (worker scope unavailable)")
                continue
            role_actions = AGENT_ROLE_ACTIONS.get(permission_context.agent_role, frozenset())
            workspace_actions = WORKSPACE_ROLE_ACTIONS.get(
                permission_context.requester_role, frozenset()
            )
            denied = [
                action
                for action in actions
                if action not in role_actions or action not in workspace_actions
            ]
            if denied:
                if required:
                    raise CapabilityBindingError(
                        f"connector capability is outside role authority: {', '.join(denied)}"
                    )
                unavailable.append(
                    f"connector:{raw_ref} (role denied: {', '.join(denied)})"
                )
                continue
            seen_key = f"connector:{identity_id}"
            if seen_key in seen:
                continue
            seen.add(seen_key)
            tools.append(
                _connector_action_tool(
                    connector_runtime,
                    identity_id,
                    tuple(actions),
                    agent_id,
                    permission_context.requester.id,
                    request_id,
                    run_id,
                    ref,
                )
            )
            continue

        if kind == "computer" or (kind == "tool" and ref in _COMPUTER_REFS):
            if computer_manager is None or not computer_session_id:
                if required:
                    raise CapabilityBindingError(
                        "required Computer capability has no active Computer session"
                    )
                unavailable.append(f"computer:{raw_ref} (session unavailable)")
                continue
            action = (
                "computer.control"
                if ref in _COMPUTER_CONTROL_REFS or kind == "computer"
                else "computer.read"
            )
            authority = authorize_action(permission_context, action)
            if not authority.allowed:
                if required:
                    raise CapabilityBindingError(
                        f"Computer capability is outside the agent role authority: {authority.reason}"
                    )
                unavailable.append(f"computer:{raw_ref} (agent role denied)")
                continue
            seen_key = f"computer:{computer_session_id}"
            if seen_key in seen:
                continue
            seen.add(seen_key)
            tools.extend(
                _computer_tools(
                    computer_manager,
                    computer_session_id,
                    agent_id=str(agent_id or ""),
                    agent_actor_id=str(permission_context.agent_actor_id or ""),
                    workspace_id=permission_context.workspace_id,
                    run_id=run_id,
                    control=action == "computer.control",
                    refs=_COMPUTER_REFS if kind == "computer" else {ref},
                )
            )
            continue

        if kind != "tool" or ref not in _TOOL_ACTIONS:
            if required:
                raise CapabilityBindingError(
                    f"required capability is unavailable: {kind}:{raw_ref}"
                )
            unavailable.append(f"{kind}:{raw_ref}")
            continue
        if ref in seen:
            continue
        seen.add(ref)
        action = _TOOL_ACTIONS[ref]
        if action in _SANDBOX_ACTIONS and action not in policy.actions:
            if required:
                raise CapabilityBindingError(
                    f"capability {raw_ref} requires sandbox action {action}"
                )
            unavailable.append(f"tool:{raw_ref} (sandbox action denied)")
            continue
        authority = authorize_action(permission_context, action)
        if not authority.allowed:
            if required:
                raise CapabilityBindingError(
                    f"capability {raw_ref} is outside the agent role authority: "
                    f"{authority.reason}"
                )
            unavailable.append(f"tool:{raw_ref} (agent role denied)")
            continue

        if ref == "read_file":
            from ..tools.files import file_tools

            tools.extend(file_tools(str(sandbox["home_ref"]), redactor=redactor))
        elif ref == "list_files":
            import aisuite as ai
            fs_tools = ai.toolkits.files(root=str(sandbox["home_ref"]), allow_write=False)
            for t in fs_tools:
                if getattr(t, "__name__", "") == "list_files":
                    tools.append(t)
                    break
        elif ref == "grep":
            from ..tools.search import search_tools

            tools.extend(search_tools(str(sandbox["home_ref"]), redactor=redactor))
        elif ref == "write_file":
            tools.append(_write_file_tool(str(sandbox["home_ref"]), config))
        elif ref in {"replace_in_file", "apply_patch", "apply_unified_diff"}:
            import aisuite as ai
            fs_tools = ai.toolkits.files(root=str(sandbox["home_ref"]), allow_write=True)
            for t in fs_tools:
                if getattr(t, "__name__", "") == ref:
                    tools.append(t)
                    break
        elif ref in {"git_status", "git_diff"}:
            import aisuite as ai
            git_tools_list = ai.toolkits.git(root=str(sandbox["home_ref"]))
            for t in git_tools_list:
                if getattr(t, "__name__", "") == ref:
                    tools.append(t)
                    break
        elif ref == "git_log":
            from ..tools.git import git_tools
            for t in git_tools(str(sandbox["home_ref"])):
                if getattr(t, "__name__", "") == "git_log":
                    tools.append(t)
                    break
        elif ref == "todo_write":
            from ..tools.todo import todo_tools
            from ..agent import TodoList
            tools.extend(todo_tools(TodoList()))
        elif ref == "team_blackboard":
            tools.append(
                _team_blackboard_tool(
                    team_service,
                    scope_id=str(task_id or conversation_id or request_id or run_id),
                    author=str(agent_id or "agent"),
                )
            )
        elif ref == "run_shell":
            tools.append(
                _run_shell_tool(
                    sandbox_runtime,
                    str(sandbox["sandbox_id"]),
                    run_id,
                    resource_policy,
                    secret_broker,
                    secret_refs,
                )
            )
        elif ref == "web_search":
            from ..web import make_web_search_tool

            tools.append(make_web_search_tool())
        elif ref == "web_fetch":
            from ..web import make_web_fetch_tool

            tools.append(make_web_fetch_tool())
        elif ref == "team_send_message":
            if not team_service or not conversation_id or not agent_id or not request_id:
                if required:
                    raise CapabilityBindingError(
                        "team_send_message requires a conversation-scoped worker context"
                    )
                unavailable.append("tool:" + raw_ref + " (conversation scope unavailable)")
                continue
            tools.append(
                _team_send_message_tool(
                    team_service,
                    conversation_id,
                    agent_id,
                    request_id,
                    run_id,
                    str(permission_context.correlation_id or request_id),
                )
            )
        elif ref == "team_handoff":
            if not team_service or not task_id or not agent_id or not request_id:
                if required:
                    raise CapabilityBindingError(
                        "team_handoff requires a task-scoped worker context"
                    )
                unavailable.append("tool:" + raw_ref + " (task scope unavailable)")
                continue
            tools.append(
                _team_handoff_tool(
                    team_service,
                    task_id,
                    agent_id,
                    request_id,
                    run_id,
                    str(permission_context.correlation_id or request_id),
                    parent_handoff_id,
                )
            )
        elif ref == "team_ask_teammate":
            if not team_service or not agent_id:
                if required:
                    raise CapabilityBindingError("team_ask_teammate requires an active worker context")
                unavailable.append("tool:" + raw_ref + " (worker scope unavailable)")
                continue
            tools.append(
                _team_ask_teammate_tool(
                    team_service,
                    current_agent_id=agent_id,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    task_id=task_id,
                )
            )

    effective = {
        "actions": sorted(policy.actions),
        "resource_policy": dict(resource_policy),
        "tools": [getattr(tool, "__name__", "") for tool in tools],
        "secret_environment_names": sorted({item.environment for item in secret_refs}),
    }
    return tools, tuple(unavailable), effective


def _computer_tools(
    computer_manager: Any,
    session_id: str,
    *,
    agent_id: str,
    agent_actor_id: str,
    workspace_id: str,
    run_id: str,
    control: bool,
    refs: Sequence[str],
) -> list[Any]:
    """Bind Computer operations to one durable run session.

    The model never chooses a Computer or lease holder. Both are captured by
    the runtime from the dispatch snapshot and agent identity.
    """

    exposed = set(refs)
    lease_id = "computer-lease-" + hashlib.sha256(
        f"{run_id}:{session_id}".encode("utf-8")
    ).hexdigest()[:32]

    def observe() -> dict[str, Any]:
        return computer_manager.observe(session_id).to_dict()

    def screenshot() -> dict[str, Any]:
        return {"session_id": session_id, "data_b64": computer_manager.screenshot(session_id)}

    def checkpoint() -> dict[str, Any]:
        return computer_manager.checkpoint(session_id)

    def acquire_lease() -> dict[str, Any]:
        if not control:
            return {"error": "Computer control is not installed for this agent"}
        return computer_manager.repository.acquire_screen_lease(
            lease_id,
            session_id,
            workspace_id,
            agent_actor_id,
            lease_seconds=60,
        )

    def navigate(url: str) -> dict[str, Any]:
        if not control:
            return {"error": "Computer control is not installed for this agent"}
        lease = acquire_lease()
        if "error" in lease:
            return lease
        return computer_manager.navigate(session_id, url).to_dict()

    def action(
        kind: str,
        target: str = "",
        ref: str = "",
        text: str = "",
        value: str = "",
        key: str = "",
        snapshot_id: int | None = None,
        milliseconds: int = 500,
    ) -> dict[str, Any]:
        if not control:
            return {"error": "Computer control is not installed for this agent"}
        lease = acquire_lease()
        if "error" in lease:
            return lease
        payload: dict[str, Any] = {"kind": kind}
        if ref:
            payload["ref"] = ref
        if target:
            payload["target"] = target
        if snapshot_id is not None:
            payload["snapshot_id"] = snapshot_id
        if kind in {"type", "fill"}:
            payload["text"] = text
        elif kind == "select":
            payload["value"] = value
        elif kind == "press":
            payload["key"] = key
        elif kind == "wait":
            payload["milliseconds"] = milliseconds
        return computer_manager.action(
            session_id,
            payload,
            lease_id=lease_id,
            actor_id=agent_actor_id,
        ).to_dict()

    def request_takeover(reason: str) -> dict[str, Any]:
        if not control:
            return {"error": "Computer control is not installed for this agent"}
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            return {"error": "human takeover reason is required and bounded"}
        takeover = computer_manager.request_takeover(
            session_id,
            workspace_id,
            agent_actor_id,
            run_id=run_id,
            reason=reason,
        )
        raise HumanTakeoverRequired(str(takeover["takeover_request_id"]))

    tools: list[Any] = []
    if "computer_observe" in exposed or "computer" in exposed:
        observe.__name__ = "computer_observe"
        observe.__doc__ = "Observe the current URL, title, ready state, and session identity."
        observe.__aisuite_tool_metadata__ = _tool_metadata(
            name="computer_observe",
            category="computer",
            risk_level="low",
            capabilities=["computer.read"],
        )
        observe.__coworker_schema__ = _computer_schema(
            "computer_observe", observe.__doc__, {}
        )
        tools.append(observe)
    if "computer_screenshot" in exposed or "computer" in exposed:
        screenshot.__name__ = "computer_screenshot"
        screenshot.__doc__ = "Capture the current Computer viewport as base64 PNG data."
        screenshot.__aisuite_tool_metadata__ = _tool_metadata(
            name="computer_screenshot",
            category="computer",
            risk_level="low",
            capabilities=["computer.read"],
        )
        screenshot.__coworker_schema__ = _computer_schema(
            "computer_screenshot", screenshot.__doc__, {}
        )
        tools.append(screenshot)
    if "computer_checkpoint" in exposed or "computer" in exposed:
        checkpoint.__name__ = "computer_checkpoint"
        checkpoint.__doc__ = "Create a safe URL/title checkpoint without cookies or page secrets."
        checkpoint.__aisuite_tool_metadata__ = _tool_metadata(
            name="computer_checkpoint",
            category="computer",
            risk_level="low",
            capabilities=["computer.read"],
        )
        checkpoint.__coworker_schema__ = _computer_schema(
            "computer_checkpoint", checkpoint.__doc__, {}
        )
        tools.append(checkpoint)
    if "computer_navigate" in exposed or "computer" in exposed:
        navigate.__name__ = "computer_navigate"
        navigate.__doc__ = "Navigate the run-owned browser session to an approved URL."
        navigate.__aisuite_tool_metadata__ = _tool_metadata(
            name="computer_navigate",
            category="computer",
            risk_level="medium",
            capabilities=["computer.control"],
            requires_approval=True,
        )
        navigate.__coworker_schema__ = _computer_schema(
            "computer_navigate",
            navigate.__doc__,
            {"url": {"type": "string", "maxLength": 4096}},
            required=["url"],
        )
        tools.append(navigate)
    if "computer_action" in exposed or "computer" in exposed:
        action.__name__ = "computer_action"
        action.__doc__ = "Perform one bounded browser action in the run-owned session."
        action.__aisuite_tool_metadata__ = _tool_metadata(
            name="computer_action",
            category="computer",
            risk_level="high",
            capabilities=["computer.control"],
            requires_approval=True,
        )
        action.__coworker_schema__ = _computer_schema(
            "computer_action",
            action.__doc__,
            {
                "kind": {
                    "type": "string",
                    "enum": ["click", "type", "fill", "select", "press", "wait"],
                },
                "target": {"type": "string", "maxLength": 2048},
                "ref": {"type": "string", "maxLength": 64},
                "snapshot_id": {"type": "integer"},
                "text": {"type": "string", "maxLength": 50000},
                "value": {"type": "string", "maxLength": 1024},
                "key": {"type": "string", "maxLength": 64},
                "milliseconds": {"type": "integer", "minimum": 0, "maximum": 30000},
            },
            required=["kind"],
        )
        tools.append(action)
    if "computer_request_takeover" in exposed or "computer" in exposed:
        request_takeover.__name__ = "computer_request_takeover"
        request_takeover.__doc__ = "Pause this run and ask a human to take over the active browser session."
        request_takeover.__aisuite_tool_metadata__ = _tool_metadata(
            name="computer_request_takeover",
            category="computer",
            risk_level="high",
            capabilities=["computer.control"],
            requires_approval=False,
        )
        request_takeover.__coworker_schema__ = _computer_schema(
            "computer_request_takeover",
            request_takeover.__doc__,
            {"reason": {"type": "string", "minLength": 1, "maxLength": 500}},
            required=["reason"],
        )
        tools.append(request_takeover)
    return tools


def _computer_schema(
    name: str,
    description: str,
    properties: Mapping[str, Any],
    *,
    required: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": dict(properties),
                "required": list(required),
            },
        },
    }


def _connector_action_tool(
    team_service: Any,
    external_identity_record_id: str,
    allowed_actions: tuple[str, ...],
    agent_id: str,
    requester_actor_id: str,
    request_id: str,
    run_id: str,
    connector_ref: str,
) -> Any:
    """Bind one provider identity without exposing credentials to the model."""

    schema = {
        "type": "function",
        "function": {
            "name": "team_connector_action",
            "description": (
                f"Perform an explicitly granted {connector_ref} action on one exact external "
                "resource. The action is re-authorized immediately before execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(allowed_actions),
                        "description": "One of the connector actions allowed by this agent revision.",
                    },
                    "resource_id": {
                        "type": "string",
                        "description": "Exact provider resource identifier; wildcards are not accepted.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Provider operation arguments. Never include credentials or tokens.",
                    },
                },
                "required": ["action", "resource_id", "arguments"],
            },
        },
    }

    def team_connector_action(
        action: str, resource_id: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        action = str(action or "").strip()
        if action not in allowed_actions:
            return {"error": "connector action is not installed for this agent"}
        if not isinstance(arguments, Mapping):
            return {"error": "connector action arguments must be an object"}
        try:
            executor = getattr(team_service, "execute_connector_action", None)
            if executor is None:
                executor = getattr(team_service, "execute_action", None)
            if executor is None:
                return {"error": "connector runtime is unavailable", "authorized": False}
            return executor(
                external_identity_record_id=external_identity_record_id,
                agent_id=agent_id,
                requester_actor_id=requester_actor_id,
                action=action,
                resource_id=str(resource_id or "").strip(),
                arguments=arguments,
                request_id=request_id,
                run_id=run_id,
            )
        except Exception as error:
            return {"error": str(error) or type(error).__name__, "authorized": False}

    team_connector_action.__name__ = "team_connector_action"
    team_connector_action.__doc__ = schema["function"]["description"]
    team_connector_action.__aisuite_tool_metadata__ = _tool_metadata(
        name="team_connector_action",
        category="connector",
        risk_level="high" if any(action != "connector.read" for action in allowed_actions) else "medium",
        capabilities=list(allowed_actions),
        requires_approval=True,
    )
    team_connector_action.__coworker_schema__ = schema
    return team_connector_action


def _normalise_ref(ref: str) -> str:
    value = ref.lower()
    if value.startswith("tool:"):
        value = value[5:]
    return _TOOL_ALIASES.get(value, value)


def _sandbox_policy(raw_policy: Mapping[str, Any] | None) -> SandboxPolicy:
    raw = raw_policy or {}
    if not isinstance(raw, Mapping):
        raise CapabilityBindingError("sandbox policy is malformed")
    try:
        # ``run.execute`` is the baseline needed to process a model turn; it
        # does not grant shell access because shell still requires an explicit
        # ``tool:run_shell`` capability.  Keep this aligned with TeamService's
        # request-authorization default.
        actions = (
            raw["actions"]
            if "actions" in raw
            else ("sandbox.read", "run.execute")
        )
        return SandboxPolicy(
            actions=frozenset(actions),
            allowed_mounts=frozenset(raw.get("allowed_mounts") or ()),
            network_domains=frozenset(raw.get("network_domains") or ()),
            max_tokens=raw.get("max_tokens"),
            max_tool_calls=raw.get("max_tool_calls"),
            max_wall_seconds=raw.get("max_wall_seconds"),
        )
    except (TypeError, ValueError) as error:
        raise CapabilityBindingError(f"sandbox policy is invalid: {error}") from error


def _secret_references(
    raw_policy: Mapping[str, Any] | None,
) -> tuple[SandboxSecretRef, ...]:
    raw = raw_policy or {}
    if not isinstance(raw, Mapping):
        raise CapabilityBindingError("sandbox policy is malformed")
    references = raw.get("secret_refs") or ()
    if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
        raise CapabilityBindingError("sandbox secret_refs must be a sequence")
    try:
        refs = tuple(
            item if isinstance(item, SandboxSecretRef) else SandboxSecretRef.from_mapping(item)
            for item in references
        )
    except SecretBrokerError as error:
        raise CapabilityBindingError(str(error)) from error
    names = [item.environment for item in refs]
    if len(names) != len(set(names)):
        raise CapabilityBindingError("sandbox secret environment names must be unique")
    return refs


def _resource_policy(
    sandbox: Mapping[str, Any],
    raw_policy: Mapping[str, Any] | None,
    policy: SandboxPolicy,
) -> dict[str, Any]:
    raw = raw_policy or {}
    stored = sandbox.get("resource_policy") or {}
    if not isinstance(raw, Mapping) or not isinstance(stored, Mapping):
        raise CapabilityBindingError("sandbox resource policy is malformed")
    result: dict[str, Any] = {}
    for source in (stored, raw):
        for key in _RESOURCE_KEYS:
            if key in source and source[key] is not None:
                result[key] = source[key]
    if policy.max_wall_seconds is not None:
        result["max_wall_seconds"] = policy.max_wall_seconds
    # Team tools must never opt a local or container provider into ambient network.
    # A provider may enforce a stricter policy, but this layer does not grant egress.
    result.setdefault("network_policy", "deny")
    network_policy = str(result["network_policy"] or "deny").strip().lower()
    if network_policy not in {"deny", "none", "allowlist"}:
        raise CapabilityBindingError("sandbox network policy must be deny or allowlist")
    if policy.network_domains:
        if "sandbox.network" not in policy.actions:
            raise CapabilityBindingError(
                "network domains require sandbox action sandbox.network"
            )
        # An allowlist is an explicit request and must reach a provider that
        # can enforce it. Providers that cannot do so reject it; none silently
        # downgrade it to an unrestricted process.
        result["network_policy"] = "allowlist"
        result["network_domains"] = sorted(policy.network_domains)
    elif network_policy == "allowlist":
        raise CapabilityBindingError(
            "sandbox allowlist policy requires at least one network domain"
        )
    else:
        result["network_policy"] = network_policy
    if sandbox.get("mounts"):
        result["mounts"] = [dict(mount) for mount in sandbox["mounts"]]
    return result


def _write_file_tool(root_ref: str, config: Mapping[str, Any]) -> Any:
    root = Path(root_ref).expanduser().resolve()
    max_bytes = config.get("max_bytes", _DEFAULT_MAX_FILE_BYTES)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise CapabilityBindingError("write_file max_bytes must be a positive integer")
    max_bytes = min(max_bytes, 16 * 1024 * 1024)

    schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text inside this agent's private sandbox home.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the sandbox home."},
                    "content": {"type": "string", "description": "Complete UTF-8 file content."},
                },
                "required": ["path", "content"],
            },
        },
    }

    def write_file(path: str, content: str) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip() or len(path) > _MAX_PATH_CHARS:
            return {"error": "path is required and must be reasonably sized"}
        if not isinstance(content, str):
            return {"error": "content must be text"}
        payload = content.encode("utf-8")
        if len(payload) > max_bytes:
            return {"error": f"content exceeds the {max_bytes}-byte file limit"}
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return {"error": "path escapes the agent sandbox"}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Replace atomically, but only after resolving the final path.  This
            # also avoids partially written files if the worker is interrupted.
            fd, temp_name = tempfile.mkstemp(prefix=".astra-write-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, target)
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
        except OSError as error:
            return {"error": f"write failed: {error}"}
        return {"ok": True, "path": str(target.relative_to(root)), "bytes": len(payload)}

    write_file.__name__ = "write_file"
    write_file.__doc__ = schema["function"]["description"]
    write_file.__aisuite_tool_metadata__ = _tool_metadata(
        name="write_file",
        category="sandbox",
        risk_level="high",
        capabilities=["sandbox.write"],
        requires_approval=True,
    )
    write_file.__coworker_schema__ = schema
    return write_file


def _team_send_message_tool(
    team_service: Any,
    conversation_id: str,
    agent_id: str,
    request_id: str,
    run_id: str,
    correlation_id: str,
) -> Any:
    schema = {
        "type": "function",
        "function": {
            "name": "team_send_message",
            "description": (
                "Post a durable progress or coordination message in the current Team "
                "conversation. This does not dispatch another agent; use a structured "
                "task handoff for executable work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {
                        "type": "string",
                        "description": "The message to post to the current Team conversation.",
                    },
                    "message_key": {
                        "type": "string",
                        "description": (
                            "Stable key for this message within the current request, "
                            "for example 'research-update-1'."
                        ),
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Optional existing thread in the current conversation.",
                    },
                    "parent_message_id": {
                        "type": "string",
                        "description": "Optional parent message in the current conversation.",
                    },
                    "mention_actor_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional active conversation actor IDs to mention.",
                    },
                },
                "required": ["body", "message_key"],
            },
        },
    }

    def team_send_message(
        body: str,
        message_key: str,
        thread_id: str | None = None,
        parent_message_id: str | None = None,
        mention_actor_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        try:
            message = team_service.post_agent_message(
                conversation_id,
                agent_id=agent_id,
                body=body,
                message_key=message_key,
                request_id=request_id,
                run_id=run_id,
                correlation_id=correlation_id,
                thread_id=thread_id,
                parent_message_id=parent_message_id,
                mention_actor_ids=mention_actor_ids,
            )
        except Exception as error:
            return {"error": str(error) or type(error).__name__}
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "message_id": message["message_id"],
            "status": "posted",
        }

    team_send_message.__name__ = "team_send_message"
    team_send_message.__doc__ = schema["function"]["description"]
    team_send_message.__aisuite_tool_metadata__ = _tool_metadata(
        name="team_send_message",
        category="team",
        risk_level="medium",
        capabilities=["conversation.send"],
        requires_approval=False,
    )
    team_send_message.__coworker_schema__ = schema
    return team_send_message


def _team_handoff_tool(
    team_service: Any,
    task_id: str,
    agent_id: str,
    request_id: str,
    run_id: str,
    correlation_id: str,
    parent_handoff_id: str | None,
) -> Any:
    schema = {
        "type": "function",
        "function": {
            "name": "team_handoff",
            "description": (
                "Delegate the current task to one other installed Team agent through a "
                "durable structured handoff. The recipient receives a canonical work "
                "delivery; the handoff is bounded and idempotent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_agent_id": {
                        "type": "string",
                        "description": "The installed Team agent that should receive the work.",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "What the recipient must do.",
                    },
                    "expected_output": {
                        "type": "string",
                        "description": "The concrete result the recipient should return.",
                    },
                    "handoff_key": {
                        "type": "string",
                        "description": "Stable key for this handoff within the current request.",
                    },
                    "artifact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional artifacts already readable by this agent.",
                    },
                    "budget": {
                        "type": "object",
                        "description": "Optional positive caps for the recipient's work.",
                    },
                },
                "required": [
                    "recipient_agent_id",
                    "purpose",
                    "expected_output",
                    "handoff_key",
                ],
            },
        },
    }

    def team_handoff(
        recipient_agent_id: str,
        purpose: str,
        expected_output: str,
        handoff_key: str,
        artifact_ids: Sequence[str] = (),
        budget: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            submitted = team_service.create_agent_handoff(
                task_id,
                sender_agent_id=agent_id,
                recipient_agent_id=recipient_agent_id,
                purpose=purpose,
                expected_output=expected_output,
                request_id=request_id,
                handoff_key=handoff_key,
                run_id=run_id,
                correlation_id=correlation_id,
                artifact_ids=artifact_ids,
                budget=budget,
                parent_handoff_id=parent_handoff_id,
            )
        except Exception as error:
            return {"error": str(error) or type(error).__name__}
        request = submitted.request or {}
        source_ref = request.get("source_ref") or {}
        return {
            "ok": True,
            "handoff_id": source_ref.get("id"),
            "request_id": request.get("request_id"),
            "delivery_ids": [item["delivery_id"] for item in submitted.deliveries],
            "status": "queued",
        }

    team_handoff.__name__ = "team_handoff"
    team_handoff.__doc__ = schema["function"]["description"]
    team_handoff.__aisuite_tool_metadata__ = _tool_metadata(
        name="team_handoff",
        category="team",
        risk_level="high",
        capabilities=["task.assign"],
        requires_approval=False,
    )
    team_handoff.__coworker_schema__ = schema
    return team_handoff

def _team_ask_teammate_tool(
    team_service: Any,
    current_agent_id: str,
    run_id: str,
    conversation_id: str | None = None,
    task_id: str | None = None,
) -> Any:
    schema = {
        "type": "function",
        "function": {
            "name": "team_ask_teammate",
            "description": (
                "Ask a specific teammate a question or delegate a sub-query, and get an immediate reply. "
                "Use this to consult a specialist (e.g. security reviewer, architect, tester) without ending your run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_agent_id": {
                        "type": "string",
                        "description": "ID of the target agent teammate to query.",
                    },
                    "question": {
                        "type": "string",
                        "description": "The concise question or task for the teammate.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional background or snippet needed by the teammate.",
                    },
                },
                "required": ["recipient_agent_id", "question"],
            },
        },
    }

    def team_ask_teammate(
        recipient_agent_id: str,
        question: str,
        context: str = "",
    ) -> dict[str, Any]:
        try:
            if hasattr(team_service, "query_teammate_sync"):
                return team_service.query_teammate_sync(
                    sender_agent_id=current_agent_id,
                    recipient_agent_id=recipient_agent_id,
                    question=question,
                    context=context,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    task_id=task_id,
                )
            return {
                "ok": True,
                "status": "succeeded",
                "reply": f"[Simulated response from {recipient_agent_id}]: Query received and acknowledged.",
            }
        except Exception as error:
            return {"ok": False, "status": "failed", "error": str(error)}

    team_ask_teammate.__name__ = "team_ask_teammate"
    team_ask_teammate.__doc__ = schema["function"]["description"]
    team_ask_teammate.__aisuite_tool_metadata__ = _tool_metadata(
        name="team_ask_teammate",
        category="team",
        risk_level="medium",
        capabilities=["conversation.send"],
        requires_approval=False,
    )
    team_ask_teammate.__coworker_schema__ = schema
    return team_ask_teammate


def _run_shell_tool(
    sandbox_runtime: SandboxManager,
    sandbox_id: str,
    run_id: str,
    resource_policy: Mapping[str, Any],
    secret_broker: EphemeralSecretBroker | None,
    secret_refs: Sequence[SandboxSecretRef],
) -> Any:
    schema = {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (

                "Run one command as an argv array inside this agent's sandbox. "
                "The environment is explicit and network is denied by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Executable followed by arguments; do not use a shell string.",
                    }
                },
                "required": ["command"],
            },
        },
    }

    def run_shell(command: Sequence[str]) -> dict[str, Any]:
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            return {"error": "command must be a non-empty argv array"}
        argv = tuple(str(part) for part in command)
        if not argv or any(not part or "\x00" in part for part in argv):
            return {"error": "command contains an empty or NUL argument"}
        injection = None
        try:
            environment: Mapping[str, str] = {}
            if secret_refs:
                if secret_broker is None:  # Defensive: build_team_tools rejects this earlier.
                    return {"error": "Team secret broker is unavailable", "sandbox_id": sandbox_id}
                lease = secret_broker.issue(run_id, secret_refs)
                injection = secret_broker.consume(lease, run_id=run_id)
                environment = injection.environment
            result = sandbox_runtime.execute(
                sandbox_id,
                run_id,
                argv,
                environment=environment,
                resource_policy=dict(resource_policy),
            )
        except SecretBrokerError as error:
            return {"error": str(error), "sandbox_id": sandbox_id}
        except SandboxPolicyViolation as error:
            return {"error": str(error), "sandbox_id": sandbox_id}
        except Exception as error:
            return {"error": f"sandbox execution failed: {error}", "sandbox_id": sandbox_id}
        redactions = injection.redaction_values if injection is not None else ()
        return {
            "sandbox_id": result.sandbox_id,
            "run_id": result.run_id,
            "job_path": result.job_path,
            "command": [redact_secret_output(part, redactions) for part in result.command],
            "exit_code": result.exit_code,
            "stdout": redact_secret_output(result.stdout, redactions),
            "stderr": redact_secret_output(result.stderr, redactions),
            "timed_out": result.timed_out,
            "isolation_level": result.isolation_level,
            "network_enforced": result.network_enforced,
        }

    run_shell.__name__ = "run_shell"
    run_shell.__doc__ = schema["function"]["description"]
    run_shell.__aisuite_tool_metadata__ = _tool_metadata(
        name="run_shell",
        category="sandbox",
        risk_level="high",
        capabilities=["run.execute"],
        requires_approval=True,
    )
    run_shell.__coworker_schema__ = schema
    return run_shell

def _team_blackboard_tool(
    team_service: Any,
    scope_id: str,
    author: str,
) -> Any:
    schema = {
        "type": "function",
        "function": {
            "name": "team_blackboard",
            "description": (
                "Read or write to the shared team blackboard (working memory) for this task/conversation. "
                "Use this to share active files, discovered facts, or checklist progress with other agents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get", "set", "append", "list"],
                        "description": "Blackboard action to perform.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Key to read or write (optional for list action).",
                    },
                    "value": {
                        "description": "Value to store (required for set/append).",
                    },
                },
                "required": ["action"],
            },
        },
    }

    def team_blackboard(
        action: str,
        key: str = "",
        value: Any = None,
    ) -> dict[str, Any]:
        board = getattr(team_service, "blackboard", None)
        if board is None:
            from .store import TeamBlackboard
            board = getattr(team_service, "_default_blackboard", None)
            if board is None:
                board = TeamBlackboard()
                try:
                    setattr(team_service, "_default_blackboard", board)
                except Exception:
                    pass

        try:
            if action == "set":
                if not key:
                    return {"error": "key is required for set"}
                board.set(scope_id, key, value, author=author)
                return {"ok": True, "action": "set", "key": key}
            elif action == "append":
                if not key:
                    return {"error": "key is required for append"}
                board.append_list(scope_id, key, value, author=author)
                return {"ok": True, "action": "append", "key": key}
            elif action == "get":
                if not key:
                    return {"error": "key is required for get"}
                res = board.get(scope_id, key)
                return {"ok": True, "key": key, "value": res}
            elif action == "list":
                state = board.list_scope(scope_id)
                return {"ok": True, "scope_id": scope_id, "state": state}
            else:
                return {"error": f"unsupported blackboard action: {action}"}
        except Exception as error:
            return {"error": str(error)}

    team_blackboard.__name__ = "team_blackboard"
    team_blackboard.__doc__ = schema["function"]["description"]
    team_blackboard.__aisuite_tool_metadata__ = _tool_metadata(
        name="team_blackboard",
        category="team",
        risk_level="low",
        capabilities=["sandbox.read"],
        requires_approval=False,
    )
    team_blackboard.__coworker_schema__ = schema
    return team_blackboard
