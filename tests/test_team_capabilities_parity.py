import pytest
from coworker.teams.capabilities import build_team_tools
from coworker.teams.permissions import PermissionContext, SandboxPolicy, WorkspaceRole
from coworker.teams.contracts import ActorKind, ActorRef, AgentRole

class DummySandboxManager:
    pass

def test_team_tools_code_parity():
    context = PermissionContext(
        workspace_id="ws-1",
        requester=ActorRef("human-1", ActorKind.HUMAN, "Human"),
        requester_role=WorkspaceRole.OWNER,
        agent_id="agent-1",
        agent_role=AgentRole.BUILDER,
        sandbox=SandboxPolicy(actions=frozenset({"sandbox.read", "sandbox.write", "run.execute"})),
    )
    sandbox = {"sandbox_id": "sb-1", "home_ref": "/tmp"}
    capabilities = [
        {"capability_kind": "tool", "capability_ref": "replace_in_file"},
        {"capability_kind": "tool", "capability_ref": "apply_patch"},
        {"capability_kind": "tool", "capability_ref": "list_files"},
        {"capability_kind": "tool", "capability_ref": "git_status"},
        {"capability_kind": "tool", "capability_ref": "team_blackboard"},
    ]
    tools, unavailable, report = build_team_tools(
        capabilities,
        sandbox=sandbox,
        sandbox_policy={"actions": ["sandbox.read", "sandbox.write", "run.execute"]},
        sandbox_runtime=DummySandboxManager(),
        run_id="run-1",
        permission_context=context,
        team_service=None,
    )

    tool_names = [getattr(t, "__name__", str(t)) for t in tools]
    assert "replace_in_file" in tool_names
    assert "apply_patch" in tool_names
    assert "list_files" in tool_names
    assert "git_status" in tool_names
    assert "team_blackboard" in tool_names
    assert len(unavailable) == 0
