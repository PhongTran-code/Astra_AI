# T06 — Custom agent lifecycle

Status: complete
Dependencies: T01–T05  
Unlocks: T07–T15

## Outcome

Let users create, configure, test, run, clone, import, export, pause, archive, and
delete persistent custom agents without weakening runtime governance.

## Checklist

- [x] Evolve persona manifests into agent templates and immutable revisions.
- [x] Add agent installation records scoped to a workspace.
- [x] Support identity, avatar, handle, description, and base role.
- [x] Compose skills, plugins, tools, connectors, and model policy.
- [x] Configure sandbox provider, mounts, network, resources, and secrets.
- [x] Configure memory, reply, budget, concurrency, and approval policies.
- [x] Validate unique mention-safe handles per workspace.
- [x] Add test playground in the target sandbox.
- [x] Snapshot the exact agent revision and grants on every run.
- [x] Add pause, stop, restart, reset, clone, export, archive, and delete semantics.
- [x] Keep historical message attribution after rename/archive; retain actor IDs
      in history.
- [x] Define historical attribution behavior for hard deletion.
- [x] Export manifests without credentials, memory, or machine-specific paths.
- [x] Review permissions and dependencies when importing an agent package.

## Acceptance criteria

- [x] A created agent survives restart with the same sandbox and policies.
- [x] Editing an agent cannot alter an already-running run snapshot.
- [x] User-facing custom names map to a finite governance base role.
- [x] Import cannot silently install executable capabilities or grants.
- [x] Archived agents stop receiving work but remain visible in history.
- [x] Deletion has a documented recovery and retention result.

## Verification

- Lifecycle E2E: create, test, DM, edit, restart, clone, export/import, archive, delete.
- Invalid manifest, missing plugin, unavailable model, sandbox failure, and revoked
  connector cases.

## Likely code surfaces

- `coworker/personas/manifest.py`
- `coworker/personas/registry.py`
- `coworker/agents/base.py`
- `coworker/server/app.py`
- Existing persona/skills GUI and new Team agent UI

## Evidence

`AgentLifecycleService` preserves the stable actor ID while updating the current
display name/handle, and archive leaves the durable installation/history visible.
`test_team_agent_rename_preserves_historical_message_attribution` verifies an
agent-authored message remains linked to the same actor and resolves the renamed
identity after archival. `tests/test_server.py` exercises a custom revision
through the canonical worker runtime and verifies the active revision is selected at
dispatch. `tests/test_team_capabilities.py` verifies that revision capabilities are
the actual tool boundary, unsupported required capabilities fail closed, and tools
run against the agent's private sandbox. Hard deletion retention, recovery boundaries,
and sanitized package export are enforced.

Latest verification: full `tests/test_team_*.py` suite passes.
