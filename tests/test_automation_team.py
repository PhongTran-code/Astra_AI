"""Canonical Team bridge tests for scheduled automations."""

from __future__ import annotations

import pytest

from coworker.server.manager import SessionManager


def _manager(tmp_path) -> SessionManager:
    return SessionManager(data_dir=tmp_path / "data")


def _agent(manager: SessionManager, *, capabilities=()) -> str:
    created = manager.team_agent_lifecycle.create(
        workspace_id=manager.team_workspace_id,
        requester=manager.team_service.requester,
        display_name="Automation Agent",
        handle="automation-agent",
        instructions="Complete the queued Team task.",
        capabilities=capabilities,
    )
    return created["agent_id"]


def test_team_automation_definition_targets_agent_without_scratch_session(tmp_path):
    manager = _manager(tmp_path)
    agent_id = _agent(manager)

    result = manager.create_automation(
        {
            "title": "Team daily brief",
            "instructions": "Prepare the daily brief.",
            "cron": "0 9 * * *",
            "team": {
                "target_kind": "agent",
                "agent_id": agent_id,
                "destination_task_id": None,
            },
        }
    )

    assert result["ok"] is True
    task = manager.task_store.get(result["task"]["id"])
    assert task is not None
    assert task.team_config["target_agent_ids"] == [agent_id]
    assert task.workspace == ""


@pytest.mark.asyncio
async def test_team_automation_trigger_is_idempotent_and_has_no_session_record(tmp_path):
    manager = _manager(tmp_path)
    agent_id = _agent(manager)
    conversation = manager.team_service.create_conversation(
        kind="direct", agent_ids=[agent_id]
    )
    manager.team_service.send_message(
        conversation["conversation_id"],
        body="Context at trigger time",
        client_request_id="context-before-trigger",
    )
    created = manager.create_automation(
        {
            "title": "Team queued brief",
            "instructions": "Prepare the queued brief.",
            "cron": "0 9 * * *",
            "team": {
                "target_kind": "agent",
                "agent_id": agent_id,
                "destination_conversation_id": conversation["conversation_id"],
            },
        }
    )
    task = manager.task_store.get(created["task"]["id"])
    assert task is not None
    task.next_run = 1.0

    first = manager._queue_team_automation_run(task, trigger="catchup", client_request_id="fire-1")
    second = manager._queue_team_automation_run(task, trigger="catchup", client_request_id="fire-1")

    assert first.run_id == second.run_id
    assert first.canonical_request_id == second.canonical_request_id
    request = manager.team_work.get_request(first.canonical_request_id)
    assert request is not None
    scope = request["permission_scope"]
    assert scope["agent_revision_ids"][agent_id]
    assert scope["sandbox_ids"][agent_id]
    assert scope["sandbox_snapshots"][agent_id]["sandbox_id"] == scope["sandbox_ids"][agent_id]
    assert scope["context_snapshot"]["agent_ids"] == [agent_id]
    assert scope["context_snapshot"]["materialized"] is True
    assert scope["context_snapshot"]["packages_by_agent"][agent_id]["conversation"]["conversation_id"] == conversation["conversation_id"]
    assert scope["agent_revision_snapshots"][agent_id]["instructions"] == "Complete the queued Team task."
    assert [item["body"] for item in scope["context_snapshot"]["packages_by_agent"][agent_id]["messages"]] == ["Context at trigger time"]
    manager.team_service.send_message(
        conversation["conversation_id"],
        body="Context after trigger",
        client_request_id="context-after-trigger",
    )
    assert [item["body"] for item in request["permission_scope"]["context_snapshot"]["packages_by_agent"][agent_id]["messages"]] == ["Context at trigger time"]
    assert manager.session_store.load(first.session_id) is None


def test_canceling_queued_team_run_projects_automation_history(tmp_path):
    manager = _manager(tmp_path)
    agent_id = _agent(manager)
    created = manager.create_automation(
        {
            "title": "Cancelable Team brief",
            "instructions": "Prepare the cancelable brief.",
            "cron": "0 9 * * *",
            "team": {"target_kind": "agent", "agent_id": agent_id},
        }
    )
    task = manager.task_store.get(created["task"]["id"])
    assert task is not None
    run = manager._queue_team_automation_run(task, trigger="catchup", client_request_id="fire-cancel")
    lease = manager._claim_canonical_team_delivery(agent_id)
    assert lease is not None
    request = manager.team_work.get_request(run.canonical_request_id)
    assert request is not None
    manager.team_coordinator.start_delivery(
        lease,
        run_id="run_cancel_projection",
        agent_revision_id=request["permission_scope"]["agent_revision_ids"][agent_id],
        sandbox_id=request["permission_scope"]["sandbox_ids"][agent_id],
    )

    canceled = manager.cancel_canonical_team_run("run_cancel_projection", reason="operator stopped it")

    assert canceled["run"]["status"] == "canceled"
    projected = manager.task_store.find_run_by_canonical_request(run.canonical_request_id)
    assert projected is not None
    assert projected.status == "skipped"
    assert projected.error == "operator stopped it"


def test_team_automation_snapshots_connector_grants_without_credentials(tmp_path):
    manager = _manager(tmp_path)
    identity = manager.team_connectors.register_identity(
        connector_kind="slack",
        external_workspace_id="T-team",
        external_identity_id="B-team",
        metadata={"access_token": "never-in-scope"},
    )
    agent_id = _agent(
        manager,
        capabilities=(
            {
                "capability_kind": "connector",
                "capability_ref": "slack",
                "required": True,
                "config": {
                    "external_identity_record_id": identity["external_identity_record_id"],
                    "actions": ["connector.read"],
                },
            },
        ),
    )
    grant = manager.team_connectors.grant_agent(
        external_identity_record_id=identity["external_identity_record_id"],
        agent_id=agent_id,
        actions=("connector.read",),
        resource_ids=("C-team",),
    )
    created = manager.create_automation(
        {
            "title": "Team connector brief",
            "instructions": "Summarize the connected channel.",
            "cron": "0 9 * * *",
            "team": {"target_kind": "agent", "agent_id": agent_id},
        }
    )
    task = manager.task_store.get(created["task"]["id"])
    assert task is not None

    run = manager._queue_team_automation_run(
        task, trigger="manual", client_request_id="connector-fire"
    )
    request = manager.team_work.get_request(run.canonical_request_id)
    assert request is not None
    snapshots = request["permission_scope"]["connector_grant_snapshots"][agent_id]
    assert snapshots == [
        {
            "connector_grant_id": grant["grant"]["connector_grant_id"],
            "external_identity_record_id": identity["external_identity_record_id"],
            "agent_id": agent_id,
            "requester_actor_id": manager.team_service.actor_id,
            "actions": ["connector.read"],
            "resource_ids": ["C-team"],
            "expires_at": None,
        }
    ]
    assert "never-in-scope" not in str(request)
