# Decision: AIE Execution Protocols

Status: accepted
Start time: 2026-09-01T15:35:47+07:00

## Initial purpose

Give a fresh coding agent a reliable path from repository orientation to runtime execution, debugging, and handoff while keeping architecture and operational rules in one source of truth.
The scope covers Claude Code compatibility, Oh My Pi context loading, local service startup, bug diagnosis, and decision history.

## Strategy

1. Read the existing repository contract, target architecture, startup documentation, and actual server/UI configuration.
2. Read Oh My Pi context-file and skill-discovery behavior before choosing an agent entrypoint.
3. Put stable commands and ports in one reference, debugging procedure in one reference, and historical rationale in an immutable record.
4. Reconcile documentation with source-backed ports instead of accepting alternate values as defaults.

## Checklist

- Read `AGENTS.md` and `plan/README.md` to identify the existing contract and plan lifecycle.
- Read `plan/target-state.md` to preserve the canonical Team flow and state model.
- Read `pyproject.toml` and `coworker/server/run.py` to confirm the server entry point and flags.
- Read `surfaces/gui/vite.config.ts` and `surfaces/gui/src-tauri/tauri.conf.json` to confirm the fixed GUI port.
- Read `README.md` and `surfaces/gui/README.md` to find setup commands and documentation drift.
- Read `omp://context-files.md` and `omp://skills.md` to confirm OMP provider discovery and context precedence.
- Create the runtime reference, agent entrypoint, debugging protocol, and this decision record.
- Align `surfaces/gui/README.md` with the source-configured GUI port.

## Result

The repository now has one execution reference, one debugging protocol, and one decision-history location without introducing a parallel `docs/adr/` taxonomy.

- `AGENTS.md` remains the repository contract and is the OMP-visible entrypoint.
- `CLAUDE.md` is a thin Claude Code compatibility entrypoint that delegates authority to `AGENTS.md`.
- `docs/ref/runtime.md` owns stable local startup, testing, build, port, token, and shutdown guidance.
- `docs/ref/debugging.md` owns the four-phase evidence protocol: trace the actual flow, prove the failure, fix the root boundary, and run the regression boundary.
- `docs/research/` owns immutable decision history; a changed decision receives a successor record instead of an in-place rewrite.
- The canonical manual API port is `127.0.0.1:8765`.
- The canonical browser/Tauri Vite port is `localhost:1420` with `strictPort`, matching the Tauri `devUrl`.
- Test-first execution is mandatory for deterministic behavior; runtime-only, visual, packaging, and timing defects require a minimal reproduction and a durable regression check instead.

## Verification

The port and command claims were compared against `pyproject.toml`, `coworker/server/run.py`, `surfaces/gui/vite.config.ts`, and `surfaces/gui/src-tauri/tauri.conf.json` before this record was written.
The GUI README previously named `5173`; it now names the source-configured `1420` port.
The OMP loading decision is grounded in `omp://context-files.md`: standalone root `AGENTS.md` is discovered by the `agents-md` provider, while Claude project context uses `.claude/CLAUDE.md`.
Final documentation scope and whitespace are verified by the closure checks recorded in the task report.

## Alternatives considered

### Create a full root `CLAUDE.md` as the OMP contract

Rejected because OMP discovers the standalone root `AGENTS.md` for this repository, while a root `CLAUDE.md` is not the OMP-native project context path.
A compatibility file is still useful for Claude Code, but it must not become a second contract.

### Create `docs/adr/` for all decisions

Rejected for this repository because the installed documentation convention uses `docs/research/` for immutable rationale and decision records, while `docs/ref/` is the stable lookup area for operating procedures.
Adding `docs/adr/` would create a second history taxonomy without a demonstrated need.

### Make every bug follow a literal DB → API → WebSocket → UI trace

Rejected because defects can start and end at a single boundary, and forcing unused layers creates checklist noise.
The protocol requires tracing the actual boundaries crossed and names the canonical Team flow as the reference when that flow is involved.

### Require a failing automated test for every defect

Rejected as an absolute rule because visual, startup, packaging, external-service, and timing defects are not always faithfully represented by deterministic tests.
The protocol keeps test-first as the default and requires a minimal reproduction plus a durable regression check for the exception path.

### Use a universal full-suite run for every change

Rejected because verification should match the changed contract and risk.
The protocol requires the affected suite and smoke test, then broadens to a full relevant suite when the change crosses a subsystem or the risk warrants it.

## Consequences

- A fresh agent has one required entrypoint and one runtime reference instead of guessing ports or startup order.
- OMP receives the quickstart through `AGENTS.md`; Claude Code receives the compatibility adapter through `CLAUDE.md`.
- Port changes must update source configuration and `docs/ref/runtime.md` together, with consumers checked for consistency.
- Debugging reports become comparable because every fix records the failing boundary, evidence baseline, root owner, and regression result.
- Decision history grows by successor records rather than rewriting old rationale.
- The protocol adds documentation work to changes that alter runtime commands, state contracts, or architecture; this is the cost of preventing repeated rediscovery.

## Decision and cross-references

Decision: adopt the protocol structure and locations above.
Action artifacts: `CLAUDE.md`, `AGENTS.md`, `docs/ref/runtime.md`, `docs/ref/debugging.md`, `surfaces/gui/README.md`.
Architecture reference: `plan/target-state.md`.
Execution plan: `plan/task-aie-execution-protocols.md`.
Cross-references: `plan/README.md`, `README.md`, `surfaces/gui/vite.config.ts`, and `surfaces/gui/src-tauri/tauri.conf.json`.

Reopen this decision when the repository adopts a different harness-native context convention, changes the canonical development ports, replaces Vite/Tauri, or needs a separate contributor workflow beyond operational debugging.
