"""Legacy Team cutover: read-only source, idempotent import, and recovery evidence."""

import hashlib
import json

from coworker.automation.models import Schedule, ScheduledTask
from coworker.automation.store import TaskStore
from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord
from coworker.teams import (
    Actor,
    ActorKind,
    ActorRef,
    IdentityRepository,
    LegacyCutoverError,
    LegacyCutoverService,
    TeamDatabase,
    TeamStore,
    Role,
)
from coworker.teams.chat import ChatStore
from coworker.teams.registry import TeamRegistry, TeamWorker


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_owner(database):
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_local", "Local workspace")
    identities.create_actor(ActorRef("actor_local_owner", ActorKind.HUMAN, "Owner"), handle="owner")
    identities.add_membership("membership_local_owner", "workspace_local", "actor_local_owner", "owner")


def _make_legacy_state(base):
    conversations = ConversationStore(base)
    conversations.save(SessionRecord(
        session_id="legacy_session",
        workspace=str(base),
        model="test-model",
        mode="interactive",
        agent="code",
        messages=[
            {"role": "user", "content": "Keep this history"},
            {"role": "assistant", "content": "History retained"},
        ],
    ))
    conversations.close()

    board = TeamStore(base / "teams.db")
    item = board.create_item("project", Actor(id="lead", role=Role.LEAD), title="Legacy board item", criteria="Imported criteria")
    board.append_event("project", "note", Actor(id="lead", role=Role.LEAD), item_id=item["id"], payload={"note": "preserve", "api_token": "must-not-be-copied"})
    board.close()

    chat = ChatStore(base / "chat.db")
    group = chat.create_group("Legacy chat", [{"name": "builder", "persona": "Builder", "role": "worker"}])
    chat.post(group["group_id"], "user", "Please keep this message", author_role="user")
    chat.close()

    automation = TaskStore(base / "automation.db")
    automation.save(ScheduledTask(
        id="legacy_task",
        title="Legacy schedule",
        instructions="Run the retained legacy schedule after review.",
        schedule=Schedule(kind="cron", cron="*/5 * * * *", timezone="UTC"),
        workspace=str(base),
        agent="builder",
    ))
    automation.close()

    registry = TeamRegistry(base / "teams.json")
    registry.create(
        space="project",
        lead_session="legacy_session",
        lead_actor="lead",
        workers=[TeamWorker(actor="builder", persona="Builder", session_id="worker_session", model="test-model")],
    )


def test_cutover_preflight_backup_import_verify_and_rerun(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _make_legacy_state(legacy)
    source_files = [legacy / name for name in ("coworker.db", "teams.db", "chat.db", "automation.db", "teams.json")]
    before = {path.name: _sha256(path) for path in source_files}

    database = TeamDatabase(tmp_path / "team.db")
    _canonical_owner(database)
    service = LegacyCutoverService(database, "workspace_local", "actor_local_owner", backup_root=tmp_path / "backups")

    preflight = service.preflight(legacy)
    assert preflight.is_usable
    assert preflight.legacy_counts["sessions"] == 1
    assert preflight.legacy_counts["team_items"] == 1
    assert preflight.legacy_counts["chat_messages"] == 1
    assert preflight.legacy_counts["automation_tasks"] == 1

    result = service.import_legacy(legacy)
    assert result.imported
    assert result.is_usable
    assert result.backup_path
    assert (tmp_path / "backups").is_dir()
    assert result.canonical_counts["conversations"] == 2
    assert result.canonical_counts["messages"] == 3
    assert result.canonical_counts["tasks"] == 1
    assert result.canonical_counts["team_routines"] == 1
    assert result.canonical_counts["crew_runs"] == 1
    assert result.mapping_count >= 6
    task_event_payloads = database._conn.execute("SELECT payload_json FROM task_events").fetchall()
    assert all("must-not-be-copied" not in row[0] for row in task_event_payloads)

    activated = service.activate(legacy)
    assert activated.is_usable
    assert service.is_active()

    rerun = service.import_legacy(legacy, create_backup=False)
    assert rerun.imported
    # Activation adds one durable marker after the first import; subsequent
    # retries must remain stable at that post-activation count.
    assert rerun.mapping_count == activated.mapping_count
    assert rerun.canonical_counts == activated.canonical_counts
    verified = service.verify(legacy)
    assert verified.is_usable

    assert {path.name: _sha256(path) for path in source_files} == before
    database.close()


def test_cutover_refuses_corrupt_legacy_before_backup_or_writes(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "teams.db").write_bytes(b"not sqlite")
    database = TeamDatabase(tmp_path / "team.db")
    _canonical_owner(database)
    service = LegacyCutoverService(database, "workspace_local", "actor_local_owner", backup_root=tmp_path / "backups")

    report = service.preflight(legacy)
    assert not report.is_usable
    try:
        service.import_legacy(legacy)
    except LegacyCutoverError as error:
        assert "preflight failed" in str(error)
    else:
        raise AssertionError("corrupt legacy state was imported")
    assert not (tmp_path / "backups").exists()
    assert database.table_counts()["conversations"] == 0
    database.close()


def test_cutover_http_surface_is_confirmation_gated(tmp_path):
    import asyncio
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    manager = SessionManager(data_dir=tmp_path / "state")
    client = TestClient(create_app(manager))
    try:
        preflight = client.get("/v1/team/cutover")
        assert preflight.status_code == 200
        assert preflight.json()["is_usable"] is True
        denied = client.post("/v1/team/cutover", json={"action": "import"})
        assert denied.status_code == 400
        assert "confirm=true" in denied.json()["error"]
        imported = client.post("/v1/team/cutover", json={"action": "import", "confirm": True, "create_backup": False, "activate": True})
        assert imported.status_code == 200
        assert imported.json()["imported"] is True
        assert manager.team_cutover.is_active()
    finally:
        client.close()
        asyncio.run(manager.aclose())


def test_board_http_switches_to_canonical_adapter_after_activation(tmp_path):
    import asyncio
    from fastapi.testclient import TestClient

    from coworker.server import SessionManager, create_app

    manager = SessionManager(data_dir=tmp_path / "state")
    manager.team_store.create_item(
        "project",
        Actor(id="lead", role=Role.LEAD),
        title="Legacy item before cutover",
        criteria="It is imported and served canonically",
    )
    manager.team_cutover.import_legacy(manager._data_base, create_backup=False, activate=True)
    token = manager.board_tokens.mint("lead", "lead")
    client = TestClient(create_app(manager))
    headers = {"Authorization": f"Bearer {token}"}
    try:
        listed = client.get("/v1/board/items", params={"space": "project"}, headers=headers)
        assert listed.status_code == 200
        assert listed.json()["items"][0]["title"] == "Legacy item before cutover"
        assert client.get("/v1/board/policy", params={"space": "project"}).status_code == 401
        fenced = client.get("/v1/board/policy", params={"space": "project"}, headers=headers)
        assert fenced.status_code == 409

        created = client.post(
            "/v1/board/items",
            headers=headers,
            json={
                "space": "project",
                "title": "Canonical item",
                "criteria": "The cutover adapter owns this task",
                "request_id": "route-create-1",
            },
        )
        assert created.status_code == 200
        assert created.json()["id"] == 2
        retried = client.post(
            "/v1/board/items",
            headers=headers,
            json={
                "space": "project",
                "title": "Canonical item",
                "criteria": "The cutover adapter owns this task",
                "request_id": "route-create-1",
            },
        )
        assert retried.status_code == 200
        assert retried.json()["id"] == 2
        assert manager.team_store.list_items("project", Actor(id="lead", role=Role.LEAD))[0]["title"] == "Legacy item before cutover"
    finally:
        client.close()
        asyncio.run(manager.aclose())
