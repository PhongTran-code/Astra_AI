"""Persistent custom-agent lifecycle and safe package tests."""

import sys

import pytest

from coworker.teams import (
    ActorKind,
    ActorRef,
    AgentLifecycleService,
    AgentLifecycleError,
    AgentStatus,
    ContractError,
    IdentityRepository,
    SandboxManager,
    SandboxRepository,
    TeamDatabase,
)
from coworker.teams.persistence import AgentRepository


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")


@pytest.fixture
def service(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER)
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    service = AgentLifecycleService(
        identities,
        AgentRepository(database),
        SandboxManager(SandboxRepository(database), tmp_path / "sandboxes"),
    )
    yield service
    database.close()


def test_create_edit_playground_and_export_keep_sandbox_and_revision_identity(service):
    created = service.create(
        workspace_id="workspace_1",
        requester=OWNER,
        display_name="Research Builder",
        handle="research-builder",
        instructions="Build a verified research report.",
        base_role="builder",
        model_policy={"model": "gpt-5.6-sol"},
    )
    assert created["status"] == AgentStatus.READY.value
    assert created["isolation_level"] == "weak_local"
    old_revision = created["active_revision_id"]
    result = service.test_playground(
        created["agent_id"],
        (sys.executable, "-c", "print('playground-ok')"),
    )
    assert result.exit_code == 0
    assert "playground-ok" in result.stdout
    health = service.sandbox_health(created["agent_id"])
    assert health["agent_id"] == created["agent_id"]
    assert health["home_exists"] is True
    edited = service.edit(
        created["agent_id"],
        requester=OWNER,
        instructions="Build a verified research report and cite evidence.",
        model_policy={"model": "gpt-5.6-sol"},
    )
    assert edited["installation"]["active_revision_id"] != old_revision
    exported = service.export(created["agent_id"])
    assert exported["revision"]["instructions"].endswith("cite evidence.")
    assert "home_ref" not in exported["sandbox"]
    assert all("secret" not in str(value).lower() for value in exported.values())


def test_agent_avatar_selection_is_persisted_exported_and_cloned(service):
    created = service.create(
        workspace_id="workspace_1",
        requester=OWNER,
        display_name="Avatar Agent",
        handle="avatar-agent",
        instructions="Use the selected identity.",
        avatar_id="07",
    )
    definition = service.agents.get_definition(created["agent_definition_id"])
    assert definition is not None
    assert definition["avatar_id"] == "07"

    edited = service.edit(created["agent_id"], requester=OWNER, avatar_id="19")
    assert edited["identity"]["avatar_id"] == "19"
    exported = service.export(created["agent_id"])
    assert exported["definition"]["avatar_id"] == "19"

    clone = service.clone(created["agent_id"], requester=OWNER, handle="avatar-agent-copy")
    clone_definition = service.agents.get_definition(clone["agent_definition_id"])
    assert clone_definition is not None
    assert clone_definition["avatar_id"] == "19"

    with pytest.raises(ContractError, match="avatar"):
        service.edit(created["agent_id"], requester=OWNER, avatar_id="31")


def test_import_requires_human_review_for_executable_capabilities_and_can_clone(service):
    created = service.create(
        workspace_id="workspace_1",
        requester=OWNER,
        display_name="Base Agent",
        handle="base-agent",
        instructions="Do bounded work.",
    )
    package = service.export(created["agent_id"])
    package["definition"]["handle"] = "imported-agent"
    package["revision"]["capabilities"] = [
        {
            "capability_kind": "plugin",
            "capability_ref": "third-party-plugin",
            "required": True,
            "config": {"api_key": "should-not-be-exported"},
        }
    ]
    pending = service.import_package(
        package,
        workspace_id="workspace_1",
        requester=OWNER,
    )
    assert not pending.installed
    assert pending.requires_approval
    approved = service.import_package(
        package,
        workspace_id="workspace_1",
        requester=OWNER,
        human_approved=True,
    )
    assert approved.installed
    clone = service.clone(created["agent_id"], requester=OWNER, handle="base-agent-copy")
    assert clone["agent_id"] != created["agent_id"]


def test_pause_resume_reset_archive_have_explicit_terminal_states(service):
    created = service.create(
        workspace_id="workspace_1",
        requester=OWNER,
        display_name="Ops Agent",
        handle="ops-agent",
        instructions="Operate carefully.",
    )
    assert service.pause(created["agent_id"])["status"] == AgentStatus.PAUSED.value
    assert service.resume(created["agent_id"])["status"] == AgentStatus.READY.value
    assert service.reset(created["agent_id"])["status"] == "ready"
    assert service.archive(created["agent_id"])["status"] == AgentStatus.ARCHIVED.value
    with pytest.raises(AgentLifecycleError):
        service.resume(created["agent_id"])


def test_stop_restart_and_delete_preserve_auditable_history(service):
    created = service.create(
        workspace_id="workspace_1",
        requester=OWNER,
        display_name="Disposable Agent",
        handle="disposable-agent",
        instructions="Do bounded work.",
    )

    stopped = service.stop(created["agent_id"])
    assert stopped["status"] == AgentStatus.PAUSED.value
    sandbox = service.sandboxes.repository.get_sandbox_for_agent(created["agent_id"])
    assert sandbox is not None and sandbox["status"] == "suspended"

    restarted = service.restart(created["agent_id"])
    assert restarted["status"] == AgentStatus.READY.value
    sandbox = service.sandboxes.repository.get_sandbox_for_agent(created["agent_id"])
    assert sandbox is not None and sandbox["status"] == "ready"

    service.archive(created["agent_id"])
    deleted = service.delete(created["agent_id"], requester=OWNER)
    assert deleted["status"] == "deleted"
    assert service.agents.is_deleted(created["agent_id"])
    retained = service.agents.get_installation(created["agent_id"])
    assert retained is not None and retained["status"] == AgentStatus.ARCHIVED.value
    with pytest.raises(AgentLifecycleError, match="deleted"):
        service.export(created["agent_id"])


def test_edit_only_changes_requested_fields_and_preserves_existing_policy(service):
    created = service.create(
        workspace_id="workspace_1",
        requester=OWNER,
        display_name="Policy Agent",
        handle="policy-agent",
        instructions="Original instructions.",
        model_policy={"model": "gpt-5.6-sol", "temperature": 0},
        sandbox_policy={"actions": ["sandbox.read"]},
        capabilities=({"capability_kind": "tool", "capability_ref": "read_file"},),
    )
    edited = service.edit(
        created["agent_id"], requester=OWNER, instructions="Updated instructions."
    )
    assert edited["revision"]["instructions"] == "Updated instructions."
    assert edited["revision"]["model_policy"] == {"model": "gpt-5.6-sol", "temperature": 0}
    assert edited["revision"]["sandbox_policy"] == {"actions": ["sandbox.read"]}
    assert edited["revision"]["capabilities"][0]["capability_ref"] == "read_file"


def test_create_keeps_failed_provisioning_visible_for_recovery(service, monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("runtime offline")

    monkeypatch.setattr(service.sandboxes, "provision", fail)
    with pytest.raises(AgentLifecycleError, match="provisioning failed"):
        service.create(
            workspace_id="workspace_1",
            requester=OWNER,
            display_name="Offline Agent",
            handle="offline-agent",
            instructions="Retry me.",
        )
    installation = service.agents.get_installation(
        service.agents.database._conn.execute(
            "SELECT agent_id FROM agent_installations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()["agent_id"]
    )
    assert installation["status"] == AgentStatus.ERROR.value
