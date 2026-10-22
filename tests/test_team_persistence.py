"""Durable schema, migration recovery, FK, cursor, and legacy-isolation tests."""

import sqlite3

import pytest

from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord
from coworker.teams import (
    AgentRepository,
    ActorKind,
    ActorRef,
    ArtifactPublication,
    ArtifactRepository,
    ArtifactVisibility,
    ContextRepository,
    ContextBoundary,
    ContextScope,
    ContextShare,
    ConversationRepository,
    ContractError,
    DomainEvent,
    GovernanceRepository,
    HistoryPolicy,
    SandboxRepository,
    TaskRepository,
    TransitionAuthority,
    WorkBudget,
    WorkRepository,
    WorkRequest,
)
from coworker.teams.persistence import (
    DEFAULT_MIGRATIONS,
    EventRepository,
    IdentityRepository,
    Migration,
    MigrationError,
    PersistenceConflictError,
    SearchRepository,
    TeamDatabase,
)
from coworker.teams.contracts import ResourceRef, WorkSource
from coworker.teams.store import TeamStore


NOW = "2026-08-29T12:00:00+00:00"
OWNER = ActorRef(id="actor_owner", kind=ActorKind.HUMAN, display_name="Owner")
SYSTEM = ActorRef(id="actor_system", kind=ActorKind.SYSTEM, display_name="System")


@pytest.fixture
def database(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    yield database
    database.close()


def seed_identity(database):
    identities = IdentityRepository(database)
    identities.create_workspace("workspace_1", "Project One")
    identities.create_actor(OWNER, handle="owner")
    identities.create_actor(SYSTEM)
    identities.add_membership("membership_1", "workspace_1", OWNER.id, "owner")
    return identities


def seed_agent(database):
    seed_identity(database)
    identities = IdentityRepository(database)
    agent_actor = ActorRef(id="actor_agent", kind=ActorKind.AGENT, display_name="Builder")
    identities.create_actor(agent_actor, handle="builder")
    identities.add_membership("membership_agent", "workspace_1", agent_actor.id, "member")
    agents = AgentRepository(database)
    agents.create_definition(
        "agent_definition_builder",
        "workspace_1",
        OWNER.id,
        "Builder",
        "builder",
        "builder",
    )
    agents.create_revision(
        "agent_revision_builder_1",
        "agent_definition_builder",
        1,
        "Build the requested change and report evidence.",
        OWNER.id,
        capabilities=[{"capability_kind": "tool", "capability_ref": "shell", "required": True}],
    )
    agents.install(
        "agent_builder",
        "workspace_1",
        agent_actor.id,
        "agent_definition_builder",
        "agent_revision_builder_1",
        status="ready",
    )
    return agent_actor


def test_schema_contains_all_bounded_context_tables_and_fts(database):
    names = {
        row[0]
        for row in database._conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        )
    }
    assert {
        "workspaces",
        "actors",
        "agent_definitions",
        "agent_revisions",
        "agent_installations",
        "conversations",
        "conversation_members",
        "messages",
        "message_revisions",
        "message_mentions",
        "message_reactions",
        "conversation_pins",
        "conversation_preferences",
        "conversation_read_cursors",
        "tasks",
        "task_dependencies",
        "work_requests",
        "work_usage",
        "deliveries",
        "delivery_attempts",
        "delivery_leases",
        "runs",
        "run_events",
        "sandboxes",
        "sandbox_mounts",
        "sandbox_snapshots",
        "artifacts",
        "artifact_revisions",
        "approvals",
        "grants",
        "connector_bindings",
        "memory_records",
        "context_pins",
        "context_shares",
        "notifications",
        "notification_preferences",
        "domain_events",
        "legacy_id_mappings",
        "team_message_fts",
        "team_task_fts",
        "team_artifact_fts",
    } <= names
    assert database.schema_version == DEFAULT_MIGRATIONS[-1].migration_id
    assert database.verify_integrity()["foreign_key_errors"] == []


def test_migrations_are_idempotent_and_checksums_are_stable(tmp_path):
    path = tmp_path / "team.db"
    first = TeamDatabase(path)
    applied = first.applied_migrations()
    assert [row["migration_id"] for row in applied] == [
        migration.migration_id for migration in DEFAULT_MIGRATIONS
    ]
    assert first.migrate() == []
    first.close()

    reopened = TeamDatabase(path)
    assert reopened.applied_migrations() == applied
    reopened.close()


def test_migration_0025_purges_legacy_reasoning_events(tmp_path):
    path = tmp_path / "team.db"
    # 1. Initialize pre-upgrade DB with migrations up to 0024 (excluding 0025)
    pre_db = TeamDatabase(path, migrations=DEFAULT_MIGRATIONS[:-1])
    agent_actor = seed_agent(pre_db)
    work = WorkRepository(pre_db)
    request = WorkRequest(
        request_id="request_legacy",
        workspace_id="workspace_1",
        source=WorkSource.AUTOMATION,
        source_ref=ResourceRef("automation", "automation_build"),
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Build task legacy",
        idempotency_key="request_dedupe_legacy",
        correlation_id="trace_work_legacy",
    )
    work.create_request(request)
    delivery = work.create_deliveries(request.request_id)[0]
    sandboxes = SandboxRepository(pre_db)
    sandboxes.create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
    )
    work.create_run(
        "run_legacy",
        delivery["delivery_id"],
        request.request_id,
        "workspace_1",
        "agent_builder",
        "agent_revision_builder_1",
        "sandbox_builder",
    )
    # Record safe event via repository, and legacy reasoning event via raw SQL to represent legacy data
    work.record_run_event(
        "ev_safe_content",
        "run_legacy",
        "agent.content.delta",
        actor_id=agent_actor.id,
        payload={"text": "safe"},
    )
    with pre_db._lock:
        pre_db._conn.execute(
            """
            INSERT INTO run_events (run_event_id, run_id, actor_id, event_type, payload_json, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("ev_reasoning_legacy", "run_legacy", agent_actor.id, "agent.reasoning.delta", '{"text": "legacy CoT"}', "2026-09-01T00:00:00Z"),
        )
        pre_db._conn.commit()
    pre_db.close()

    # 2. Upgrade DB to latest (includes 0025)
    upgraded_db = TeamDatabase(path, migrations=DEFAULT_MIGRATIONS)
    applied = upgraded_db.applied_migrations()
    assert applied[-1]["migration_id"] == "0025_purge_legacy_reasoning_events"

    # Assert raw DB purge: legacy reasoning row deleted from SQLite disk
    with upgraded_db._lock:
        remaining = upgraded_db._conn.execute(
            "SELECT run_event_id, event_type FROM run_events WHERE run_id = 'run_legacy'"
        ).fetchall()
        ids = [row["run_event_id"] for row in remaining]
        assert "ev_reasoning_legacy" not in ids
        assert "ev_safe_content" in ids

    # 4. Assert fail-closed: record_run_event explicitly forbids recording agent.reasoning.delta
    work_latest = WorkRepository(upgraded_db)
    with pytest.raises(ContractError, match="raw chain-of-thought"):
        work_latest.record_run_event(
            "ev_reasoning_new",
            "run_legacy",
            "agent.reasoning.delta",
            actor_id=agent_actor.id,
            payload={"text": "new CoT"},
        )

    # 3. Assert idempotency on second migrate()
    assert upgraded_db.migrate() == []
    upgraded_db.close()


def test_migration_normalizes_legacy_one_agent_group(tmp_path):
    path = tmp_path / "team.db"
    initial = TeamDatabase(path)
    agent_actor = seed_agent(initial)
    conversations = ConversationRepository(initial)
    conversations.create_conversation(
        "conversation_legacy_group",
        "workspace_1",
        "group",
        OWNER.id,
        title="Old group",
        reply_mode="coordinator",
        coordinator_agent_id="agent_builder",
    )
    conversations.add_member("conversation_legacy_group", OWNER.id)
    conversations.add_member("conversation_legacy_group", agent_actor.id)
    initial._conn.execute(
        "DELETE FROM team_schema_migrations WHERE migration_id = ?",
        ("0019_normalize_one_agent_groups",),
    )
    initial.close()

    migrated = TeamDatabase(path)
    conversation = ConversationRepository(migrated).get_conversation("conversation_legacy_group")

    assert conversation is not None
    assert conversation["conversation_kind"] == "direct"
    assert conversation["title"] == ""
    assert conversation["reply_mode"] == "coordinator"
    assert conversation["coordinator_agent_id"] == "agent_builder"
    assert conversation["agent_member_ids"] == ["agent_builder"]
    migrated.close()

def test_changed_applied_migration_is_refused(tmp_path):
    path = tmp_path / "team.db"
    initial = TeamDatabase(path)
    initial.close()
    changed = Migration(DEFAULT_MIGRATIONS[-1].migration_id, ("CREATE TABLE unexpected (id TEXT)",))
    with pytest.raises(MigrationError, match="checksum changed"):
        TeamDatabase(path, migrations=(*DEFAULT_MIGRATIONS[:-1], changed))


def test_failed_migration_rolls_back_and_preserves_a_recoverable_backup(tmp_path):
    path = tmp_path / "team.db"
    database = TeamDatabase(path)
    seed_identity(database)
    database.close()

    failing = Migration(
        "0026_failure_probe",
        (
            "CREATE TABLE failure_probe (id TEXT PRIMARY KEY)",
            "THIS IS NOT VALID SQL",
        ),
    )
    with pytest.raises(MigrationError, match="previous state is recoverable"):
        TeamDatabase(path, migrations=(*DEFAULT_MIGRATIONS, failing))

    backup_dir = tmp_path / "team-backups"
    assert any("before-0026_failure_probe" in path.name for path in backup_dir.glob("*.sqlite"))
    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'failure_probe'"
    ).fetchone() is None
    assert connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 1
    connection.close()

    recovered = TeamDatabase(path)
    assert recovered.verify_integrity()["foreign_key_errors"] == []
    recovered.close()


@pytest.mark.parametrize("phase", ["before", "statement:1"])
def test_migration_crash_injection_before_or_during_transaction_is_atomic(tmp_path, phase):
    path = tmp_path / "team.db"
    initial = TeamDatabase(path)
    initial.close()
    probe = Migration(
        "0026_crash_probe",
        ("CREATE TABLE crash_probe (id TEXT PRIMARY KEY)", "INSERT INTO crash_probe (id) VALUES ('one')"),
    )

    def crash(migration_id, current_phase):
        if migration_id == probe.migration_id and current_phase == phase:
            raise RuntimeError("injected migration crash")

    with pytest.raises(MigrationError, match="previous state is recoverable"):
        TeamDatabase(path, migrations=(*DEFAULT_MIGRATIONS, probe), migration_hook=crash)
    recovered = TeamDatabase(path)
    assert recovered._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'crash_probe'"
    ).fetchone() is None
    recovered.close()


def test_migration_crash_injection_after_commit_is_replay_safe(tmp_path):
    path = tmp_path / "team.db"
    probe = Migration("0027_after_commit_probe", ("CREATE TABLE after_commit_probe (id TEXT PRIMARY KEY)",))

    def crash(migration_id, current_phase):
        if migration_id == probe.migration_id and current_phase == "after_commit":
            raise RuntimeError("injected post-commit crash")

    with pytest.raises(MigrationError, match="previous state is recoverable"):
        TeamDatabase(path, migrations=(*DEFAULT_MIGRATIONS, probe), migration_hook=crash)
    reopened = TeamDatabase(path, migrations=(*DEFAULT_MIGRATIONS, probe))
    assert reopened._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'after_commit_probe'"
    ).fetchone() is not None
    reopened.close()


def test_parity_report_handles_large_jsonl_and_corrupt_sqlite(tmp_path):
    base = tmp_path / "legacy"
    conversations = base / "conversations"
    conversations.mkdir(parents=True)
    with (conversations / "large.jsonl").open("w", encoding="utf-8") as stream:
        for index in range(1200):
            stream.write('{"role":"user","content":"line %s"}\n' % index)
    (base / "teams.db").write_bytes(b"not a sqlite database")
    database = TeamDatabase(base / "team.db")
    report = database.parity_report(base)
    assert report.legacy_counts["conversation_jsonl_messages"] == 1200
    assert any("teams.db" in source for source in report.corrupt_sources)
    assert not report.is_usable
    database.close()


def test_identity_repository_and_foreign_keys_reject_orphan_rows(database):
    identities = IdentityRepository(database)
    with pytest.raises(PersistenceConflictError):
        identities.add_membership("membership_missing", "workspace_missing", OWNER.id, "member")

    seed_identity(database)
    with pytest.raises(PersistenceConflictError):
        identities.add_membership("membership_2", "workspace_1", OWNER.id, "member")
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO deliveries
                    (delivery_id, request_id, workspace_id, target_agent_id, status,
                     idempotency_key, created_at, updated_at)
                VALUES ('delivery_orphan', 'request_missing', 'workspace_1', 'agent_missing',
                        'pending', 'dedupe', ?, ?)
                """,
                (NOW, NOW),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO tasks
                    (task_id, workspace_id, title, description, acceptance_criteria,
                     status, created_by_actor_id, created_at, updated_at)
                VALUES ('task_orphan', 'workspace_missing', 'Orphan', '', 'Never', 'open', ?, ?, ?)
                """,
                (OWNER.id, NOW, NOW),
            )
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runs
                    (run_id, delivery_id, request_id, workspace_id, agent_id,
                     agent_revision_id, sandbox_id, status, created_at)
                VALUES ('run_orphan', 'delivery_missing', 'request_missing', 'workspace_1',
                        'agent_missing', 'revision_missing', 'sandbox_missing', 'queued', ?)
                """,
                (NOW,),
            )


def test_events_are_append_only_idempotent_and_cursor_paginated(database):
    seed_identity(database)
    events = EventRepository(database)
    first = DomainEvent(
        event_id="event_1",
        event_type="workspace.created",
        workspace_id="workspace_1",
        actor_id=SYSTEM.id,
        aggregate_type="workspace",
        aggregate_id="workspace_1",
        correlation_id="trace_1",
        payload={"name": "Project One"},
        occurred_at=NOW,
    )
    persisted = events.append(first)
    assert persisted["sequence"] == 1
    assert events.append(first) == persisted
    with pytest.raises(PersistenceConflictError, match="idempotency conflict"):
        events.append(
            DomainEvent.from_dict({**first.to_dict(), "payload": {"name": "Different"}})
        )

    for index in range(2, 5):
        events.append(
            DomainEvent(
                event_id=f"event_{index}",
                event_type="task.created",
                workspace_id="workspace_1",
                actor_id=OWNER.id,
                aggregate_type="task",
                aggregate_id=f"task_{index}",
                correlation_id="trace_1",
                occurred_at=NOW,
            )
        )
    page_one = events.list_workspace_events("workspace_1", limit=2)
    assert [event["sequence"] for event in page_one["events"]] == [1, 2]
    assert page_one["next_sequence"] == 2
    assert page_one["has_more"]
    page_two = events.list_workspace_events(
        "workspace_1", after_sequence=page_one["next_sequence"], limit=2
    )
    assert [event["sequence"] for event in page_two["events"]] == [3, 4]
    assert not page_two["has_more"]


def test_search_projections_are_workspace_scoped_and_cursor_paginated(database):
    seed_identity(database)
    search = SearchRepository(database)
    search.index_message("message_1", "workspace_1", "conversation_1", "Astra Team sandbox")
    search.index_message("message_2", "workspace_1", "conversation_1", "Astra Team routing")
    search.index_message("message_other", "workspace_2", "conversation_2", "Astra Team hidden")
    search.index_task("task_1", "workspace_1", "Sandbox", "Isolated runtime", "No shared tree")
    search.index_artifact(
        "artifact_1",
        "workspace_1",
        "sandbox-report.md",
        "text/markdown",
        {"label": "sandbox"},
    )

    first = search.search_messages("workspace_1", "Astra", limit=1)
    assert len(first["results"]) == 1
    assert first["has_more"]
    second = search.search_messages(
        "workspace_1", "Astra", after_cursor=first["next_cursor"], limit=1
    )
    assert len(second["results"]) == 1
    assert not second["has_more"]
    assert {item["message_id"] for item in [*first["results"], *second["results"]]} == {
        "message_1",
        "message_2",
    }
    assert search.search_messages("workspace_1", "hidden")["results"] == []
    assert search.search_tasks("workspace_1", "shared tree")["results"][0]["task_id"] == "task_1"
    assert search.search_artifacts("workspace_1", "sandbox")["results"][0]["artifact_id"] == "artifact_1"


def test_search_rejects_fts_operators_and_bad_cursors_without_sql_errors(database):
    seed_identity(database)
    search = SearchRepository(database)
    search.index_message("message_1", "workspace_1", "conversation_1", "Astra routing")
    assert search.search_messages("workspace_1", "Astra OR *")["results"] == []
    with pytest.raises(ContractError, match="numeric cursor"):
        search.search_messages("workspace_1", "Astra", after_cursor="not-a-cursor")


def test_new_team_database_does_not_mutate_legacy_jsonl_or_hash_chained_store(tmp_path):
    base = tmp_path / "state"
    conversation_store = ConversationStore(base)
    conversation_store.save(
        SessionRecord(
            session_id="legacy_session",
            workspace=str(tmp_path),
            model="test",
            mode="interactive",
            messages=[{"role": "user", "content": "Keep this readable"}],
        )
    )
    board = TeamStore(base / "teams.db")
    from coworker.teams import Actor, Role

    board.create_item("legacy-space", Actor(id="legacy_lead", role=Role.LEAD), title="Keep", criteria="hash")
    before_count = board.verify_chain("legacy-space")

    database = TeamDatabase(base / "team.db")
    database.close()
    assert conversation_store.load("legacy_session").messages[0]["content"] == "Keep this readable"
    assert board.verify_chain("legacy-space") == before_count
    board.close()
    conversation_store.close()


def test_agent_conversation_and_message_repositories_are_idempotent(database):
    agent_actor = seed_agent(database)
    conversations = ConversationRepository(database)
    conversations.create_conversation(
        "conversation_1",
        "workspace_1",
        "group",
        OWNER.id,
        title="Build room",
        reply_mode="coordinator",
    )
    conversations.add_member("conversation_1", OWNER.id, history_policy=HistoryPolicy.ALL_HISTORY)
    conversations.add_member("conversation_1", agent_actor.id)
    message = conversations.append_message(
        "message_1",
        "conversation_1",
        OWNER,
        "Please build the sandbox",
        "message_dedupe_1",
        "trace_message_1",
        mentions=(agent_actor.id,),
    )
    assert message["mentions"] == [agent_actor.id]
    assert conversations.append_message(
        "message_1",
        "conversation_1",
        OWNER,
        "Please build the sandbox",
        "message_dedupe_1",
        "trace_message_1",
        mentions=(agent_actor.id,),
    ) == message
    with pytest.raises(PersistenceConflictError):
        conversations.append_message(
            "message_other",
            "conversation_1",
            OWNER,
            "Different body",
            "message_dedupe_1",
            "trace_message_1",
            mentions=(agent_actor.id,),
        )
    page = conversations.list_messages("conversation_1", limit=1)
    assert [item["message_id"] for item in page["messages"]] == ["message_1"]
    assert not page["has_more"]
    assert conversations.get_message("message_1")["body"] == "Please build the sandbox"


def test_conversation_actions_threads_and_member_acl_are_durable(database):
    agent_actor = seed_agent(database)
    conversations = ConversationRepository(database)
    conversations.create_conversation(
        "conversation_actions",
        "workspace_1",
        "group",
        OWNER.id,
        title="Ops room",
        reply_mode="coordinator",
    )
    conversations.add_member(
        "conversation_actions", OWNER.id, member_role="owner", history_policy=HistoryPolicy.ALL_HISTORY
    )
    conversations.add_member("conversation_actions", agent_actor.id)

    pinned_conversation = conversations.set_pinned(
        "conversation_actions", OWNER.id, pinned=True
    )
    assert pinned_conversation["pinned"] is True
    assert conversations.list_conversations("workspace_1", OWNER.id)[0]["pinned"] is True
    assert conversations.list_conversations("workspace_1", agent_actor.id)[0]["pinned"] is False
    assert conversations.set_pinned("conversation_actions", OWNER.id, pinned=True)["changed"] is False

    root = conversations.append_message(
        "message_root",
        "conversation_actions",
        OWNER,
        "Review this release",
        "client-send-1",
        "correlation-1",
        mentions=(agent_actor.id,),
    )
    assert root["reactions"] == []
    assert conversations.unread_state("conversation_actions", OWNER.id)["unread_count"] == 0
    agent_reply = conversations.append_message(
        "message_agent_reply",
        "conversation_actions",
        agent_actor,
        "Agent reply",
        "client-send-agent-reply",
        "correlation-agent-reply",
    )
    assert agent_reply["author_actor_id"] == agent_actor.id
    assert conversations.unread_state("conversation_actions", OWNER.id)["unread_count"] == 1
    conversations.set_read_cursor("conversation_actions", OWNER.id, agent_reply["message_id"])
    assert conversations.unread_state("conversation_actions", OWNER.id)["unread_count"] == 0
    edited = conversations.edit_message("message_root", OWNER.id, "Review this release now", expected_revision=1)
    assert edited["body"] == "Review this release now"
    assert [item["revision_number"] for item in conversations.list_message_revisions("message_root")] == [1, 2]

    conversations.create_thread("thread_root", "conversation_actions", "message_root")
    reply = conversations.append_message(
        "message_reply",
        "conversation_actions",
        OWNER,
        "Thread follow-up",
        "client-send-2",
        "correlation-2",
        thread_id="thread_root",
        parent_message_id="message_root",
        quote_message_id="message_root",
    )
    assert reply["quote_message_id"] == "message_root"
    conversations.follow_thread("thread_root", agent_actor.id)
    assert conversations.list_thread_followers("thread_root", OWNER.id)[0]["actor_id"] == agent_actor.id
    assert conversations.unread_state("conversation_actions", agent_actor.id)["thread_unread"]["thread_root"] == 2
    conversations.set_thread_read_cursor("thread_root", agent_actor.id, "message_reply")
    assert conversations.unread_state("conversation_actions", agent_actor.id)["thread_unread"] == {}

    conversations.set_reaction("message_root", agent_actor.id, "👍")
    assert conversations.list_reactions("message_root") == [
        {"actor_id": agent_actor.id, "emoji": "👍", "created_at": root["created_at"]}
    ] or len(conversations.list_reactions("message_root")) == 1
    conversations.pin_message("conversation_actions", "message_root", OWNER.id)
    assert conversations.list_pinned_messages("conversation_actions", agent_actor.id)[0]["message_id"] == "message_root"
    conversations.pin_message("conversation_actions", "message_root", OWNER.id)
    assert len(conversations.list_pinned_messages("conversation_actions", OWNER.id)) == 1
    assert conversations.unpin_message("conversation_actions", "message_root", OWNER.id) is True
    assert conversations.unpin_message("conversation_actions", "message_root", OWNER.id) is False
    with pytest.raises(ContractError, match="admin"):
        conversations.pin_message("conversation_actions", "message_root", agent_actor.id)
    conversations.set_translation("message_root", agent_actor.id, "vi", "Xem bản phát hành ngay", model="gpt-5.6-sol")
    attachment = conversations.add_attachment(
        "message_root",
        OWNER.id,
        {
            "attachment_id": "attachment-1",
            "storage_ref": "artifact://release-notes",
            "filename": "release.md",
            "media_type": "text/markdown",
            "size_bytes": 12,
            "sha256": "a" * 64,
        },
    )
    assert attachment["filename"] == "release.md"
    assert conversations.add_attachment(
        "message_root",
        OWNER.id,
        {
            "attachment_id": "attachment-1",
            "storage_ref": "artifact://release-notes",
            "filename": "release.md",
            "media_type": "text/markdown",
            "size_bytes": 12,
            "sha256": "a" * 64,
        },
    ) == attachment
    conversations.save_draft("draft-1", "conversation_actions", OWNER.id, "composer-1", "unfinished")
    assert conversations.get_draft("conversation_actions", OWNER.id, "composer-1")["body"] == "unfinished"
    assert conversations.stable_message_link("conversation_actions", "message_root").endswith(
        "/message/message_root"
    )

    conversations.mute_conversation("conversation_actions", agent_actor.id, muted_until="2026-08-30T00:00:00+00:00")
    assert conversations.get_conversation("conversation_actions")["status"] == "active"
    conversations.update_conversation("conversation_actions", OWNER.id, title="Renamed ops room")
    assert conversations.list_conversations("workspace_1", OWNER.id)[0]["title"] == "Renamed ops room"
    conversations.remove_member("conversation_actions", agent_actor.id, OWNER.id)
    with pytest.raises(ContractError, match="member"):
        conversations.list_messages("conversation_actions", requester_actor_id=agent_actor.id)
    conversations.transition_conversation("conversation_actions", OWNER.id, "archived")
    conversations.transition_conversation("conversation_actions", OWNER.id, "active")
    deleted = conversations.delete_message("message_root", OWNER.id)
    assert deleted["status"] == "tombstoned" and deleted["body"] == ""


def test_task_dependencies_and_work_delivery_leases_are_durable(database):
    agent_actor = seed_agent(database)
    tasks = TaskRepository(database)
    tasks.create_task(
        "task_1",
        "workspace_1",
        "Foundation",
        "Tests pass",
        OWNER.id,
        owner_agent_id="agent_builder",
        reviewer_actor_id=OWNER.id,
    )
    tasks.create_task(
        "task_2",
        "workspace_1",
        "Feature",
        "Review complete",
        OWNER.id,
        owner_agent_id="agent_builder",
        reviewer_actor_id=OWNER.id,
    )
    tasks.add_dependency("task_2", "task_1")
    with pytest.raises(ContractError, match="dependencies are incomplete"):
        tasks.transition_task(
            "task_2",
            "in_progress",
            actor_id=agent_actor.id,
            authority=TransitionAuthority.TASK_OWNER,
        )
    tasks.transition_task(
        "task_1",
        "in_progress",
        actor_id=agent_actor.id,
        authority=TransitionAuthority.TASK_OWNER,
    )
    tasks.transition_task(
        "task_1",
        "review",
        actor_id=agent_actor.id,
        authority=TransitionAuthority.TASK_OWNER,
    )
    tasks.transition_task(
        "task_1",
        "done",
        actor_id=OWNER.id,
        authority=TransitionAuthority.TASK_REVIEWER,
    )
    assert tasks.get_task("task_2")["dependencies"][0]["status"] == "done"
    with pytest.raises(ContractError, match="cycle"):
        tasks.add_dependency("task_1", "task_2")

    work = WorkRepository(database)
    request = WorkRequest(
        request_id="request_1",
        workspace_id="workspace_1",
        source=WorkSource.AUTOMATION,
        source_ref=ResourceRef("automation", "automation_build"),
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Build task 2",
        idempotency_key="request_dedupe_1",
        correlation_id="trace_work_1",
        task_id="task_2",
        budget=WorkBudget(max_tool_calls=5),
    )
    persisted = work.create_request(request)
    assert persisted["target_agent_ids"] == ["agent_builder"]
    assert work.create_request(request) == persisted
    deliveries = work.create_deliveries(request.request_id)
    assert len(deliveries) == 1
    leased = work.lease_delivery(deliveries[0]["delivery_id"], "coordinator_1", now=NOW)
    assert leased["status"] == "leased"
    with pytest.raises(PersistenceConflictError):
        work.lease_delivery(deliveries[0]["delivery_id"], "coordinator_2", now=NOW)
    work.heartbeat_lease(
        deliveries[0]["delivery_id"], leased["lease_token"], now="2026-08-29T12:00:10+00:00"
    )
    work.mark_dispatched(deliveries[0]["delivery_id"], leased["lease_token"], now=NOW)
    assert work.acknowledge(deliveries[0]["delivery_id"], leased["lease_token"], now=NOW)["status"] == "acknowledged"
    assert work.get_request(request.request_id)["budget"] == WorkBudget(max_tool_calls=5).to_dict()


def test_sandbox_and_context_repositories_preserve_boundaries(database):
    seed_agent(database)
    sandboxes = SandboxRepository(database)
    sandbox = sandboxes.create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
    )
    sandboxes.add_mount(
        "mount_1",
        sandbox["sandbox_id"],
        "workspace_source",
        "/workspace",
        "read",
        OWNER.id,
    )
    sandboxes.set_resource_policy(
        sandbox["sandbox_id"],
        {"cpu_millis": 1000, "memory_mib": 512, "network_policy": "deny"},
    )
    sandboxes.transition_status(sandbox["sandbox_id"], "ready")
    assert sandboxes.get_sandbox(sandbox["sandbox_id"])["resource_policy"]["network_policy"] == "deny"

    share = ContextShare(
        share_id="context_share_1",
        workspace_id="workspace_1",
        resource=ResourceRef("message", "message_1"),
        source=ContextBoundary(ContextScope.DIRECT_MESSAGE, "conversation_1"),
        target=ContextBoundary(ContextScope.TASK, "task_2"),
        shared_by=OWNER,
        idempotency_key="share_dedupe_1",
        correlation_id="trace_share_1",
        reason="The task explicitly needs the request details.",
    )
    context = ContextRepository(database)
    persisted = context.create_share(share)
    assert persisted["resource_kind"] == "message"
    assert context.create_share(share) == persisted
    assert context.list_scope_shares("workspace_1", ContextScope.TASK, "task_2")[0]["context_share_id"] == "context_share_1"


def test_run_artifact_governance_and_context_records_are_queryable(database):
    agent_actor = seed_agent(database)
    tasks = TaskRepository(database)
    tasks.create_task("task_1", "workspace_1", "Build", "Verified output", OWNER.id, owner_agent_id="agent_builder")
    work = WorkRepository(database)
    request = WorkRequest(
        request_id="request_1",
        workspace_id="workspace_1",
        source=WorkSource.TASK,
        source_ref=ResourceRef("task", "task_1"),
        requester=OWNER,
        target_agent_ids=("agent_builder",),
        instruction="Build task 1",
        idempotency_key="request_task_1",
        correlation_id="trace_task_1",
        task_id="task_1",
    )
    work.create_request(request)
    delivery = work.create_deliveries(request.request_id)[0]
    sandboxes = SandboxRepository(database)
    sandboxes.create_sandbox(
        "sandbox_builder",
        "workspace_1",
        "agent_builder",
        "local",
        "weak_local",
        "agent_home_builder",
    )
    run = work.create_run(
        "run_1",
        delivery["delivery_id"],
        request.request_id,
        "workspace_1",
        "agent_builder",
        "agent_revision_builder_1",
        "sandbox_builder",
        budget={"max_tool_calls": 3},
    )
    assert run["status"] == "queued"
    assert work.transition_run("run_1", "running")["started_at"]
    work.record_run_event("run_event_1", "run_1", "tool.started", actor_id=agent_actor.id)
    publication = ArtifactPublication(
        artifact_id="artifact_1",
        revision_id="artifact_revision_1",
        workspace_id="workspace_1",
        source_run_id="run_1",
        publisher=agent_actor,
        requester_id=OWNER.id,
        visibility=ArtifactVisibility.TASK,
        scope_id="task_1",
        filename="report.md",
        media_type="text/markdown",
        size_bytes=4,
        sha256="0" * 64,
        idempotency_key="artifact_publish_1",
        correlation_id="trace_artifact_1",
        source_task_id="task_1",
        source_ref=ResourceRef("task", "task_1"),
        metadata={"kind": "report"},
    )
    artifact = ArtifactRepository(database).publish(publication, "r2://artifact-1")
    assert artifact["revision"]["metadata"] == {"kind": "report"}
    context = ContextRepository(database)
    memory = context.create_memory(
        "memory_1",
        "workspace_1",
        ContextScope.TASK,
        "task_1",
        "The report is ready for review.",
        OWNER.id,
        source_artifact_id="artifact_1",
        source_run_id="run_1",
    )
    assert memory["source_artifact_id"] == "artifact_1"
    assert context.pin_context(
        "context_pin_1",
        "workspace_1",
        ContextScope.TASK,
        "task_1",
        ResourceRef("artifact", "artifact_1"),
        OWNER.id,
    )["resource_id"] == "artifact_1"
    governance = GovernanceRepository(database)
    approval = governance.create_approval(
        "approval_1",
        "workspace_1",
        OWNER.id,
        "connector.send",
        ResourceRef("connector", "github"),
        "Send the verified report.",
        request_id="request_1",
    )
    assert governance.decide_approval("approval_1", "approved", approver_actor_id=OWNER.id)["status"] == "approved"
    assert governance.approval_is_valid("approval_1")
    member = ActorRef(id="actor_member", kind=ActorKind.HUMAN, display_name="Member")
    identities = IdentityRepository(database)
    identities.create_actor(member, handle="member")
    identities.add_membership("membership_member", "workspace_1", member.id, "member")
    governance.create_approval(
        "approval_member_cannot_decide",
        "workspace_1",
        OWNER.id,
        "connector.send",
        ResourceRef("connector", "github"),
        "Member must not decide this.",
    )
    with pytest.raises(ContractError, match="owner or admin"):
        governance.decide_approval(
            "approval_member_cannot_decide", "approved", approver_actor_id=member.id
        )
    assert governance.get_approval("approval_member_cannot_decide")["status"] == "pending"
    grant = governance.create_grant(
        "grant_1",
        "workspace_1",
        OWNER.id,
        agent_actor.id,
        "connector",
        ResourceRef("connector", "github"),
        "send",
        agent_id="agent_builder",
    )
    assert governance.bind_connector(
        "binding_1",
        "workspace_1",
        "github",
        OWNER.id,
        external_workspace_id="org_1",
        external_identity_id="repo_1",
        agent_id="agent_builder",
        grant_id=grant["grant_id"],
    )["status"] == "active"
    assert governance.append_audit(
        "audit_1",
        "workspace_1",
        "artifact.publish",
        ResourceRef("artifact", "artifact_1"),
        "allowed",
        "trace_artifact_1",
        actor_id=agent_actor.id,
        requester_actor_id=OWNER.id,
    )["decision"] == "allowed"
    assert governance.revoke_grant("grant_1", actor_id=OWNER.id)["status"] == "revoked"
    notification = governance.create_notification(
        "notification_1",
        "workspace_1",
        OWNER.id,
        "artifact.ready",
        ResourceRef("artifact", "artifact_1"),
        "artifact_1:ready",
    )
    assert governance.mark_notification_read(notification["notification_id"], OWNER.id)["read_at"]
    defaults = governance.get_notification_preferences("workspace_1", OWNER.id)
    assert defaults["muted"] is False
    preferences = governance.set_notification_preferences(
        "workspace_1",
        OWNER.id,
        muted=True,
        mention_only=True,
        quiet_hours={"start": "22:00", "end": "07:30", "timezone": "Asia/Ho_Chi_Minh"},
        event_preferences={"message.created": False},
    )
    assert preferences["muted"] is True
    assert preferences["mention_only"] is True
    assert preferences["quiet_hours"]["start"] == "22:00"
    assert preferences["event_preferences"]["message.created"] is False

    governance.create_approval(
        "approval_expiring",
        "workspace_1",
        OWNER.id,
        "connector.send",
        ResourceRef("connector", "github"),
        "Temporary approval.",
        expires_at=NOW,
    )
    assert governance.expire_approval("approval_expiring", now="2026-08-29T12:00:01+00:00")["status"] == "expired"
    governance.create_approval(
        "approval_cancel",
        "workspace_1",
        OWNER.id,
        "connector.send",
        ResourceRef("connector", "github"),
        "Approval to cancel.",
    )
    assert governance.cancel_approval("approval_cancel", actor_id=OWNER.id)["status"] == "canceled"


def test_parity_report_detects_jsonl_corruption_and_preserves_legacy_hash_state(tmp_path):
    base = tmp_path / "state"
    conversation_store = ConversationStore(base)
    conversation_store.save(
        SessionRecord(
            session_id="session_1",
            workspace=str(tmp_path),
            model="test",
            mode="interactive",
            messages=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}],
        )
    )
    (base / "conversations" / "session_1.jsonl").open("a", encoding="utf-8").write("{broken\n")
    board = TeamStore(base / "teams.db")
    from coworker.teams import Actor, Role

    board.create_item("space_1", Actor(id="lead", role=Role.LEAD), title="One", criteria="done")
    chat = __import__("coworker.teams.chat", fromlist=["ChatStore"]).ChatStore(base / "chat.db")
    group = chat.create_group("Build", [{"name": "builder"}])
    chat.post(group["group_id"], "user", "Start", author_role="user")
    database = TeamDatabase(base / "team.db")
    report = database.parity_report(base)
    assert report.legacy_counts["sessions"] == 1
    assert report.legacy_counts["conversation_jsonl_messages"] == 2
    assert report.legacy_counts["team_events"] == 1
    assert report.legacy_counts["chat_messages"] == 1
    assert report.hash_chain_status["space_1"] == "verified"
    assert report.corrupt_sources
    assert not report.is_usable
    chat.close()
    board.close()
    conversation_store.close()
    database.close()
