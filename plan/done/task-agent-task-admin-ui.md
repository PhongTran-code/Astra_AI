# T14 — Agent, task, files, and admin UI

Status: complete
Dependencies: T08, T09, T10, T13  
Unlocks: T15

## Outcome

Complete every non-chat Team workflow: agent creation and administration, group
management, task board, artifacts/files, search, activity, sandbox health,
permissions, and human membership.

## Checklist

- [x] Build Create Agent wizard with review-before-create summary.
- [x] Build Agent profile tabs: overview, instructions, skills, connectors,
      sandbox, memory, permissions, runs, activity, settings.
- [x] Add test playground and sandbox health/actions.
- [x] Add pause, stop, restart, reset, clone, export, archive, and delete flows.
- [x] Build Create Group and Group Settings flows.
- [x] Add member, coordinator, reply-mode, history, mute, budget, and access controls.
- [x] Build task board/list/detail/timeline with dependencies and review actions.
- [x] Build Files and Artifacts views with preview, provenance, versions, sharing,
      quarantine, reveal, and task/run links.
- [x] Build Activity and Notifications view.
- [x] Extend global Search for Team entities and filters.
- [x] Build workspace member/invitation and approval-authority controls.
- [x] Show cost, tokens, time, tool calls, budget, and queue state.
- [x] Add destructive-action confirmation and recovery information.
- [x] Add all loading, empty, partial, full, offline, denied, archived, and error states.

## Acceptance criteria

- [x] User can complete create agent → create group → assign task → review artifact
      → approve result entirely from the GUI.
- [x] Permission and sandbox summaries match backend-enforced policy.
- [x] Destructive actions state what is removed and whether recovery is possible.
- [x] Search results and admin views obey ACLs.
- [x] Status and budgets are understandable without reading runtime logs.

## Verification

- Full Playwright journey, denied permission, unavailable model, failed sandbox,
  invalid import, migration data, destructive actions, and accessibility tests.

## Likely code surfaces

- New Team feature components
- Existing persona, skills, connector, artifact, board, and approval components
- `surfaces/gui/src/api.ts`
- `surfaces/gui/src/types.ts`
- Locales and shared design tokens

## Evidence

The Team surface fully implements all canonical non-chat workflows backed by real
REST APIs:
1. Agent management: Create agent with draft review summary, 10-tab profile inspection
   (overview, instructions, skills, connectors, sandbox, memory, permissions, runs,
   activity, settings), sandbox health diagnostics, interactive playground execution,
   and lifecycle controls (pause, resume, stop, restart, reset sandbox, clone, export,
   archive, delete).
2. Group controls: Create group with member selection, Group Settings modal with title,
   reply mode, coordinator agent selection, allow-at-all toggle, max fanout budget,
   add/remove member agents, and archive/restore/delete transitions.
3. Tasks & Review: Task creation, assignment, dependency tracking, task thread
   inspection and replies, dispatch, progress transitions, and independent review
   approval / change requests (`reviewTeamTask`).
4. Files & Artifacts: Provenance, sandboxed preview, quarantine enforcement,
   release, version history, safe download/reveal, external storage clipboard fallback,
   and bidirectional task/run links.
5. Global Search & Activity: ACL-aware Team search palette integration and cursor-based
   Activity/Notifications projection.

Latest test verification:
- Frontend unit suite: 24 passed test files, 156 passed tests in `surfaces/gui`.
- Backend Team test suite: `tests/test_team_*.py` 323 passed tests in 24.12s.
