import pytest
from coworker.teams.runtime import _render_package

def test_render_package_semantic_roles():
    package = {
        "messages": [
            {
                "message_id": "msg-1",
                "author_actor_id": "user-human",
                "author_display_name": "Alice Human",
                "body": "Hãy lập kế hoạch thiết kế database.",
            },
            {
                "message_id": "msg-2",
                "author_actor_id": "agent-architect",
                "author_display_name": "ArchitectBot",
                "body": "Tôi đề xuất dùng PostgreSQL với pgvector.",
            },
            {
                "message_id": "msg-3",
                "author_actor_id": "agent-coder",
                "author_display_name": "CoderBot",
                "body": "Tôi đã tạo schema migration.",
            },
        ],
        "memories": [{"content": "Dự án ưu tiên hiệu năng"}],
    }

    # Agent Coder is running (current agent is agent-coder)
    history, context_text = _render_package(
        package,
        agent_actor_id="agent-coder",
        source_message_id=None,
    )

    assert len(history) == 3
    # Human message -> role: "user"
    assert history[0]["role"] == "user"
    assert "Alice Human" in history[0].get("name", "") or "[Alice Human]" in history[0]["content"]

    # Other agent message (ArchitectBot) -> role: "assistant" with name
    assert history[1]["role"] == "assistant"
    assert history[1].get("name") == "ArchitectBot"

    # Self agent message (CoderBot) -> role: "assistant"
    assert history[2]["role"] == "assistant"
    assert history[2].get("name") == "CoderBot"

    assert "Dự án ưu tiên hiệu năng" in context_text
