"""Sandbox provider isolation labels, environment, mounts, and lifecycle tests."""

import os
import shutil
import subprocess
import sys

import pytest

from coworker.teams import (
    AgentRepository,
    ActorKind,
    ActorRef,
    ContainerSandboxAdapter,
    IdentityRepository,
    LocalSandboxProvider,
    SandboxError,
    SandboxManager,
    SandboxPolicyViolation,
    SandboxRepository,
    TeamDatabase,
)


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")


@pytest.fixture
def sandbox_stack(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER)
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    agent_actor = ActorRef("actor_agent", ActorKind.AGENT, "Builder")
    identities.create_actor(agent_actor)
    identities.add_membership("membership_agent", "workspace_1", agent_actor.id, "member")
    agents = AgentRepository(database)
    agents.create_definition("definition", "workspace_1", OWNER.id, "Builder", "builder", "builder")
    agents.create_revision("revision", "definition", 1, "Build safely.", OWNER.id)
    agents.install("agent_builder", "workspace_1", agent_actor.id, "definition", "revision", status="ready")
    manager = SandboxManager(SandboxRepository(database), tmp_path / "sandboxes")
    yield database, manager, tmp_path
    database.close()


def test_local_provider_is_explicitly_weak_and_does_not_inherit_host_environment(sandbox_stack):
    _database, manager, tmp_path = sandbox_stack
    sandbox = manager.provision("sandbox_builder", "workspace_1", "agent_builder")
    assert sandbox["isolation_level"] == "weak_local"
    assert manager.provider.strong_isolation is False

    os.environ["ASTRA_HOST_SECRET_TEST"] = "must-not-leak"
    try:
        result = manager.execute(
            "sandbox_builder",
            "run_env",
            (
                sys.executable,
                "-c",
                "import os; print(os.getenv('ASTRA_HOST_SECRET_TEST', 'missing')); open('own.txt', 'w').write('ok')",
            ),
        )
    finally:
        os.environ.pop("ASTRA_HOST_SECRET_TEST", None)
    assert result.exit_code == 0
    assert "must-not-leak" not in result.stdout
    assert "missing" in result.stdout
    assert (tmp_path / "sandboxes" / "sandbox_builder" / "jobs" / "run_env" / "own.txt").read_text() == "ok"


def test_local_provider_rejects_network_allowlist_it_cannot_enforce(sandbox_stack):
    _database, manager, _tmp_path = sandbox_stack
    manager.provision("sandbox_builder", "workspace_1", "agent_builder")

    with pytest.raises(SandboxPolicyViolation, match="cannot enforce"):
        manager.execute(
            "sandbox_builder",
            "run_network",
            ("true",),
            resource_policy={
                "network_policy": "allowlist",
                "network_domains": ["example.com"],
            },
        )

    assert manager.repository.get_sandbox("sandbox_builder")["status"] == "error"


def test_read_mount_is_copied_and_traversal_is_rejected(sandbox_stack):
    _database, manager, tmp_path = sandbox_stack
    manager.provision("sandbox_builder", "workspace_1", "agent_builder")
    source = tmp_path / "source.txt"
    source.write_text("original")
    result = manager.execute(
        "sandbox_builder",
        "run_mount",
        (
            sys.executable,
            "-c",
            "from pathlib import Path; Path('input.txt').write_text('changed'); print(Path('input.txt').read_text())",
        ),
        mounts=(
            {
                "source_ref": str(source),
                "target_path": "/input.txt",
                "access_mode": "read",
            },
        ),
    )
    assert result.exit_code == 0
    assert source.read_text() == "original"
    with pytest.raises(SandboxPolicyViolation, match="escapes"):
        manager.execute(
            "sandbox_builder",
            "run_escape",
            (sys.executable, "-c", "print('no')"),
            mounts=(
                {
                    "source_ref": str(source),
                    "target_path": "../../outside.txt",
                    "access_mode": "read",
                },
            ),
        )


def test_reset_archives_old_home_and_archive_is_not_secure_relabeling(sandbox_stack):
    _database, manager, tmp_path = sandbox_stack
    manager.provision("sandbox_builder", "workspace_1", "agent_builder")
    home = tmp_path / "sandboxes" / "sandbox_builder" / "home"
    (home / "state.txt").write_text("old")
    manager.reset("sandbox_builder")
    assert not (home / "state.txt").exists()
    assert list((home.parent / "archives").glob("*/home/state.txt"))
    archived = manager.archive("sandbox_builder")
    assert archived["status"] == "archived"


def test_external_adapter_cannot_claim_strong_isolation_without_attestation():
    class WeakAdapter:
        provider_kind = "container"
        isolation_level = "container"
        strong_isolation = False
        network_enforced = False

    from coworker.teams import ExternalSandboxProvider

    with pytest.raises(SandboxError, match="must enforce"):
        ExternalSandboxProvider(WeakAdapter())


def test_container_adapter_builds_strong_deny_by_default_argv_without_host_env(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _runtime: "/usr/bin/fake-container")
    adapter = ContainerSandboxAdapter("docker", "python:3.12-slim", tmp_path / "sandboxes")
    adapter.provision("sandbox_1", str(tmp_path / "sandboxes" / "sandbox_1" / "home"))

    class FakeProcess:
        pid = 123
        returncode = 0

        def communicate(self, timeout=None):
            assert timeout == 7
            return b"ok", b""

    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    result = adapter.execute(
        "sandbox_1",
        "run_1",
        (sys.executable, "-c", "print('ok')"),
        environment={"EXPLICIT_ONLY": "yes"},
        resource_policy={"network_policy": "deny", "max_wall_seconds": 7},
    )
    argv, kwargs = calls[0]
    assert result.network_enforced is True
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--env" in argv and "EXPLICIT_ONLY" in argv
    assert kwargs["env"] == {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "EXPLICIT_ONLY": "yes",
    }

    with pytest.raises(SandboxPolicyViolation, match="network deny"):
        adapter.execute(
            "sandbox_1",
            "run_2",
            ("true",),
            environment={},
            resource_policy={"network_policy": "allow"},
        )
