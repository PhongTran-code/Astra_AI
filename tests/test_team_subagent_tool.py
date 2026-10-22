import pytest
from coworker.teams.capabilities import _team_ask_teammate_tool

class DummyTeamService:
    def __init__(self):
        self.called_with = None

    def query_teammate_sync(self, **kwargs):
        self.called_with = kwargs
        return {
            "ok": True,
            "status": "succeeded",
            "reply": "PostgreSQL schema is valid and indexes are optimal.",
            "responder_agent_id": kwargs["recipient_agent_id"],
        }

def test_team_ask_teammate_tool():
    service = DummyTeamService()
    tool = _team_ask_teammate_tool(
        team_service=service,
        current_agent_id="agent-coder",
        run_id="run-123",
        conversation_id="conv-1",
        task_id="task-1",
    )

    result = tool(
        recipient_agent_id="agent-reviewer",
        question="Vui lòng review giúp tôi file schema.sql này",
    )

    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert "PostgreSQL schema is valid" in result["reply"]
    assert service.called_with["recipient_agent_id"] == "agent-reviewer"
    assert service.called_with["sender_agent_id"] == "agent-coder"
