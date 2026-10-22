"""Skill locations shared by the runtime and the local Skills screen.

OpenCode-compatible skills may already exist on the user's machine. Keep those
locations read-only: Astra can discover and load them, while its own skill
store remains the place where skills are created, edited, and deleted.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _unique_existing(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def machine_skill_dirs(home: Optional[Path] = None) -> list[Path]:
    """Return existing global skill roots used by OpenCode-compatible tools."""
    if home is None and os.environ.get("COWORKER_DISCOVER_MACHINE_SKILLS", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return []
    home_dir = (home or Path.home()).expanduser()
    configured_opencode = os.environ.get("OPENCODE_CONFIG_DIR", "").strip() if home is None else ""
    if configured_opencode:
        opencode_root = Path(configured_opencode).expanduser()
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME", "").strip() if home is None else ""
        opencode_root = (
            Path(config_home).expanduser() / "opencode"
            if config_home
            else home_dir / ".config" / "opencode"
        )
    return _unique_existing(
        [
            opencode_root / "skills",
            home_dir / ".claude" / "skills",
            home_dir / ".agents" / "skills",
            home_dir / ".agent" / "skills",
        ]
    )


def workspace_skill_dirs(workspace: Optional[str | Path]) -> list[Path]:
    """Return project skill roots, from repository ancestors to the active workspace."""
    if workspace is None:
        return []
    current = Path(workspace).expanduser().resolve()
    ancestors: list[Path] = []
    while True:
        ancestors.append(current)
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    paths: list[Path] = []
    for root in reversed(ancestors):
        paths.extend(
            [
                root / ".opencode" / "skills",
                root / ".opencode" / "skill",
                root / ".claude" / "skills",
                root / ".coworker" / "skills",
            ]
        )
    return _unique_existing(paths)


def all_skill_dirs(
    workspace: Optional[str | Path], managed_global: Path
) -> list[Path]:
    """Build the loader order: machine < Astra global < project-local."""
    return _unique_existing(
        [*machine_skill_dirs(), managed_global, *workspace_skill_dirs(workspace)]
    )
