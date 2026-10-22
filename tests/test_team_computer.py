"""Durable Computer sessions, exclusive screen control, and human takeover."""

from __future__ import annotations

import base64

import pytest

from coworker.teams import (
    ActorKind,
    ActorRef,
    ComputerManager,
    ComputerObservation,
    ComputerRepository,
    ContractError,
    IdentityRepository,
    ScreenLeaseConflict,
    TeamDatabase,
)


OWNER = ActorRef("actor_owner", ActorKind.HUMAN, "Owner")
OTHER = ActorRef("actor_other", ActorKind.HUMAN, "Other")
AGENT = ActorRef("actor_agent", ActorKind.AGENT, "Builder")


class FakeComputerProvider:
    provider_kind = "fake_browser"

    def __init__(self) -> None:
        self.provisioned: list[str] = []
        self.reconnected: list[tuple[str, str | None]] = []

    def provision(self, computer_id, provider_ref, capabilities):
        self.provisioned.append(computer_id)

    def reconnect(self, computer_id, session_ref):
        self.reconnected.append((computer_id, session_ref))
        return "provider-session-2"

    def suspend(self, computer_id):
        return None

    def destroy(self, computer_id):
        return None

    def observe(self, session_ref):
        return ComputerObservation(session_id=session_ref, url="https://example.test", title="Example")

    def screenshot(self, session_ref):
        return b"png-bytes"

    def navigate(self, session_ref, url):
        return ComputerObservation(session_id=session_ref, url=url, title="Navigated")

    def action(self, session_ref, action):
        return ComputerObservation(session_id=session_ref, state={"action": action["kind"]})

    def transfer_file(self, session_ref, direction, path, content=None):
        return {"direction": direction, "path": path}

    def checkpoint(self, session_ref):
        return {"checkpoint_ref": "checkpoint-1"}


@pytest.fixture
def computer_stack(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    for actor in (OWNER, OTHER, AGENT):
        identities.create_actor(actor)
    identities.add_membership("membership_owner", "workspace_1", OWNER.id, "owner")
    identities.add_membership("membership_other", "workspace_1", OTHER.id, "member")
    identities.add_membership("membership_agent", "workspace_1", AGENT.id, "member")
    repository = ComputerRepository(database)
    provider = FakeComputerProvider()
    manager = ComputerManager(repository, provider)
    yield database, repository, manager, provider
    database.close()


def test_computer_requires_an_installed_agent_identity(computer_stack):
    _database, repository, manager, provider = computer_stack
    with pytest.raises(ContractError, match="agent"):
        repository.create_computer(
            "computer_builder", "workspace_1", "agent_builder", provider.provider_kind,
        )


def test_computer_lifecycle_and_exclusive_screen_lease(computer_stack):
    database, repository, manager, provider = computer_stack
    from coworker.teams import AgentRepository

    agents = AgentRepository(database)
    agents.create_definition("definition", "workspace_1", OWNER.id, "Builder", "builder", "builder")
    agents.create_revision("revision", "definition", 1, "Build", OWNER.id)
    agents.install("agent_builder", "workspace_1", AGENT.id, "definition", "revision", status="ready")

    repository.create_computer("computer_builder", "workspace_1", "agent_builder", provider.provider_kind)
    assert manager.provision("computer_builder")["status"] == "ready"
    session = repository.create_session(
        "session_1", "computer_builder", "workspace_1", "agent_builder", OWNER.id,
        provider_session_ref="provider-session-1",
    )
    assert session["status"] == "active"
    lease = repository.acquire_screen_lease(
        "lease_1", "session_1", "workspace_1", OWNER.id, lease_seconds=60,
    )
    with pytest.raises(ScreenLeaseConflict):
        repository.acquire_screen_lease("lease_2", "session_1", "workspace_1", OTHER.id)

    observation = manager.action(
        "session_1", {"kind": "click", "target": "submit"},
        lease_id=lease["screen_lease_id"], actor_id=OWNER.id,
    )
    assert observation.state == {"action": "click"}
    assert base64.b64decode(manager.screenshot("session_1")) == b"png-bytes"
    assert manager.navigate("session_1", "https://example.test/next").url.endswith("/next")
    assert manager.reconnect("session_1")["provider_session_ref"] == "provider-session-2"
    assert provider.reconnected == [("computer_builder", "provider-session-1")]


def test_takeover_parks_session_and_approval_resumes_it(computer_stack):
    database, repository, manager, _provider = computer_stack
    from coworker.teams import AgentRepository

    agents = AgentRepository(database)
    agents.create_definition("definition", "workspace_1", OWNER.id, "Builder", "builder", "builder")
    agents.create_revision("revision", "definition", 1, "Build", OWNER.id)
    agents.install("agent_builder", "workspace_1", AGENT.id, "definition", "revision", status="ready")
    repository.create_computer("computer_builder", "workspace_1", "agent_builder", "fake_browser")
    manager.provision("computer_builder")
    repository.create_session("session_1", "computer_builder", "workspace_1", "agent_builder", OWNER.id)

    takeover = manager.request_takeover(
        "session_1", "workspace_1", OWNER.id, reason="MFA requires a human",
    )
    assert takeover["status"] == "pending"
    assert repository.get_session("session_1")["status"] == "waiting_human"
    assert repository.decide_takeover(takeover["takeover_request_id"], "approved", actor_id=OWNER.id)["status"] == "approved"
    assert repository.get_session("session_1")["status"] == "active"


def test_computer_rejects_malformed_browser_url(computer_stack):
    database, repository, manager, provider = computer_stack
    from coworker.teams import AgentRepository

    agents = AgentRepository(database)
    agents.create_definition("definition", "workspace_1", OWNER.id, "Builder", "builder", "builder")
    agents.create_revision("revision", "definition", 1, "Build", OWNER.id)
    agents.install("agent_builder", "workspace_1", AGENT.id, "definition", "revision", status="ready")
    repository.create_computer("computer_builder", "workspace_1", "agent_builder", provider.provider_kind)
    manager.provision("computer_builder")
    repository.create_session("session_1", "computer_builder", "workspace_1", "agent_builder", OWNER.id)
    with pytest.raises(ContractError):
        manager.navigate("session_1", "https://example.test/\nsecret")
