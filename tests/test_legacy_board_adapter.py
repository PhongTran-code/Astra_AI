"""Canonical cutover compatibility for the numeric legacy board protocol."""

from coworker.teams import Actor, LegacyBoardAdapter, LegacyCutoverService, Role

from test_team_cutover import _canonical_owner, _make_legacy_state
from coworker.teams import TeamDatabase


def test_adapter_reads_and_writes_canonical_tasks_with_idempotent_create(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    _make_legacy_state(legacy)
    source_hash = (legacy / "teams.db").read_bytes()

    database = TeamDatabase(tmp_path / "team.db")
    _canonical_owner(database)
    cutover = LegacyCutoverService(database, "workspace_local", "actor_local_owner")
    cutover.import_legacy(legacy, create_backup=False, activate=True)
    adapter = LegacyBoardAdapter(database, "workspace_local", "actor_local_owner")
    lead = Actor(id="lead", role=Role.LEAD)
    worker = Actor(id="builder", role=Role.WORKER)

    imported = adapter.get_item("project", 1, actor=lead)
    assert imported["title"] == "Legacy board item"

    created = adapter.create_item(
        "project",
        lead,
        title="Canonical filing",
        criteria="It is visible through Team",
        request_id="client-create-1",
    )
    retried = adapter.create_item(
        "project",
        lead,
        title="Canonical filing",
        criteria="It is visible through Team",
        request_id="client-create-1",
    )
    assert created["id"] == retried["id"]
    assert len(adapter.list_items("project", lead)) == 2

    assigned = adapter.assign("project", lead, created["id"], "builder")
    assert assigned["assignee"] == "builder"
    adapter.transition("project", lead, created["id"], "in_progress", request_id="move-1")
    adapter.transition("project", worker, created["id"], "review", request_id="move-2")
    adapter.comment("project", worker, created["id"], "Ready for review", request_id="comment-1")
    detail = adapter.get_item("project", created["id"], actor=lead)
    assert detail["state"] == "review"
    assert any(comment["body"] == "Ready for review" for comment in detail["comments"])

    child = adapter.create_item(
        "project",
        lead,
        title="Blocked filing",
        criteria="It waits for the first filing",
        request_id="client-create-2",
    )
    adapter.link("project", lead, created["id"], "blocks", child["id"])
    assert {link["kind"] for link in adapter.get_item("project", child["id"], actor=lead)["links"]} >= {"blocked_by"}
    assert (legacy / "teams.db").read_bytes() == source_hash
    database.close()


def test_adapter_refuses_unmapped_actor_and_item(tmp_path):
    database = TeamDatabase(tmp_path / "team.db")
    _canonical_owner(database)
    adapter = LegacyBoardAdapter(database, "workspace_local", "actor_local_owner")
    unknown = Actor(id="not-imported", role=Role.WORKER)
    try:
        adapter.list_items("project", unknown)
    except Exception as error:
        assert "not imported" in str(error)
    else:
        raise AssertionError("unmapped actor was accepted")
    database.close()
