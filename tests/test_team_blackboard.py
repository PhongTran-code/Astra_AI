import pytest
from coworker.teams.store import TeamBlackboard

def test_team_blackboard_operations():
    bb = TeamBlackboard()

    # 1. Set key-value
    bb.set("task-100", "active_file", "src/auth/jwt.py", author="agent-coder")
    assert bb.get("task-100", "active_file") == "src/auth/jwt.py"

    # 2. List keys for task
    state = bb.list_scope("task-100")
    assert "active_file" in state
    assert state["active_file"]["value"] == "src/auth/jwt.py"
    assert state["active_file"]["author"] == "agent-coder"

    # 3. Update checklist or facts
    bb.append_list("task-100", "discovered_ports", 8765)
    bb.append_list("task-100", "discovered_ports", 5188)
    assert bb.get("task-100", "discovered_ports") == [8765, 5188]
