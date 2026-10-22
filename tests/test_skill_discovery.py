from __future__ import annotations

from pathlib import Path

from coworker.skills import SkillLoader, SkillStore
from coworker.skills.paths import all_skill_dirs, machine_skill_dirs


def _skill(base: Path, name: str, body: str) -> None:
    folder = base / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_machine_skill_dirs_finds_openwork_compatible_locations(tmp_path):
    agents = tmp_path / ".agents" / "skills"
    claude = tmp_path / ".claude" / "skills"
    agents.mkdir(parents=True)
    claude.mkdir(parents=True)

    assert machine_skill_dirs(tmp_path) == [claude, agents]


def test_loader_reads_direct_and_one_level_nested_skills(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "direct", "direct body")
    _skill(root / "security", "nested", "nested body")

    loader = SkillLoader([root])

    assert {skill.name for skill in loader.items()} == {"direct", "nested"}
    assert loader.get("nested").instructions == "nested body"


def test_managed_and_project_skills_override_machine_skill(tmp_path, monkeypatch):
    machine = tmp_path / "machine"
    managed = tmp_path / "managed"
    workspace = tmp_path / "workspace"
    machine.mkdir()
    managed.mkdir()
    (workspace / ".coworker" / "skills").mkdir(parents=True)
    _skill(machine, "same", "machine body")
    _skill(managed, "same", "managed body")
    _skill(workspace / ".coworker" / "skills", "same", "project body")
    monkeypatch.setattr("coworker.skills.paths.machine_skill_dirs", lambda: [machine])

    loader = SkillLoader(all_skill_dirs(workspace, managed))

    assert loader.get("same").instructions == "project body"


def test_skill_store_lists_detected_skills_as_read_only(tmp_path, monkeypatch):
    detected = tmp_path / "detected"
    detected.mkdir()
    _skill(detected, "from-claude", "detected body")
    monkeypatch.setattr("coworker.skills.store.machine_skill_dirs", lambda: [detected])
    store = SkillStore(global_dir=tmp_path / "managed")

    rows = store.rows()

    assert rows == [
        {
            "name": "from-claude",
            "description": "from-claude skill",
            "instructions": "detected body",
            "scope": "global",
            "source": "detected",
            "enabled": True,
            "path": str(detected / "from-claude"),
            "files": 0,
        }
    ]
    assert store.find_detected("from-claude") == detected / "from-claude"
