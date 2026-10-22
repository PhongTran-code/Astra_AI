# Astra Claude Code Entrypoint

This file is the Claude Code compatibility entrypoint for this repository.
`AGENTS.md` is the canonical repository contract and takes precedence over this adapter.
Do not duplicate or weaken its security, data-integrity, isolation, architecture, or verification rules.

## Start here

1. Read `AGENTS.md` before editing anything.
2. Read `plan/target-state.md` before changing Team architecture, state, routing, persistence, or permissions.
3. Read `docs/ref/runtime.md` before starting services or choosing a port.
4. Read `docs/ref/debugging.md` before fixing a bug.
5. Read the relevant `docs/research/` decision record before removing or reshaping behavior with a non-obvious rationale.

## Quick cheatsheet

| Need | Command or reference |
|---|---|
| Start the API | `docs/ref/runtime.md#start-the-api` |
| Start the browser GUI | `docs/ref/runtime.md#start-the-browser-gui` |
| Start the desktop GUI | `docs/ref/runtime.md#start-the-desktop-gui` |
| Backend targeted tests | `.venv/bin/pytest tests/test_server.py -q` |
| Backend Team tests | `.venv/bin/pytest tests/test_team_*.py -q` |
| Backend full suite | `.venv/bin/pytest tests -q` |
| GUI unit tests | `cd surfaces/gui && npm test` |
| GUI typecheck/build | `cd surfaces/gui && npx tsc --noEmit && npm run build` |
| GUI E2E | `cd surfaces/gui && npm run e2e` |
| Debugging workflow | `docs/ref/debugging.md` |

## Port discipline

The canonical ports and the only supported local-development defaults are defined in `docs/ref/runtime.md#canonical-ports`:

- API: `127.0.0.1:8765`.
- Browser/Tauri Vite server: `localhost:1420` with `strictPort`.

Do not silently switch to `8766`, `1421`, `5173`, or another free port.
If a temporary override is necessary, pass it consistently to every consumer and record it in the run report.

## Source-of-truth boundaries

- Backend/runtime owns authorization, persistence, migrations, state transitions, delivery, budgets, and invariants.
- The GUI reflects server state; it is not the authority for permissions, readiness, completion, persistence, or grants.
- `plan/target-state.md` owns the current Team target architecture.
- `docs/ref/` owns stable operating procedures and commands.
- `docs/research/` owns immutable decision history; supersede a changed decision with a new record instead of rewriting the old one.

## Completion rule

A change is not complete when code or documentation is written.
Run the narrowest proving command, broaden verification according to risk, inspect the final scope, and report exact evidence and any unverified boundary.
