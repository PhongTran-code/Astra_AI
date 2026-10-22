"""P6 gate tests — server: OpenAI-compatible endpoint, WS session API, REST."""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ToolCall,
)
from coworker.server import SessionManager, create_app
from coworker.sessions import SessionRecord
from coworker.teams import ActorKind, ActorRef, ResourceRef
from coworker.teams.contracts import ContractError


class ScriptedProvider(ProviderClient):
    """A ProviderClient that returns queued AssistantTurns (streams via base default)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.calls = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append({"model": model, "messages": messages, "tools": tools, "settings": settings})
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text):
    return AssistantTurn(text=text, finish_reason="stop")


def _tool(name, args, call_id="call_1"):
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=args)])


def _client(tmp_path, turns):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider(turns))
    return TestClient(create_app(manager))


# -- REST -----------------------------------------------------------------------


def test_chat_completions_openai_shape(tmp_path):
    client = _client(tmp_path, [_text("hello world")])
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.5", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "hello world"
    assert body["choices"][0]["finish_reason"] == "stop"


def test_agents_and_memory_rest(tmp_path):
    client = _client(tmp_path, [])
    agents = client.get("/v1/agents").json()["agents"]
    # The picker lists enabled+surfaced personas. Release lineup (owner 2026-08-21):
    # Astra + the security bundles; Code ships disabled, Chat is gone, and
    # ships:false personas (teams, ops, design) need ASTRA_UNSHIPPED=1.
    names = [a["name"] for a in agents]
    assert names[0] == "cowork"
    assert set(names) == {"cowork", "security", "cloud-posture", "dep-audit"}
    assert "skills" in client.get("/v1/skills").json()  # catalog (may be empty)

    added = client.post("/v1/memory", json={"content": "prefer pathlib"}).json()
    assert added["content"] == "prefer pathlib"
    assert any(
        m["content"] == "prefer pathlib"
        for m in client.get("/v1/memory").json()["memory"]
    )


def test_canonical_team_api_routes_dm_through_durable_coordinator(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="builder",
        instructions="Build safely.",
    )
    client = TestClient(create_app(manager))
    created = client.post(
        "/v1/team/conversations",
        json={"kind": "direct", "agent_ids": [agent["agent_id"]]},
    )
    assert created.status_code == 200
    conversation = created.json()
    sent = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "hello", "client_request_id": "client-send-1"},
    )
    assert sent.status_code == 200
    assert sent.json()["routing"]["target_agent_ids"] == [agent["agent_id"]]
    assert client.get(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages"
    ).json()["messages"][0]["body"] == "hello"

def test_canonical_team_api_rejects_two_member_groups(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    first_agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="First agent",
        handle="first-agent",
        instructions="Handle first-agent work safely.",
    )
    second_agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Second agent",
        handle="second-agent",
        instructions="Handle second-agent work safely.",
    )
    client = TestClient(create_app(manager))

    rejected = client.post(
        "/v1/team/conversations",
        json={"kind": "group", "agent_ids": [first_agent["agent_id"]]},
    )
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "a group conversation must have at least two agents"

    created = client.post(
        "/v1/team/conversations",
        json={"kind": "group", "agent_ids": [first_agent["agent_id"], second_agent["agent_id"]]},
    )
    assert created.status_code == 200
    removed = client.delete(
        f"/v1/team/conversations/{created.json()['conversation_id']}/members/{second_agent['agent_id']}",
    )
    assert removed.status_code == 400
    assert removed.json()["error"] == "group conversations must keep at least two agents"




def test_canonical_team_api_opens_thread_replies_and_reacts(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Thread agent",
        handle="thread-agent",
        instructions="Handle thread work safely.",
    )
    second_agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Second agent",
        handle="second-agent",
        instructions="Review thread work safely.",
    )
    third_agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Third agent",
        handle="third-agent",
        instructions="Review third-agent work safely.",
    )
    client = TestClient(create_app(manager))
    conversation = client.post(
        "/v1/team/conversations",
        json={"kind": "group", "agent_ids": [agent["agent_id"], second_agent["agent_id"]]},
    ).json()
    added_member = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/members",
        json={"agent_id": third_agent["agent_id"]},
    )
    assert added_member.status_code == 200
    assert third_agent["agent_id"] in added_member.json()["agent_member_ids"]
    removed_member = client.delete(
        f"/v1/team/conversations/{conversation['conversation_id']}/members/{third_agent['agent_id']}"
    )
    assert removed_member.status_code == 200
    assert third_agent["agent_id"] not in removed_member.json()["agent_member_ids"]
    pinned_conversation = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/pin",
    )
    assert pinned_conversation.status_code == 200
    assert pinned_conversation.json()["pinned"] is True
    assert client.get("/v1/team/conversations").json()["conversations"][0]["pinned"] is True
    assert client.delete(
        f"/v1/team/conversations/{conversation['conversation_id']}/pin",
    ).json()["pinned"] is False
    root = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "Open a focused thread.", "client_request_id": "thread-api-root"},
    ).json()["message"]

    opened = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/threads",
        json={"root_message_id": root["message_id"]},
    )
    replay = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/threads",
        json={"root_message_id": root["message_id"]},
    )
    assert opened.status_code == replay.status_code == 200
    thread = opened.json()["thread"]
    assert thread["thread_id"] == replay.json()["thread"]["thread_id"]

    reply = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={
            "body": "Thread reply.",
            "client_request_id": "thread-api-reply",
            "thread_id": thread["thread_id"],
            "parent_message_id": root["message_id"],
            "quote_message_id": root["message_id"],
        },
    )
    assert reply.status_code == 200
    thread_view = client.get(f"/v1/team/threads/{thread['thread_id']}")
    assert [message["body"] for message in thread_view.json()["messages"]] == [
        "Open a focused thread.",
        "Thread reply.",
    ]
    assert thread_view.json()["messages"][1]["quote_message_id"] == root["message_id"]
    reaction = client.post(
        f"/v1/team/messages/{root['message_id']}/reaction",
        json={"emoji": "👍", "active": True},
    )
    assert reaction.status_code == 200

    pinned = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages/{root['message_id']}/pin",
    )
    assert pinned.status_code == 200
    assert pinned.json()["message_id"] == root["message_id"]
    assert client.get(
        f"/v1/team/conversations/{conversation['conversation_id']}/pins"
    ).json()["pins"][0]["message_id"] == root["message_id"]
    assert client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages/{root['message_id']}/pin",
    ).status_code == 200
    unpinned = client.delete(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages/{root['message_id']}/pin",
    )
    assert unpinned.status_code == 200 and unpinned.json()["removed"] is True
    assert client.delete(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages/{root['message_id']}/pin",
    ).json()["removed"] is False

    attachment = client.post(
        f"/v1/team/messages/{root['message_id']}/attachments",
        json={
            "filename": "release.png",
            "data_b64": base64.b64encode(b"\x89PNG\r\n\x1a\nrelease").decode(),
        },
    )
    assert attachment.status_code == 200
    attachment_body = attachment.json()
    assert attachment_body["filename"] == "release.png"
    replayed_attachment = client.post(
        f"/v1/team/messages/{root['message_id']}/attachments",
        json={
            "filename": "release.png",
            "data_b64": base64.b64encode(b"\x89PNG\r\n\x1a\nrelease").decode(),
        },
    )
    assert replayed_attachment.json()["attachment_id"] == attachment_body["attachment_id"]
    downloaded = client.get(
        f"/v1/team/messages/{root['message_id']}/attachments/{attachment_body['attachment_id']}"
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"\x89PNG\r\n\x1a\nrelease"

    edited = client.post(
        f"/v1/team/messages/{root['message_id']}/edit",
        json={"body": "Open the focused thread now.", "expected_revision": 1},
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "Open the focused thread now."

    deleted = client.delete(f"/v1/team/messages/{root['message_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "tombstoned"


def test_canonical_team_message_translation_is_toolless_and_cached(tmp_path):
    provider = ScriptedProvider([_text("Xem bản phát hành này")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Translator",
        handle="translator",
        instructions="Translate requests safely.",
    )
    client = TestClient(create_app(manager))
    conversation = client.post(
        "/v1/team/conversations",
        json={"kind": "direct", "agent_ids": [agent["agent_id"]]},
    ).json()
    sent = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Review this release",
        client_request_id="translation-message-1",
    )
    message_id = sent.message["message_id"]

    translated = client.post(
        f"/v1/team/messages/{message_id}/translate",
        json={"language": "vi-VN"},
    )
    assert translated.status_code == 200
    assert translated.json()["body"] == "Xem bản phát hành này"
    assert translated.json()["language"] == "vi-vn"

    cached = client.post(
        f"/v1/team/messages/{message_id}/translate",
        json={"language": "vi-VN"},
    )
    assert cached.status_code == 200
    assert cached.json() == translated.json()
    assert len(provider.calls) == 1
    assert provider.calls[0]["tools"] == []
    assert "Review this release" in provider.calls[0]["messages"][1]["content"]


def test_canonical_team_task_crew_artifact_and_integration_lifecycle(tmp_path):
    provider = ScriptedProvider([_text("task result")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Crew builder",
        handle="crew-builder",
        instructions="Complete the task and report evidence.",
    )
    reviewer = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Crew reviewer",
        handle="crew-reviewer",
        instructions="Review release work safely.",
    )

    client = TestClient(create_app(manager))

    task_response = client.post(
        "/v1/team/tasks",
        json={
            "title": "Build the release note",
            "description": "Prepare the verified release note.",
            "acceptance_criteria": "The note is ready for review.",
            "owner_agent_id": agent["agent_id"],
        },
    )
    assert task_response.status_code == 200
    task = task_response.json()
    assert task["canonical_thread_id"]
    task_thread = client.get(f"/v1/team/tasks/{task['task_id']}/thread")
    assert task_thread.status_code == 200
    assert task_thread.json()["thread"]["thread_id"] == task["canonical_thread_id"]
    assert "Task created: Build the release note" in task_thread.json()["messages"][0]["body"]

    group = client.post(
        "/v1/team/conversations",
        json={
            "kind": "group",
            "title": "Release crew",
            "agent_ids": [agent["agent_id"], reviewer["agent_id"]],
        },
    ).json()
    summary = client.post(
        f"/v1/team/tasks/{task['task_id']}/summary",
        json={
            "conversation_id": group["conversation_id"],
            "summary": "Builder has started the release note.",
            "client_request_id": "task-summary-api-1",
        },
    )
    assert summary.status_code == 200
    assert "astra://conversation/" in summary.json()["body"]

    crew_response = client.post(
        "/v1/team/crews",
        json={
            "agent_ids": [agent["agent_id"]],
            "root_task_id": task["task_id"],
            "agent_roles": {agent["agent_id"]: "builder"},
            "client_request_id": "crew-api-1",
        },
    )
    assert crew_response.status_code == 200
    crew = crew_response.json()
    assert crew["status"] == "active"
    assert crew["agents"][0]["crew_role"] == "builder"

    dispatch = client.post(
        f"/v1/team/tasks/{task['task_id']}/dispatch",
        json={"crew_run_id": crew["crew_run_id"], "client_request_id": "task-api-1"},
    )
    assert dispatch.status_code == 200
    request = dispatch.json()["request"]
    assert request["crew_run_id"] == crew["crew_run_id"]
    assert client.get(f"/v1/team/tasks/{task['task_id']}").json()["status"] == "in_progress"

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))
    assert result is not None and result["status"] == "succeeded"
    run_id = result["run"]["run_id"]
    run_detail = client.get(f"/v1/team/runs/{run_id}")
    assert run_detail.status_code == 200
    assert run_detail.json()["run"]["run_id"] == run_id
    assert run_detail.json()["usage"]["request_id"] == request["request_id"]
    assert client.get(
        "/v1/team/runs", params={"task_id": task["task_id"]}
    ).json()["runs"][0]["run_id"] == run_id

    published = client.post(
        "/v1/team/artifacts/publish",
        json={
            "source_run_id": run_id,
            "source_task_id": task["task_id"],
            "filename": "release.md",
            "media_type": "text/markdown",
            "size_bytes": 11,
            "sha256": "0" * 64,
            "visibility": "task",
            "scope_id": task["task_id"],
            "storage_ref": "r2://team/release.md",
            "idempotency_key": "release-artifact-1",
            "metadata": {"preview": "The verified release note."},
        },
    )
    assert published.status_code == 200
    artifact = published.json()
    artifact_id = artifact["artifact_id"]
    assert client.get(f"/v1/team/artifacts/{artifact_id}/download").json()["storage_ref"] == "r2://team/release.md"
    assert client.get(f"/v1/team/artifacts/{artifact_id}/preview").json()["preview"] == "The verified release note."

    memory = client.post(
        "/v1/team/context/memories",
        json={
            "scope": "task",
            "scope_id": task["task_id"],
            "content": "The release note has a verified source.",
            "source_artifact_id": artifact_id,
            "source_run_id": run_id,
        },
    ).json()
    memory_id = memory["memory_id"]
    assert client.put(
        f"/v1/team/context/memories/{memory_id}",
        json={"content": "The release note was reviewed."},
    ).json()["content"] == "The release note was reviewed."
    assert client.get(
        "/v1/team/context/memories",
        params={"scope": "task", "scope_id": task["task_id"]},
    ).json()["memories"][0]["memory_id"] == memory_id
    assert client.delete(f"/v1/team/context/memories/{memory_id}").status_code == 200

    integration = client.post(
        "/v1/team/integrations",
        json={
            "task_id": task["task_id"],
            "run_id": run_id,
            "artifact_id": artifact_id,
            "source_ref": "worktree://crew-builder/task",
            "target_ref": "project://main",
            "idempotency_key": "integration-api-1",
        },
    )
    assert integration.status_code == 200
    integration_id = integration.json()["integration_id"]
    assert client.post(
        f"/v1/team/integrations/{integration_id}/transition",
        json={"status": "approved"},
    ).json()["status"] == "approved"
    assert client.post(
        f"/v1/team/integrations/{integration_id}/transition",
        json={"status": "applied"},
    ).json()["status"] == "applied"

    assert client.post(
        f"/v1/team/artifacts/{artifact_id}/quarantine",
        json={"reason": "malware scan pending"},
    ).status_code == 200
    assert client.get(f"/v1/team/artifacts/{artifact_id}/download").status_code == 400
    assert client.post(f"/v1/team/artifacts/{artifact_id}/quarantine/release").status_code == 200


def test_canonical_team_local_artifact_content_is_acl_checked_and_integrity_checked(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Artifact builder",
        handle="artifact-builder",
        instructions="Publish verified artifacts.",
    )
    client = TestClient(create_app(manager))
    task = client.post(
        "/v1/team/tasks",
        json={
            "title": "Publish local artifact",
            "acceptance_criteria": "The artifact is retrievable.",
            "owner_agent_id": agent["agent_id"],
        },
    ).json()
    dispatched = client.post(
        f"/v1/team/tasks/{task['task_id']}/dispatch",
        json={"client_request_id": "local-artifact-dispatch"},
    ).json()
    run_id = dispatched["request"]["request_id"]
    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))
    assert result is not None
    run_id = result["run"]["run_id"]

    data = b"\x89PNG\r\n\x1a\n"
    storage_ref = manager.attachment_store.put(data, "verified.png")
    artifact = client.post(
        "/v1/team/artifacts/publish",
        json={
            "source_run_id": run_id,
            "source_task_id": task["task_id"],
            "filename": "verified.png",
            "media_type": "image/png",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "visibility": "task",
            "scope_id": task["task_id"],
            "storage_ref": storage_ref,
            "idempotency_key": "local-artifact-1",
        },
    ).json()
    artifact_id = artifact["artifact_id"]
    descriptor = client.get(f"/v1/team/artifacts/{artifact_id}/download").json()
    assert descriptor["content_available"] is True
    content = client.get(f"/v1/team/artifacts/{artifact_id}/content")
    assert content.status_code == 200
    assert content.content == data
    assert content.headers["content-disposition"] == 'attachment; filename="verified.png"'

    manager.attachment_store.path_for(storage_ref[len("attachment://"):].split("#", 1)[0]).write_bytes(b"tampered")
    assert client.get(f"/v1/team/artifacts/{artifact_id}/content").status_code == 409


def test_canonical_team_worker_executes_custom_agent_and_acknowledges_delivery(tmp_path):
    provider = ScriptedProvider([_text("custom reply")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Reviewer",
        handle="reviewer",
        instructions="You are a strict release reviewer.",
        model_policy={"model": "gpt-5.6-sol", "temperature": 0},
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Review this release.",
        client_request_id="team-worker-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None
    assert result["status"] == "succeeded"
    messages = manager.team_conversations.list_messages(
        conversation["conversation_id"], requester_actor_id=manager.team_actor_id
    )["messages"]
    assert [message["body"] for message in messages] == [
        "Review this release.",
        "custom reply",
    ]
    delivery = manager.team_work.get_delivery(submitted.deliveries[0]["delivery_id"])
    assert delivery["status"] == "acknowledged"
    run = manager.team_work.get_run(result["run"]["run_id"])
    assert run["status"] == "succeeded"
    assert manager.team_work.get_usage(submitted.request["request_id"])["model_calls"] == 1
    with manager.team_database._lock:
        output_event = manager.team_database._conn.execute(
            "SELECT payload_json FROM run_events WHERE run_id = ? AND event_type = 'run.output'",
            (run["run_id"],),
        ).fetchone()
    assert output_event is not None and "custom reply" in output_event["payload_json"]
    assert manager.team_agents.get_installation(agent["agent_id"])["status"] == "ready"
    assert provider.calls[0]["model"] == "gpt-5.6-sol"
    assert "strict release reviewer" in provider.calls[0]["messages"][0]["content"]


def test_canonical_team_worker_uses_revision_at_dispatch(tmp_path):
    provider = ScriptedProvider([_text("revision two reply")])
    manager = SessionManager(workspace=tmp_path, provider=provider)
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="builder-revision",
        instructions="Use revision one.",
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    manager.team_service.send_message(
        conversation["conversation_id"],
        body="Do the work.",
        client_request_id="team-worker-revision-1",
    )
    manager.team_agent_lifecycle.edit(
        agent["agent_id"],
        requester=human,
        instructions="Use revision two.",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None and result["status"] == "succeeded"
    # Dispatch snapshots pin the revision at enqueue time; editing the Bot
    # before the worker claims the delivery must not mutate queued work.
    assert "Use revision one." in provider.calls[0]["messages"][0]["content"]


def test_canonical_team_worker_failure_retries_without_false_ack(tmp_path):
    class FailingProvider(ProviderClient):
        def complete(self, *, model, messages, tools=None, **settings):
            raise RuntimeError("provider unavailable")

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(workspace=tmp_path, provider=FailingProvider())
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Verifier",
        handle="verifier-failure",
        instructions="Verify carefully.",
    )
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=(agent["agent_id"],)
    )
    submitted = manager.team_service.send_message(
        conversation["conversation_id"],
        body="Verify this.",
        client_request_id="team-worker-failure-1",
    )

    result = asyncio.run(manager.drain_team_work_once(agent["agent_id"]))

    assert result is not None
    assert result["status"] == "failed"
    assert "provider unavailable" in result["error"]
    assert manager.team_work.get_delivery(submitted.deliveries[0]["delivery_id"])["status"] == "retry_wait"
    assert manager.team_work.get_run(result["run"]["run_id"])["status"] == "failed"
    messages = manager.team_conversations.list_messages(
        conversation["conversation_id"], requester_actor_id=manager.team_actor_id
    )["messages"]
    assert len(messages) == 1


def test_team_agent_rename_preserves_historical_message_attribution(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="builder",
        instructions="Build safely.",
    )
    client = TestClient(create_app(manager))
    conversation = client.post(
        "/v1/team/conversations",
        json={"kind": "direct", "title": "Search discussion", "agent_ids": [agent["agent_id"]]},
    ).json()
    manager.team_conversations.append_message(
        "message_historical_attribution",
        conversation["conversation_id"],
        ActorRef(agent["agent_actor_id"], ActorKind.AGENT, "Builder"),
        "completed build",
        "history-message-1",
        "history-trace-1",
    )

    renamed = client.patch(
        f"/v1/team/agents/{agent['agent_id']}",
        json={"display_name": "Reviewer", "handle": "reviewer"},
    )
    assert renamed.status_code == 200
    avatar = client.patch(
        f"/v1/team/agents/{agent['agent_id']}",
        json={"avatar_id": "12"},
    )
    assert avatar.status_code == 200
    assert avatar.json()["definition"]["avatar_id"] == "12"
    listed_agent = next(item for item in client.get("/v1/team/agents").json()["agents"] if item["agent_id"] == agent["agent_id"])
    assert listed_agent["avatar_id"] == "12"
    assert client.get(f"/v1/team/agents/{agent['agent_id']}/export").json()["definition"]["avatar_id"] == "12"
    health = client.get(f"/v1/team/agents/{agent['agent_id']}/sandbox/health")
    assert health.status_code == 200
    assert health.json()["agent_id"] == agent["agent_id"]
    assert client.get("/v1/team/agents/agent-from-another-workspace/export").status_code == 400
    assert client.post(
        "/v1/team/agents/agent-from-another-workspace/playground",
        json={"command": ["echo", "no"]},
    ).status_code == 400
    history = client.get(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages"
    )
    assert history.status_code == 200
    message = history.json()["messages"][0]
    assert message["author_actor_id"] == agent["agent_actor_id"]
    assert message["author_display_name"] == "Reviewer"
    assert message["author_handle"] == "reviewer"

    archived = client.post(f"/v1/team/agents/{agent['agent_id']}/archive")
    assert archived.status_code == 200
    assert client.get(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages"
    ).json()["messages"][0]["author_actor_id"] == agent["agent_actor_id"]


def test_archived_agent_cannot_receive_new_or_pending_work(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="builder",
        instructions="Build safely.",
    )
    client = TestClient(create_app(manager))
    conversation = client.post(
        "/v1/team/conversations",
        json={"kind": "direct", "agent_ids": [agent["agent_id"]]},
    ).json()
    queued = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "queued before archive", "client_request_id": "archive-queue-1"},
    )
    assert queued.status_code == 200
    delivery_id = queued.json()["deliveries"][0]["delivery_id"]

    archived = client.post(f"/v1/team/agents/{agent['agent_id']}/archive")
    assert archived.status_code == 200
    assert manager.team_coordinator.claim_next(agent["agent_id"], "worker-after-archive") is None
    assert manager.team_work.get_delivery(delivery_id)["status"] == "pending"

    after_archive = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "new after archive", "client_request_id": "archive-new-1"},
    )
    assert after_archive.status_code == 200
    assert after_archive.json()["routing"]["target_agent_ids"] == []
    assert after_archive.json()["request"] is None

    deleted = client.post(f"/v1/team/agents/{agent['agent_id']}/delete")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert deleted.json()["cancellation"]["canceled_request_ids"]
    assert agent["agent_id"] not in {
        item["agent_id"] for item in client.get("/v1/team/agents").json()["agents"]
    }
    assert client.get(f"/v1/team/agents/{agent['agent_id']}/export").status_code == 400


def test_canonical_team_tasks_search_notifications_and_event_catchup(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="builder",
        instructions="Build safely.",
    )
    client = TestClient(create_app(manager))

    task = client.post(
        "/v1/team/tasks",
        json={
            "title": "Ship Team search",
            "description": "Index canonical messages.",
            "acceptance_criteria": "Search returns the exact task.",
            "owner_agent_id": agent["agent_id"],
            "reviewer_actor_id": manager.team_actor_id,
        },
    )
    assert task.status_code == 200
    task_id = task.json()["task_id"]
    assert client.post(
        f"/v1/team/tasks/{task_id}/transition", json={"status": "in_progress"}
    ).json()["status"] == "in_progress"
    assert client.post(
        f"/v1/team/tasks/{task_id}/transition", json={"status": "review"}
    ).json()["status"] == "review"
    reviewed = client.post(
        f"/v1/team/tasks/{task_id}/review",
        json={"approved": True, "comment": "Verified by the owner."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "done"

    first = client.post(
        "/v1/team/tasks",
        json={"title": "Dependency", "acceptance_criteria": "done"},
    ).json()
    dependency = client.post(
        f"/v1/team/tasks/{task_id}/dependencies",
        json={"depends_on_task_id": first["task_id"]},
    )
    assert dependency.status_code == 200
    assert dependency.json()["depends_on_task_id"] == first["task_id"]

    conversation = client.post(
        "/v1/team/conversations",
        json={"kind": "direct", "title": "Search discussion", "agent_ids": [agent["agent_id"]]},
    ).json()
    client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "searchable Team note", "client_request_id": "search-note-1"},
    )
    search = client.get("/v1/team/search", params={"q": "searchable"})
    assert search.status_code == 200
    assert search.json()["messages"]["results"][0]["conversation_id"] == conversation["conversation_id"]
    assert search.json()["tasks"]["results"] == []
    directory_search = client.get(
        "/v1/team/search",
        params=[("q", "Builder"), ("kind", "agents")],
    )
    assert directory_search.status_code == 200
    assert directory_search.json()["agents"]["results"][0]["agent_id"] == agent["agent_id"]
    conversation_search = client.get(
        "/v1/team/search",
        params=[("q", "Search discussion"), ("kind", "conversations")],
    )
    assert conversation_search.status_code == 200
    assert conversation_search.json()["conversations"]["results"][0]["conversation_id"] == conversation["conversation_id"]

    notification = manager.team_governance.create_notification(
        "notification_server_1",
        manager.team_workspace_id,
        manager.team_actor_id,
        "task.ready",
        ResourceRef("task", task_id),
        "task-ready-1",
    )
    listed = client.get("/v1/team/notifications", params={"unread_only": True})
    assert listed.status_code == 200
    assert listed.json()["notifications"][0]["notification_id"] == notification["notification_id"]
    assert client.post(f"/v1/team/notifications/{notification['notification_id']}/read").json()["read_at"]

    events = client.get("/v1/team/events")
    assert events.status_code == 200
    assert {event["event_type"] for event in events.json()["events"]} >= {
        "task.created",
        "task.transitioned",
        "message.created",
    }
    activity = client.get("/v1/team/activity")
    assert activity.status_code == 200
    assert any(item["event_type"] == "message.created" for item in activity.json()["activities"])
    assert all(item["activity_id"] == item["event_id"] for item in activity.json()["activities"])
    with client.websocket_connect("/ws/team") as ws:
        catchup = ws.receive_json()
        assert catchup["type"] == "team_catchup"
        assert catchup["data"]["events"]

    memory = client.post(
        "/v1/team/context/memories",
        json={
            "scope": "direct_message",
            "scope_id": conversation["conversation_id"],
            "content": "keep this requirement in context",
        },
    )
    assert memory.status_code == 200
    package = client.get(
        f"/v1/team/conversations/{conversation['conversation_id']}/context"
    )
    assert package.status_code == 200
    assert package.json()["memories"][0]["content"] == "keep this requirement in context"


def test_team_approval_api_enforces_owner_decision_and_notifies_requester(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    other = ActorRef("actor_approval_requester", ActorKind.HUMAN, "Requester")
    manager.team_identity.create_actor(other, handle="approval-requester")
    manager.team_identity.add_membership(
        "membership_approval_requester",
        manager.team_workspace_id,
        other.id,
        "member",
    )
    approval = manager.team_governance.create_approval(
        "approval_api_1",
        manager.team_workspace_id,
        other.id,
        "connector.send",
        ResourceRef("connector", "github"),
        "Publish the reviewed report.",
    )
    client = TestClient(create_app(manager))

    listed = client.get("/v1/team/approvals", params={"status": "pending"})
    assert listed.status_code == 200
    assert listed.json()["approvals"] == [approval]
    assert client.get("/v1/team/approvals/approval_api_1").json() == approval

    decided = client.post(
        "/v1/team/approvals/approval_api_1/decide",
        json={"status": "approved"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    requester_notifications = manager.team_governance.list_notifications(
        manager.team_workspace_id, other.id
    )["notifications"]
    assert requester_notifications[0]["aggregate_id"] == "approval_api_1"

    manager.team_service.actor_id = other.id
    forbidden = client.post(
        "/v1/team/approvals/approval_api_1/decide",
        json={"status": "denied"},
    )
    assert forbidden.status_code == 400
    assert "cannot perform approval.decide" in forbidden.json()["error"]


def test_team_commands_keep_search_events_and_notifications_in_sync(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    other = ActorRef("actor_other_human", ActorKind.HUMAN, "Other")
    manager.team_identity.create_actor(other, handle="other")
    manager.team_identity.add_membership(
        "membership_other_human",
        manager.team_workspace_id,
        other.id,
        "member",
    )
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="builder",
        instructions="Build safely.",
    )
    second_agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Reviewer",
        handle="reviewer",
        instructions="Review changes safely.",
    )

    client = TestClient(create_app(manager))
    conversation = client.post(
        "/v1/team/conversations",
        json={"kind": "group", "title": "Release", "agent_ids": [agent["agent_id"], second_agent["agent_id"]]},
    ).json()
    manager.team_conversations.add_member(
        conversation["conversation_id"],
        other.id,
        requester_actor_id=manager.team_actor_id,
    )

    sent = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "old searchable note", "client_request_id": "sync-note-1"},
    )
    assert sent.status_code == 200
    message_id = sent.json()["message"]["message_id"]
    notifications = manager.team_governance.list_notifications(
        manager.team_workspace_id, other.id
    )["notifications"]
    assert notifications[0]["aggregate_id"] == message_id
    duplicate = client.post(
        f"/v1/team/conversations/{conversation['conversation_id']}/messages",
        json={"body": "old searchable note", "client_request_id": "sync-note-1"},
    )
    assert duplicate.status_code == 200
    assert len(
        manager.team_governance.list_notifications(
            manager.team_workspace_id, other.id
        )["notifications"]
    ) == 1

    edited = client.post(
        f"/v1/team/messages/{message_id}/edit",
        json={"body": "new searchable note", "expected_revision": 1},
    )
    assert edited.status_code == 200
    assert client.get("/v1/team/search", params={"q": "old searchable"}).json()["messages"]["results"] == []
    assert client.get("/v1/team/search", params={"q": "new searchable"}).json()["messages"]["results"]

    deleted = client.delete(f"/v1/team/messages/{message_id}")
    assert deleted.status_code == 200
    assert client.get("/v1/team/search", params={"q": "new searchable"}).json()["messages"]["results"] == []

    archived = client.post(f"/v1/team/conversations/{conversation['conversation_id']}/archive")
    assert archived.status_code == 200
    event_types = {event["event_type"] for event in client.get("/v1/team/events").json()["events"]}
    assert {"conversation.created", "message.edited", "message.deleted", "conversation.transitioned"} <= event_types


def test_team_service_rejects_member_conversation_from_another_workspace(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    other_workspace = "workspace-outside-local-team"
    manager.team_identity.create_workspace(other_workspace, "Outside workspace")
    manager.team_identity.add_membership(
        "membership-outside-owner",
        other_workspace,
        human.id,
        "owner",
    )
    manager.team_conversations.create_conversation(
        "conversation-outside-local-team",
        other_workspace,
        "direct",
        human.id,
        reply_mode="coordinator",
    )
    manager.team_conversations.add_member(
        "conversation-outside-local-team",
        human.id,
    )
    manager.team_conversations.append_message(
        "message-outside-local-team",
        "conversation-outside-local-team",
        human,
        "must remain outside the local Team",
        "outside-message-key",
        "outside-message-trace",
    )

    with pytest.raises(ContractError, match="conversation not found"):
        manager.team_service.get_conversation("conversation-outside-local-team")
    with pytest.raises(ContractError, match="message not found"):
        manager.team_service.get_message("message-outside-local-team")


def test_team_event_stream_filters_private_conversation_events_per_member(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    human = ActorRef(manager.team_actor_id, ActorKind.HUMAN, "You")
    other = ActorRef("actor_event_stream_member", ActorKind.HUMAN, "Other")
    manager.team_identity.create_actor(other, handle="event-stream-member")
    manager.team_identity.add_membership(
        "membership-event-stream-member",
        manager.team_workspace_id,
        other.id,
        "member",
    )
    agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Builder",
        handle="event-stream-builder",
        instructions="Build safely.",
    )
    second_agent = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=human,
        display_name="Reviewer",
        handle="reviewer",
        instructions="Review changes safely.",
    )


    shared = manager.team_service.create_conversation(
        kind="group",
        title="Shared room",
        agent_ids=[agent["agent_id"], second_agent["agent_id"]],
    )
    manager.team_conversations.add_member(
        shared["conversation_id"],
        other.id,
        requester_actor_id=human.id,
    )
    shared_message = manager.team_service.send_message(
        shared["conversation_id"],
        body="visible to the second member",
        client_request_id="event-stream-shared-message",
    )

    private = manager.team_service.create_conversation(
        kind="direct",
        title="Private room",
        agent_ids=[agent["agent_id"]],
    )
    private_message = manager.team_service.send_message(
        private["conversation_id"],
        body="must stay private",
        client_request_id="event-stream-private-message",
    )

    manager.team_service.actor_id = other.id
    page = manager.team_service.list_events(limit=200)
    event_conversation_ids = {
        event.get("payload", {}).get("conversation_id")
        for event in page["events"]
        if isinstance(event.get("payload"), dict)
    }

    assert shared_message.message is not None
    assert private_message.message is not None
    assert shared["conversation_id"] in event_conversation_ids
    assert private["conversation_id"] not in event_conversation_ids
    assert all(
        private_message.message["message_id"] != event.get("aggregate_id")
        for event in page["events"]
    )
    if page["events"]:
        assert page["next_sequence"] >= page["events"][-1]["sequence"]


def test_disable_persona_archives_its_sessions(tmp_path):
    """Disable = "put this coworker and its history away": the persona's real sessions are
    archived atomically server-side (so its sidebar section disappears with it), internal
    __run__ threads and other personas are untouched, and re-enable never unarchives."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    store = manager.session_store

    def mk(sid, agent):
        store.save(
            SessionRecord(
                session_id=sid,
                workspace=str(tmp_path),
                model="m",
                mode="interactive",
                agent=agent,
            )
        )

    mk("chat-a", "code")
    mk("chat-b", "code")
    mk("chat-old", "code")
    store.set_flags(
        "chat-old", archived=True
    )  # already archived — must not be re-counted
    mk("cowork-a", "cowork")
    mk("__run__r1", "code")  # internal automation thread — never touched

    client = TestClient(create_app(manager))
    body = client.post("/v1/personas/code", json={"enabled": False}).json()
    assert body["ok"] is True
    assert body["archived_sessions"] == 2
    assert store.load("chat-a").archived and store.load("chat-b").archived
    assert store.load("cowork-a").archived is False
    assert store.load("__run__r1").archived is False

    # Re-enable brings the persona back but never rewrites the user's archive state.
    client.post("/v1/personas/chat", json={"enabled": True})
    assert store.load("chat-a").archived

    # The dedicated §5/§8 enable route shares the same semantic.
    mk("chat-c", "code")
    client.post("/v1/personas/code/enable", json={"enabled": False})
    assert store.load("chat-c").archived


def test_connector_tool_settings_and_audit_rest(tmp_path):
    client = _client(tmp_path, [])
    connectors = {
        c["name"]: c for c in client.get("/v1/connectors").json()["connectors"]
    }
    assert any(t["name"] == "browser_open_url" for t in connectors["browser"]["tools"])

    res = client.patch(
        "/v1/connectors/browser/tools", json={"enabled": {"browser_open_url": False}}
    ).json()
    assert res["ok"] is True
    connectors = {
        c["name"]: c for c in client.get("/v1/connectors").json()["connectors"]
    }
    browser_tools = {t["name"]: t for t in connectors["browser"]["tools"]}
    assert browser_tools["browser_open_url"]["enabled"] is False

    assert client.get("/v1/audit", params={"session_id": "none"}).json()["events"] == []
    assert client.get("/v1/browser/state").json()["status"] in {
        "closed",
        "open",
        "error",
    }


def test_artifacts_list_and_read_previewable_files(tmp_path):
    (tmp_path / "brief.md").write_text("# Brief\n\nHello", encoding="utf-8")
    (tmp_path / "page.html").write_text("<h1>Preview</h1>", encoding="utf-8")
    (tmp_path / ".secret.md").write_text("hidden", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.md").write_text("skip", encoding="utf-8")

    client = _client(tmp_path, [])
    artifacts = client.get("/v1/sessions/unknown/artifacts").json()["artifacts"]
    by_path = {a["path"]: a for a in artifacts}

    assert by_path["brief.md"]["kind"] == "markdown"
    assert by_path["page.html"]["kind"] == "html"
    assert ".secret.md" not in by_path
    assert "node_modules/noise.md" not in by_path

    md = client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": "brief.md"}
    ).json()
    assert md["ok"] is True
    assert md["kind"] == "markdown"
    assert md["content"].startswith("# Brief")

    html = client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": "page.html"}
    ).json()
    assert html["ok"] is True
    assert html["kind"] == "html"
    assert "<h1>Preview</h1>" in html["content"]


def test_video_artifact_is_listed_and_streamed_in_ranges(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"0123456789")

    client = _client(tmp_path, [])
    artifacts = client.get("/v1/sessions/unknown/artifacts").json()["artifacts"]
    assert {a["path"]: a["kind"] for a in artifacts}["clip.mp4"] == "video"

    metadata = client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": "clip.mp4"}
    ).json()
    assert metadata == {"ok": True, "path": "clip.mp4", "kind": "video"}

    full = client.get(
        "/v1/sessions/unknown/artifacts/video", params={"path": "clip.mp4"}
    )
    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "video/mp4"

    partial = client.get(
        "/v1/sessions/unknown/artifacts/video",
        params={"path": "clip.mp4"},
        headers={"Range": "bytes=2-5"},
    )
    assert partial.status_code == 206
    assert partial.content == b"2345"
    assert partial.headers["content-range"] == "bytes 2-5/10"

    suffix = client.get(
        "/v1/sessions/unknown/artifacts/video",
        params={"path": "clip.mp4"},
        headers={"Range": "bytes=-3"},
    )
    assert suffix.status_code == 206
    assert suffix.content == b"789"


def test_artifact_read_folder_returns_listing(tmp_path):
    """A linked directory (e.g. a skill package dir) renders as a listing, never a dead
    'not found' (owner report 2026-07-27). Dirs first, then files, sizes on files only."""
    pkg = tmp_path / "directory-statistics"
    pkg.mkdir()
    (pkg / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    (pkg / "stats.py").write_text("print(1)", encoding="utf-8")
    (pkg / "examples").mkdir()

    client = _client(tmp_path, [])
    res = client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": "directory-statistics"}
    ).json()
    assert res["ok"] is True and res["kind"] == "folder"
    names = [e["name"] for e in res["entries"]]
    assert names == ["examples", "SKILL.md", "stats.py"]  # dirs first, then files by name
    assert res["entries"][0]["dir"] is True
    assert res["entries"][2]["size"] > 0

    # A genuinely missing path keeps a friendly, non-jargon error.
    missing = client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": "nope.md"}
    ).json()
    assert missing["ok"] is False
    assert "moved or deleted" in missing["error"]


def test_artifact_read_rejects_path_escape(tmp_path):
    client = _client(tmp_path, [])
    escaped = client.get(
        "/v1/sessions/unknown/artifacts/read", params={"path": "../outside.md"}
    ).json()
    assert escaped["ok"] is False
    assert "escapes" in escaped["error"]


def test_sessions_hide_scheduled_internal_runs(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    manager.session_store.save(
        SessionRecord(
            session_id="normal",
            workspace=str(tmp_path),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "normal task"}],
            title="Normal task",
            agent="cowork",
        )
    )
    manager.session_store.save(
        SessionRecord(
            session_id="__run__daily-news-1",
            workspace=str(tmp_path),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "scheduled run"}],
            title="Daily news briefing",
            agent="cowork",
        )
    )
    manager.session_store.save(
        SessionRecord(
            session_id="__task__daily-news",
            workspace=str(tmp_path),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "scheduled task"}],
            title="Daily news briefing",
            agent="cowork",
        )
    )
    client = TestClient(create_app(manager))
    session_ids = {
        s["session_id"] for s in client.get("/v1/sessions").json()["sessions"]
    }
    assert "normal" in session_ids
    assert "__run__daily-news-1" not in session_ids
    assert "__task__daily-news" not in session_ids


def test_sessions_can_be_renamed_and_deleted(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    manager.session_store.save(
        SessionRecord(
            session_id="rename-me",
            workspace=str(tmp_path),
            model="gpt-5.5",
            mode="interactive",
            messages=[{"role": "user", "content": "original"}],
            title="Original title",
            agent="cowork",
        )
    )
    client = TestClient(create_app(manager))

    renamed = client.patch(
        "/v1/sessions/rename-me", json={"title": "  Better title  "}
    ).json()
    assert renamed["ok"] is True
    sessions = client.get("/v1/sessions").json()["sessions"]
    assert any(
        s["session_id"] == "rename-me" and s["title"] == "Better title"
        for s in sessions
    )

    deleted = client.delete("/v1/sessions/rename-me").json()
    assert deleted["ok"] is True
    sessions = client.get("/v1/sessions").json()["sessions"]
    assert all(s["session_id"] != "rename-me" for s in sessions)
    assert client.get("/v1/sessions/rename-me/messages").json()["messages"] == []


def test_sessions_can_be_pinned_and_archived(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    for sid in ("older", "newer"):
        manager.session_store.save(
            SessionRecord(
                session_id=sid,
                workspace=str(tmp_path),
                model="gpt-5.5",
                mode="interactive",
                messages=[{"role": "user", "content": sid}],
                agent="cowork",
            )
        )
    client = TestClient(create_app(manager))

    assert (
        client.patch("/v1/sessions/older", json={"pinned": True}).json()["ok"] is True
    )
    sessions = client.get("/v1/sessions").json()["sessions"]
    assert sessions[0]["session_id"] == "older" and sessions[0]["pinned"] is True

    assert (
        client.patch("/v1/sessions/newer", json={"archived": True}).json()["ok"] is True
    )
    by_id = {s["session_id"]: s for s in client.get("/v1/sessions").json()["sessions"]}
    assert by_id["newer"]["archived"] is True

    assert (
        client.patch("/v1/sessions/older", json={"pinned": False}).json()["ok"] is True
    )
    assert (
        client.patch("/v1/sessions/newer", json={"archived": False}).json()["ok"]
        is True
    )
    by_id = {s["session_id"]: s for s in client.get("/v1/sessions").json()["sessions"]}
    assert by_id["older"]["pinned"] is False and by_id["newer"]["archived"] is False


# -- WebSocket ------------------------------------------------------------------


def _drain(ws, on_permission=None):
    """Collect event types until turn_done; optionally answer permission_required."""
    types = []
    while True:
        event = ws.receive_json()
        types.append(event["type"])
        if event["type"] == "permission_required" and on_permission:
            ws.send_json({"type": "approval", "decision": on_permission})
        if event["type"] == "turn_done":
            return types


def test_ws_simple_turn(tmp_path):
    client = _client(tmp_path, [_text("done thinking")])
    with client.websocket_connect("/ws/session/s1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello"})
        types = _drain(ws)
        assert "assistant_message" in types
        assert "turn_end" in types


def test_ws_rejects_oversized_message(tmp_path):
    from coworker.server import app as app_mod
    from coworker.attachments import MAX_ATTACHMENTS

    client = _client(tmp_path, [_text("should not run")])
    with client.websocket_connect("/ws/session/big") as ws:
        assert ws.receive_json()["type"] == "ready"

        # Oversized text → single input-rejected frame, no turn runs.
        ws.send_json(
            {"type": "user_message", "text": "x" * (app_mod._MAX_MESSAGE_TEXT_CHARS + 1)}
        )
        evt = ws.receive_json()
        assert evt["type"] == "input_rejected"
        assert "too long" in evt["data"]["error"].lower()

        # The ingress cap is the same cap the attachment builder enforces.
        assert app_mod._MAX_ATTACHMENTS == MAX_ATTACHMENTS
        ws.send_json(
            {
                "type": "user_message",
                "text": "hi",
                "attachments": ["a"] * (app_mod._MAX_ATTACHMENTS + 1),
            }
        )
        evt = ws.receive_json()
        assert evt["type"] == "input_rejected"
        assert "attachment" in evt["data"]["error"].lower()

        # A normal message still works afterwards (the socket wasn't torn down).
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_done" in _drain(ws)


def test_ws_rejects_malformed_payloads_without_killing_socket(tmp_path):
    client = _client(tmp_path, [_text("normal")])
    with client.websocket_connect("/ws/session/malformed") as ws:
        assert ws.receive_json()["type"] == "ready"

        invalid = [
            [],
            {"type": "user_message", "text": ["not", "text"]},
            {"type": "user_message", "text": "x", "attachments": {}},
            {
                "type": "user_message",
                "text": "x",
                "attachments": [{"kind": "image", "data_url": "https://example.com/x"}],
            },
            {"type": "set_model", "model": {"unexpected": True}},
            {"type": "unknown"},
        ]
        for payload in invalid:
            ws.send_json(payload)
            evt = ws.receive_json()
            assert evt["type"] == "input_rejected"

        ws.send_json({"type": "user_message", "text": "still works"})
        assert "turn_done" in _drain(ws)


def test_ws_allows_only_one_inflight_turn_per_session(tmp_path):
    import threading
    import time

    class SlowProvider(ProviderClient):
        def __init__(self):
            self._lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def complete(self, *, model, messages, tools=None, **settings):
            # The fire-and-forget auto-title completion legitimately runs CONCURRENTLY
            # with the chat turn (it fires at turn start, owner catch 2026-08-24) — the
            # invariant under test is one CHAT turn at a time, so exclude title calls.
            if messages and "title chat sessions" in str(messages[0].get("content", "")):
                return _text("A Title")
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.08)
                return _text("done")
            finally:
                with self._lock:
                    self.active -= 1

        def capabilities(self, model):
            return ModelCapabilities()

    provider = SlowProvider()
    manager = SessionManager(workspace=tmp_path, provider=provider)
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/serialized") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "first"})
        ws.send_json({"type": "user_message", "text": "second"})

        types = []
        while "turn_done" not in types:
            types.append(ws.receive_json()["type"])

    assert "input_rejected" in types
    assert provider.max_active == 1
    engine = manager._engines["serialized"]
    user_messages = [m for m in engine.messages if m.get("role") == "user"]
    assert [m["content"] for m in user_messages] == ["first"]


def test_ws_rate_limits_inbound_frames(tmp_path):
    from coworker.server import app as app_mod
    from starlette.websockets import WebSocketDisconnect

    client = _client(tmp_path, [])
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/session/rate") as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(app_mod._WS_RATE_LIMIT_COUNT):
                ws.send_json({"type": "unknown"})
                assert ws.receive_json()["type"] == "input_rejected"
            ws.send_json({"type": "unknown"})
            assert ws.receive_json()["type"] == "input_rejected"
            ws.receive_json()


def test_server_sets_explicit_websocket_frame_limit(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from coworker.server import run as server_run

    seen = {}
    fake_app = object()

    monkeypatch.setattr(server_run, "_ensure_ca_bundle", lambda: None)
    monkeypatch.setattr(server_run, "_exit_when_orphaned", lambda: None)
    monkeypatch.setattr(server_run, "build_app", lambda *args: fake_app)
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda app, **kwargs: seen.update(app=app, **kwargs)),
    )

    server_run.main(["--cwd", str(tmp_path), "--port", "8766"])

    assert seen["app"] is fake_app
    assert seen["ws_max_size"] == server_run._WS_MAX_FRAME_BYTES


def test_standalone_server_token_file_is_user_only(tmp_path, monkeypatch):
    import os

    from coworker.server import run as server_run

    monkeypatch.delenv("COWORKER_API_TOKEN", raising=False)
    path = server_run._ensure_api_token(9876)
    try:
        assert path == tmp_path / "coworker-state" / "sidecar-9876.token"
        assert path.read_text().strip() == os.environ["COWORKER_API_TOKEN"]
        assert len(path.read_text().strip()) == 64
        assert (path.stat().st_mode & 0o777) == 0o600
    finally:
        path.unlink(missing_ok=True)
        os.environ.pop("COWORKER_API_TOKEN", None)


def test_ws_error_persists_notice_and_retry_reruns(tmp_path):
    class FlakyProvider(ProviderClient):
        def __init__(self):
            self.calls = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("outage")
            return _text("recovered")

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(workspace=tmp_path, provider=FlakyProvider())
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/flaky") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "error" in _drain(ws)
        # The error survives as a persisted notice (reload shows what happened)…
        messages = client.get("/v1/sessions/flaky/messages").json()["messages"]
        assert messages[-1]["role"] == "notice" and messages[-1]["kind"] == "error"
        # …and retry re-runs the turn without a new user message.
        ws.send_json({"type": "retry"})
        types = _drain(ws)
        assert "turn_start" in types and "assistant_message" in types
    messages = client.get("/v1/sessions/flaky/messages").json()["messages"]
    assert messages[-1]["role"] == "assistant" and messages[-1]["content"] == "recovered"
    assert sum(1 for m in messages if m["role"] == "user") == 1


# -- origin gate (local-API hardening): a browser page on a foreign origin must not be able to
# read the API cross-origin or open the driving WebSocket. -------------------------------------


def test_cors_rejects_foreign_origin(tmp_path):
    client = _client(tmp_path, [])
    # A random website's origin gets no ACAO header, so the browser blocks the read.
    resp = client.get("/v1/sessions", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    # The desktop webview's own origin is allowed.
    ok = client.get("/v1/sessions", headers={"Origin": "tauri://localhost"})
    assert ok.headers.get("access-control-allow-origin") == "tauri://localhost"
    # Localhost dev/browser build is allowed too.
    dev = client.get("/v1/sessions", headers={"Origin": "http://localhost:1420"})
    assert dev.headers.get("access-control-allow-origin") == "http://localhost:1420"


def test_ws_rejects_foreign_origin(tmp_path):
    from starlette.websockets import WebSocketDisconnect as WSD

    client = _client(tmp_path, [_text("hi")])
    with pytest.raises(WSD) as e:
        with client.websocket_connect(
            "/ws/session/x", headers={"Origin": "https://evil.example"}
        ) as ws:
            ws.receive_json()
    assert e.value.code == 1008


def test_ws_allows_webview_origin(tmp_path):
    client = _client(tmp_path, [_text("hi")])
    with client.websocket_connect(
        "/ws/session/x", headers={"Origin": "http://tauri.localhost"}
    ) as ws:
        assert ws.receive_json()["type"] == "ready"


def test_sidecar_token_gates_rest_and_websockets(tmp_path, monkeypatch):
    from coworker.mcp.config import global_mcp_path
    from starlette.websockets import WebSocketDisconnect as WSD

    monkeypatch.setenv("COWORKER_API_TOKEN", "a" * 64)
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    client = TestClient(create_app(manager))

    assert client.get("/v1/health").json() == {"status": "ok"}
    assert client.get("/v1/sessions").status_code == 401
    assert client.get(
        "/v1/sessions", headers={"X-Astra-Token": "wrong"}
    ).status_code == 401

    astra_headers = {"X-Astra-Token": "a" * 64}
    assert client.get("/v1/sessions", headers=astra_headers).status_code == 200
    assert client.get("/v1/health", headers=astra_headers).json()[
        "default_workspace"
    ] == str(tmp_path.resolve())

    rejected = client.post(
        "/v1/mcp",
        json={"name": "evil", "config": {"command": "sh", "args": ["-c", "id"]}},
    )
    assert rejected.status_code == 401
    assert not global_mcp_path().exists()

    with pytest.raises(WSD) as denied:
        with client.websocket_connect("/ws/session/tokenless") as ws:
            ws.receive_json()
    assert denied.value.code == 1008

    with client.websocket_connect(
        "/ws/session/astra", subprotocols=["astra", "a" * 64]
    ) as ws:
        assert ws.accepted_subprotocol == "astra"
        assert ws.receive_json()["type"] == "ready"

    with client.websocket_connect(
        "/ws/events", subprotocols=["astra", "a" * 64]
    ) as ws:
        assert ws.accepted_subprotocol == "astra"

    # Redirect callbacks remain tokenless, then enforce their own signed state.
    assert client.get(
        "/auth/callback", params={"code": "x", "state": "bad"}
    ).status_code == 400
    assert client.get("/mcp/oauth/callback").status_code == 400
    assert client.post("/oauth/callback", data={"app_state": "bad"}).status_code == 400


def test_ws_approval_round_trip(tmp_path):
    client = _client(
        tmp_path,
        [
            _tool("write_file", {"path": "made.py", "content": "print(1)\n"}),
            _text("wrote it"),
        ],
    )
    with client.websocket_connect("/ws/session/s2") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "create made.py"})
        types = _drain(ws, on_permission="once")
        assert "permission_required" in types
        assert "tool_finished" in types
    assert (tmp_path / "made.py").read_text() == "print(1)\n"


def test_ws_session_persisted_while_parked_on_approval(tmp_path):
    """A crash mid-turn must not eat the conversation: by the time the engine parks on an
    approval, the session (user message + assistant tool call) is already on disk."""
    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider(
            [_tool("write_file", {"path": "x.py", "content": "1\n"}), _text("done")]
        ),
    )
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/persist1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "make x.py"})
        while ws.receive_json()["type"] != "permission_required":
            pass
        # Parked on the approval — nothing approved, turn far from done. Already saved?
        rec = manager.session_store.load("persist1")
        assert rec is not None
        roles = [m.get("role") for m in rec.messages]
        assert "user" in roles  # turn_start checkpoint
        assert "assistant" in roles  # iteration progress checkpoint
        ws.send_json({"type": "approval", "decision": "deny"})
        while ws.receive_json()["type"] != "turn_done":
            pass


def test_ws_browser_tool_audit_round_trip(tmp_path):
    client = _client(tmp_path, [_tool("browser_close", {}), _text("closed")])
    with client.websocket_connect("/ws/session/browser-audit?agent=cowork") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "close browser"})
        types = _drain(ws, on_permission="once")
        assert "permission_required" in types
        assert "tool_finished" in types

    rows = client.get(
        "/v1/audit", params={"session_id": "browser-audit", "connector": "browser"}
    ).json()["events"]
    assert any(
        r["tool"] == "browser_close" and r["stage"] == "approval_resolved" for r in rows
    )
    assert any(r["tool"] == "browser_close" and r["stage"] == "finished" for r in rows)


def test_open_and_recent_workspaces(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    client = _client(tmp_path, [])
    opened = client.post("/v1/workspaces/open", json={"path": str(proj)}).json()
    assert opened["ok"] is True
    recents = client.get("/v1/workspaces/recent").json()["workspaces"]
    assert any(w["path"] == str(proj.resolve()) for w in recents)


def test_workspace_command_trust_controls_live_engine(tmp_path):
    from urllib.parse import quote

    proj = tmp_path / "trusted-project"
    (proj / ".coworker").mkdir(parents=True)
    (proj / ".coworker" / "config.toml").write_text(
        'allowed_commands = ["pytest"]\nauto_allow = ["write_file"]\n'
    )
    manager = SessionManager(
        workspace=None, data_dir=tmp_path / "data", provider=ScriptedProvider([])
    )
    client = TestClient(create_app(manager))

    with client.websocket_connect(
        f"/ws/session/trust?workspace={quote(str(proj))}"
    ) as ws:
        ready = ws.receive_json()
        policy = ready["data"]["command_trust"]
        assert policy["required"] is True
        assert policy["requested_commands"] == ["pytest"]

        engine = manager._engines["trust"]
        before = engine.permissions.evaluate(
            "run_shell", {"command": "pytest -q"}, None
        )
        assert not before.allowed and before.needs_user
        # Workspace auto_allow remains ignored even after command trust.
        assert "write_file" not in engine.permissions.auto_allow_tools

        trusted = client.post(
            "/v1/workspaces/trust",
            json={"path": str(proj), "trusted": True},
        ).json()
        assert trusted["ok"] and trusted["trusted"]
        assert engine.permissions.evaluate(
            "run_shell", {"command": "pytest -q"}, None
        ).allowed

        listed = client.get("/v1/workspaces/trusted").json()["workspaces"]
        assert [item["workspace"] for item in listed] == [str(proj.resolve())]

        revoked = client.post(
            "/v1/workspaces/trust",
            json={"path": str(proj), "trusted": False},
        ).json()
        assert revoked["ok"] and not revoked["trusted"]
        after = engine.permissions.evaluate(
            "run_shell", {"command": "pytest -q"}, None
        )
        assert not after.allowed and after.needs_user

    manager.workspace_trust.set_trusted(proj, True)
    proj.rename(tmp_path / "moved-project")
    assert client.post(
        "/v1/workspaces/trust",
        json={"path": str(proj), "trusted": False},
    ).json()["ok"]
    assert manager.trusted_workspaces() == []


def test_recent_workspaces_exclude_scratch_dirs(tmp_path):
    # Scratch dirs get touched like any workspace, but must never show up as
    # "recent projects" in the folder gate (owner call, 2026-07-03).
    from coworker.server.manager import SessionManager

    proj = tmp_path / "real-project"
    proj.mkdir()
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr._prefs["scratch_base"] = str(tmp_path / "scratch")
    scratch = mgr._provision_scratch("sess-1")
    mgr.session_store.touch_workspace(str(proj.resolve()))
    mgr.session_store.touch_workspace(scratch)
    paths = [w["path"] for w in mgr.recent_workspaces()]
    assert str(proj.resolve()) in paths
    assert scratch not in paths


def test_delete_session_removes_its_scratch_dir_only(tmp_path):
    # Deleting a session also deletes its per-conversation scratch dir (owner call,
    # 2026-07-03) — but NEVER a real project folder the user picked.
    from pathlib import Path

    from coworker.server.manager import SessionManager
    from coworker.sessions import SessionRecord

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    mgr._prefs["scratch_base"] = str(tmp_path / "scratch")

    scratch = Path(mgr._provision_scratch("sess-scratch"))
    mgr.session_store.save(
        SessionRecord(
            session_id="sess-scratch",
            workspace=str(scratch),
            model="m",
            mode="interactive",
        )
    )
    assert mgr.delete_session("sess-scratch")["ok"]
    assert not scratch.exists()

    proj = tmp_path / "real-project"
    proj.mkdir()
    mgr.session_store.save(
        SessionRecord(
            session_id="sess-proj", workspace=str(proj), model="m", mode="interactive"
        )
    )
    assert mgr.delete_session("sess-proj")["ok"]
    assert proj.is_dir()  # user folders are sacred


def test_open_invalid_workspace(tmp_path):
    client = _client(tmp_path, [])
    bad = client.post(
        "/v1/workspaces/open", json={"path": str(tmp_path / "nope")}
    ).json()
    assert bad["ok"] is False


def test_open_workspace_create(tmp_path):
    client = _client(tmp_path, [])
    fresh = tmp_path / "fresh-project"
    assert not fresh.exists()
    res = client.post(
        "/v1/workspaces/open", json={"path": str(fresh), "create": True}
    ).json()
    assert res["ok"] is True
    assert fresh.is_dir()


def test_ws_requires_workspace_when_no_default(tmp_path):
    # Manager with no default workspace: a session with no folder is rejected.
    manager = SessionManager(
        workspace=None, data_dir=tmp_path, provider=ScriptedProvider([])
    )
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/nofolder") as ws:
        first = ws.receive_json()
        assert first["type"] == "error"
        assert "workspace" in first["data"]["error"]


def test_ws_with_workspace_query(tmp_path):
    from urllib.parse import quote

    proj = tmp_path / "proj"
    proj.mkdir()
    manager = SessionManager(
        workspace=None,
        data_dir=tmp_path,
        provider=ScriptedProvider([_text("hi from proj")]),
    )
    client = TestClient(create_app(manager))
    with client.websocket_connect(f"/ws/session/s?workspace={quote(str(proj))}") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["data"]["workspace"] == str(proj.resolve())
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_end" in _drain(ws)


def test_ws_removed_agent_id_falls_back_to_default(tmp_path):
    # Chat is removed (owner 2026-08-21): a stored session or deep link carrying
    # agent=chat resolves to the default persona instead of erroring.
    manager = SessionManager(
        workspace=None,
        data_dir=tmp_path,
        provider=ScriptedProvider([_text("hi")]),
    )
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/chat1?agent=chat") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["data"]["agent"] == "cowork"
        ws.send_json({"type": "user_message", "text": "hello"})
        assert "turn_end" in _drain(ws)


def test_ws_set_mode_auto_skips_approval(tmp_path):
    from urllib.parse import quote

    proj = tmp_path / "proj"
    proj.mkdir()
    manager = SessionManager(
        workspace=None,
        data_dir=tmp_path,
        provider=ScriptedProvider(
            [_tool("write_file", {"path": "a.py", "content": "x"}), _text("done")]
        ),
    )
    client = TestClient(create_app(manager))
    with client.websocket_connect(f"/ws/session/sm?workspace={quote(str(proj))}") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "set_mode", "mode": "auto"})
        ws.send_json({"type": "user_message", "text": "write a.py"})
        types = _drain(ws)  # no approval handler — would hang if it asked
        assert "permission_required" not in types
    assert (proj / "a.py").read_text() == "x"


def test_ws_session_resume_via_store(tmp_path):
    # First connection runs a turn and persists the session.
    client = _client(tmp_path, [_text("first answer")])
    with client.websocket_connect("/ws/session/keep") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "remember this"})
        _drain(ws)
    # The session is now listed via REST.
    sessions = client.get("/v1/sessions").json()["sessions"]
    assert any(s["session_id"] == "keep" and s["messages"] > 0 for s in sessions)


def test_ws_first_message_binds_then_midsession_switch_persists_notice(tmp_path):
    """The FIRST user_message's model binds the session silently (race-proof across
    reconnects — found 2026-07-04). Mid-session rebinds are ALLOWED (roadmap item 3,
    2026-07-22, supersedes the 07-04 lock): the switch lands as a persisted model_switch
    notice and a model_changed broadcast, and the next turn runs on the new model."""
    # 4 turns: 3 user turns + the autotitle's fire-and-forget complete() after turn 1.
    client = _client(
        tmp_path, [_text("ok"), _text("Session title"), _text("ok again"), _text("still ok")]
    )
    with client.websocket_connect("/ws/session/model-per-msg") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        ws.send_json({"type": "user_message", "text": "hi", "model": "zai:glm-5.2"})
        assert "model_changed" not in _drain(ws)  # first bind is silent
        # message WITHOUT a model keeps the bound one (no silent reset to default)
        ws.send_json({"type": "user_message", "text": "again"})
        _drain(ws)
        ws.send_json({"type": "set_model", "model": "kimi:kimi-k2.6"})
        changed = ws.receive_json()
        assert changed["type"] == "model_changed"
        assert changed["data"]["model"] == "kimi:kimi-k2.6"
        assert "Kimi" in changed["data"]["text"]
        ws.send_json({"type": "user_message", "text": "switched now"})
        _drain(ws)
    mgr = client.app.state.manager
    engine = mgr._engines["model-per-msg"]
    assert engine.model == "kimi:kimi-k2.6"
    # The marker is persisted between the turns; the provider never sees it.
    messages = client.get("/v1/sessions/model-per-msg/messages").json()["messages"]
    notices = [m for m in messages if m["role"] == "notice"]
    assert [n["kind"] for n in notices] == ["model_switch"]
    assert all(m.get("role") != "notice" for m in engine._outbound_messages())


def test_session_messages_prefers_the_live_engine(tmp_path):
    """Opening a RUNNING session (e.g. a scheduled automation's first turn) must show the live
    conversation: the persisted record may not exist yet mid-turn — reading only the store gave
    a blank transcript on first open (owner report, 2026-07-04)."""
    client = _client(tmp_path, [_text("ok")])
    mgr = client.app.state.manager
    engine = mgr.get_engine("__run__live", agent="chat")
    engine.messages.append({"role": "user", "content": "hi from a running automation"})

    msgs = client.get("/v1/sessions/__run__live/messages").json()["messages"]
    assert any(m.get("content") == "hi from a running automation" for m in msgs)


def test_pick_native_folder_paths(tmp_path, monkeypatch):
    """The sidecar-side folder picker (for browser GUIs): picked path round-trips; cancel and
    missing-picker degrade to ok:False without raising."""
    import subprocess
    from types import SimpleNamespace

    client = _client(tmp_path, [])
    mgr = client.app.state.manager

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=0, stdout="/tmp/picked\n", stderr=""
        ),
    )
    assert client.post("/v1/workspaces/pick").json() == {
        "ok": True,
        "path": "/tmp/picked",
    }

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="", stderr="User canceled."
        ),
    )
    assert client.post("/v1/workspaces/pick").json()["ok"] is False

    def boom(*a, **k):
        raise OSError("no zenity")

    monkeypatch.setattr(subprocess, "run", boom)
    out = mgr.pick_native_folder()
    assert out["ok"] is False and "picker" in out["error"]


def test_provider_set_and_remove_roundtrip(tmp_path):
    """Settings ▸ Models "Remove key": DELETE /v1/providers/{name} forgets the stored
    profile so the provider reads unconfigured again; unknown names are a clean error.
    """
    client = _client(tmp_path, [])
    assert client.post(
        "/v1/providers", json={"name": "zai", "fields": {"api_key": "zk-test"}}
    ).json()["ok"]
    prov = {p["name"]: p for p in client.get("/v1/providers").json()}
    assert prov["zai"]["configured"] and prov["zai"]["key_set_at"]

    assert client.delete("/v1/providers/zai").json()["ok"]
    prov = {p["name"]: p for p in client.get("/v1/providers").json()}
    assert not prov["zai"]["configured"]
    assert not prov["zai"]["key_set_at"]

    assert not client.delete("/v1/providers/nope").json()["ok"]


def test_always_allow_grants_survive_restart(tmp_path):
    """"Always allow" is session-scoped, and the session outlives the process — a restart
    (fresh manager over the same store) must not re-ask for an approved command
    (owner-hit 2026-07-22 on the 0.1.6 walkthrough)."""

    def _shell_turns():
        return ScriptedProvider(
            [
                _tool("run_shell", {"command": "uname -a"}, call_id="c1"),
                _text("done"),
            ]
        )

    def _run_turn(client, expect_prompts):
        with client.websocket_connect("/ws/session/grants1?agent=cowork") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user_message", "text": "run it"})
            asked = 0
            while True:
                ev = ws.receive_json()
                if ev["type"] == "permission_required":
                    asked += 1
                    ws.send_json({"type": "approval", "decision": "always_command"})
                if ev["type"] == "turn_done":
                    break
            assert asked == expect_prompts

    mgr = SessionManager(workspace=None, provider=_shell_turns())
    _run_turn(TestClient(create_app(mgr)), expect_prompts=1)

    # "Restart": new manager + engine rebuilt from the persisted record.
    mgr2 = SessionManager(workspace=None, provider=_shell_turns())
    _run_turn(TestClient(create_app(mgr2)), expect_prompts=0)


def test_managed_connectors_are_hidden_and_refused_by_default(tmp_path):
    """A local install offers direct credentials only unless its owner enables a broker."""
    client = _client(tmp_path, [])
    connectors = {c["name"]: c for c in client.get("/v1/connectors").json()["connectors"]}
    for name in ("gmail", "google_calendar", "google_drive"):
        c = connectors[name]
        assert c["managed"] is False and c["managed_paused"] is False
        assert c["fields"], f"{name} lost its manual fields"
    assert connectors["slack"]["managed"] is False

    refused = client.post("/v1/connectors/gmail/connect-managed", json={}).json()
    assert refused["ok"] is False and "disabled" in refused["error"]


def test_set_provider_persists_extra_fields(tmp_path):
    """Non-secret descriptor extras (ollama's endpoint) round-trip: saved into the
    profile, echoed by get_providers for form prefill, cleared by an empty save."""
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([]))
    assert manager.set_provider("ollama", {"base_url": "http://127.0.0.1:9999"})["ok"]
    providers = {p["name"]: p for p in manager.get_providers()}
    assert providers["ollama"]["values"]["base_url"] == "http://127.0.0.1:9999"

    manager.set_provider("ollama", {"base_url": ""})
    providers = {p["name"]: p for p in manager.get_providers()}
    assert "base_url" not in providers["ollama"]["values"]


def test_mcp_connect_route_flags_authorizing_immediately(tmp_path, monkeypatch):
    """Owner-hit 2026-08-21: the Test button looked dead — the connect ran as a
    background task, and the GUI's first refresh landed before the task set
    `authorizing`, so the fast poll never armed. The route must flag it
    synchronously (and only for known servers, so nothing wedges)."""
    import asyncio

    from coworker.server import SessionManager

    mgr = SessionManager(data_dir=tmp_path / "data")
    monkeypatch.setattr(
        "coworker.server.manager.read_global", lambda: {"sales-db": {"command": "x"}}
    )
    mgr.begin_mcp_connect("sales-db")
    assert "sales-db" in mgr._mcp_authorizing
    mgr.begin_mcp_connect("nope")
    assert "nope" not in mgr._mcp_authorizing
    # An unmatched name clears the flag instead of wedging "Testing…" forever.
    monkeypatch.setattr("coworker.server.manager.load_mcp_servers", lambda *a, **k: [])
    res = asyncio.run(mgr.connect_mcp("sales-db"))
    assert not res["ok"] and "sales-db" not in mgr._mcp_authorizing


def test_ws_ready_reports_live_turn(tmp_path):
    # A reconnect can land mid-turn (sidebar revisit, relaunch, dropped socket). `ready`
    # must carry server truth on the running turn or the GUI loses Stop + the waiting row
    # (owner catch 2026-08-24, v0.2.0 walkthrough).
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/live1") as ws:
        assert ws.receive_json()["data"]["running"] is False

    manager.mark_running("live1")
    try:
        with client.websocket_connect("/ws/session/live1") as ws:
            assert ws.receive_json()["data"]["running"] is True
    finally:
        manager.mark_idle("live1")


def test_set_mode_persists_notice_once_then_markers(tmp_path):
    # Owner ruling 2026-08-24: the Auto-Approve explainer is server-authored and persisted
    # ONCE per session; later switches persist one-line markers. Restarts re-show nothing.
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/modes1") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "set_mode", "mode": "auto-approve"})
        ev = ws.receive_json()
        assert ev["type"] == "mode_notice"
        assert ev["data"]["title"] == "Auto-approve is on."
        assert "uses a model" in ev["data"]["text"]
        ws.send_json({"type": "set_mode", "mode": "interactive"})
        assert ws.receive_json()["data"] == {"text": "Ask for approval is on."}
        # Re-entering auto-approve: marker, never the banner again.
        ws.send_json({"type": "set_mode", "mode": "auto-approve"})
        assert ws.receive_json()["data"] == {"text": "Auto-approve is on."}

    engine = manager._engines["modes1"]
    kinds = [m.get("kind") for m in engine.messages if m.get("role") == "notice"]
    assert kinds.count("mode_notice") == 1
    assert kinds.count("mode_switch") == 2


def test_connect_banners_a_session_already_in_auto_approve(tmp_path):
    from coworker.permissions import Mode

    manager = SessionManager(
        workspace=tmp_path,
        provider=ScriptedProvider([_text("hi")]),
        mode=Mode("auto-approve"),
    )
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/modes2") as ws:
        assert ws.receive_json()["type"] == "ready"
        ev = ws.receive_json()
        assert ev["type"] == "mode_notice" and ev["data"]["title"] == "Auto-approve is on."
    # A reconnect stays quiet: the banner is persisted (asserted below), not re-announced —
    # a set_mode echo of the SAME mode also stays silent (previous is new_mode).
    with client.websocket_connect("/ws/session/modes2") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "set_mode", "mode": "interactive"})
        assert ws.receive_json()["data"] == {"text": "Ask for approval is on."}
    engine = manager._engines["modes2"]
    kinds = [m.get("kind") for m in engine.messages if m.get("role") == "notice"]
    assert kinds.count("mode_notice") == 1


def test_browser_proxy_blocks_loopback(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
    client = TestClient(create_app(manager))
    resp = client.get("/v1/browser/proxy?url=http://127.0.0.1:8765/v1/health")
    assert resp.status_code == 403
    assert "Blocked by URL safety policy" in resp.text
    assert resp.headers.get("referrer-policy") == "no-referrer"
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_browser_proxy_blocks_cloud_metadata(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
    client = TestClient(create_app(manager))
    resp = client.get("/v1/browser/proxy?url=http://169.254.169.254/latest/meta-data/")
    assert resp.status_code == 403
    assert "Blocked by URL safety policy" in resp.text
    assert resp.headers.get("referrer-policy") == "no-referrer"


def test_browser_proxy_empty_url(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
    client = TestClient(create_app(manager))
    resp = client.get("/v1/browser/proxy?url=")
    assert resp.status_code == 400
    assert "No URL specified" in resp.text
    assert resp.headers.get("referrer-policy") == "no-referrer"


def test_browser_proxy_security_headers_on_success(tmp_path, monkeypatch):
    class _MockResponse:
        status_code = 200
        text = "<html><head><title>Test</title></head><body>Hello</body></html>"
        headers = {}

    monkeypatch.setattr(
        "coworker.web.guard.get_checked",
        lambda client, url: _MockResponse(),
    )
    manager = SessionManager(workspace=tmp_path, provider=ScriptedProvider([_text("hi")]))
    client = TestClient(create_app(manager))
    resp = client.get("/v1/browser/proxy?url=https://example.com")
    assert resp.status_code == 200
    assert '<base href="https://example.com">' in resp.text
    assert resp.headers.get("referrer-policy") == "no-referrer"
    assert resp.headers.get("x-content-type-options") == "nosniff"
