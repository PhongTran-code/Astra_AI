# AIE Execution Protocols Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every coding harness one canonical runtime reference, one root-cause debugging protocol, and durable decision history without duplicating repository rules.

**Architecture:** `AGENTS.md` remains the OMP-native repository contract and receives a short execution entrypoint. `CLAUDE.md` is a thin Claude Code compatibility adapter. Stable commands and ports live in `docs/ref/runtime.md`; debugging procedure lives in `docs/ref/debugging.md`; historical rationale lives in an immutable `docs/research/` record.

**Tech Stack:** Markdown, Oh My Pi context discovery, Python/pytest, npm/Vite/Tauri.

## Global Constraints

- Preserve all pre-existing working-tree changes, including the untracked `AGENTS.md` and plan files.
- Do not add dependencies, code, migrations, or new runtime behavior.
- API development port is `8765`; Vite/Tauri development port is `1420` with `strictPort`.
- Keep `AGENTS.md` as the repository-wide instruction source; do not duplicate its security and architecture contract.
- Use `docs/ref/` for stable operational references and `docs/research/` for immutable decision records.
- Treat `plan/target-state.md` as target architecture and do not rewrite it for this documentation-only change.

---

### Task 1: Add canonical runtime reference

**Files:**
- Create: `docs/ref/runtime.md`
- Modify: `surfaces/gui/README.md` to align its browser command with the canonical Vite/Tauri port.

**Interfaces:**
- Consumes: `README.md`, `surfaces/gui/README.md`, `surfaces/gui/vite.config.ts`, and `surfaces/gui/src-tauri/tauri.conf.json`.
- Produces: one runtime reference containing startup, test, build, port, token, and stop guidance.

- [ ] **Step 1: Record the source-backed runtime facts**

Read the backend startup command in `README.md`, the GUI commands in `surfaces/gui/README.md`, and the port declarations in `surfaces/gui/vite.config.ts` and `surfaces/gui/src-tauri/tauri.conf.json`.

- [ ] **Step 2: Write the canonical runtime reference**

Document the API command on `127.0.0.1:8765`, browser/Tauri UI on `localhost:1420`, targeted verification commands, and the fact that `1420` is strict for Tauri compatibility.

- [ ] **Step 3: Align the stale GUI README command**

Change its plain-browser URL from `5173` to `1420` and preserve the existing backend/token explanation.

- [ ] **Step 4: Verify the reference against source**

Run a targeted text check over the four source/config files and read the created reference to confirm every command and port matches.

### Task 2: Add agent entrypoints

**Files:**
- Create: `CLAUDE.md`
- Modify: `AGENTS.md` with a concise execution quickstart section and links to the canonical references.

**Interfaces:**
- Consumes: `AGENTS.md` as the existing repository contract and `docs/ref/runtime.md` as the runtime source.
- Produces: a Claude Code adapter and an OMP-visible execution entrypoint with no duplicated architecture rules.

- [ ] **Step 1: Add the Claude Code adapter**

State that `AGENTS.md` is canonical, link the runtime and debugging references, and point architecture questions to `plan/target-state.md`.

- [ ] **Step 2: Add the OMP-visible quickstart**

Insert a short section into `AGENTS.md` covering the runtime reference, canonical ports, targeted backend/GUI verification, and migration ownership.

- [ ] **Step 3: Verify instruction precedence**

Read both files and confirm the adapter delegates authority to `AGENTS.md` rather than creating a competing rule set.

### Task 3: Add systematic debugging protocol

**Files:**
- Create: `docs/ref/debugging.md`

**Interfaces:**
- Consumes: `AGENTS.md` lifecycle and verification rules plus `plan/target-state.md` canonical execution flow.
- Produces: a reusable four-phase protocol for reproducible bug work.

- [ ] **Step 1: Define the evidence ledger**

Require symptom, affected flow, failing boundary, expected invariant, reproduction evidence, and verification command before a fix is accepted.

- [ ] **Step 2: Define the four phases**

Cover actual-flow tracing, failing-test or minimal-reproduction-first, root-cause repair, and affected-suite regression verification with explicit exceptions for runtime-only defects.

- [ ] **Step 3: Define boundary-specific guidance**

Separate database/migration, backend/API, WebSocket, GUI, packaging, concurrency, and external-service checks without pretending every bug crosses every layer.

- [ ] **Step 4: Verify protocol consistency**

Read the protocol against `AGENTS.md`, `plan/target-state.md`, and the runtime reference; remove any contradictory command, port, or authority claim.

### Task 4: Record the protocol architecture decision

**Files:**
- Create: `docs/research/ai-first-engineering-protocols.md`

**Interfaces:**
- Consumes: the implemented references and source-backed port/runtime evidence.
- Produces: an immutable decision record explaining why the repository uses an AGENTS contract, a thin CLAUDE adapter, `docs/ref/` protocols, and `docs/research/` history.

- [ ] **Step 1: Record context and alternatives**

Capture the decision scope, rejected `docs/adr/` duplication, rejected universal DB-to-UI tracing, and rejected unconditional full-suite/TDD requirements.

- [ ] **Step 2: Record consequences and reopen triggers**

State maintenance responsibilities, OMP/Claude compatibility behavior, and the conditions that require a successor decision record.

- [ ] **Step 3: Link the decision to its artifacts**

Reference the runtime, debugging, agent, architecture, and plan documents from the decision record.

### Task 5: Verify the complete documentation set

**Files:**
- Verify: all files created or modified by Tasks 1–4.

- [ ] **Step 1: Check Markdown references**

Verify every relative link resolves and every command/port claim has a source or explicit rationale.

- [ ] **Step 2: Check working-tree scope**

Run `git status --short` and `git diff --check`; do not stage, reset, clean, or modify unrelated pre-existing work.

- [ ] **Step 3: Read the final artifacts**

Confirm the final documents are dense, consistent, English-only in code-facing content, and actionable for a fresh agent session.
