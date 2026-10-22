# AGENTS.md — Astra Engineering Contract

This file is the engineering contract for AI coding agents working in this repository.
It applies to the repository root and all descendants. A more-specific AGENTS.md
may add narrower rules for a subsystem, but must not weaken the repository-wide
security, data-integrity, isolation, or verification requirements in this file.

Astra is a local-first desktop AI coworker platform. It has a Python agent runtime
and FastAPI server, a React/TypeScript GUI inside a Tauri desktop shell, a Rust
speech-to-text sidecar, model providers, connectors, automations, governed tool
execution, and a persistent Team collaboration surface.

The objective is to make the smallest reliable change that preserves existing
behavior and moves the product toward its documented architecture.

## 1. Instruction priority

Follow instructions in this order:

1. Explicit user or task requirements.
2. This file and any applicable more-specific AGENTS.md.
3. The authoritative architecture and product documents.
4. The active task's scope, checklist, and acceptance criteria.
5. Existing conventions in the affected subsystem.
6. General engineering convention.

For Team work, use these sources deliberately:

- plan/target-state.md — product and target-architecture source of truth.
- plan/README.md — dependency order and global completion gates.
- plan/*.md and plan/done/*.md — task-local scope and acceptance evidence.

Plans describe intent and may be ahead of the current implementation. Confirm
important claims against source code and tests. If documents, code, and the task
disagree, surface the conflict; do not silently invent a fourth behavior.

## 2. Work modes

Identify the requested mode before acting:

- Implement — change code, tests, configuration, or docs as requested.
- Diagnose/review — investigate and report; do not modify files unless asked.
- Plan/design — produce the design, contracts, risks, and verification plan;
  do not modify production code.

For a non-trivial implementation, establish this contract before editing:

~~~
Goal
Current behavior
Expected behavior
Canonical owner of the behavior
Callers and consumers
Invariants at risk
Tests that characterize the behavior
Verification required
~~~

Do not begin with a speculative refactor. Trace the relevant behavior first.

## 2.1 Execution quickstart

Use `docs/ref/runtime.md` as the canonical reference for startup, ports, tests, builds, token handling, and process shutdown.

- API development server: `127.0.0.1:8765`.
- Browser/Tauri Vite development server: `localhost:1420`; `vite.config.ts` enforces `strictPort`.
- Start the API before browser Vite so the GUI can read its launch token.
- For bugs, follow `docs/ref/debugging.md` before changing production code.
- Backend/runtime owns permissions, persistence, migrations, state transitions, budgets, and delivery semantics; the GUI only projects server state.
- Persistence changes require fresh-database and existing-database checks, with migration behavior verified before and after the change.

## 3. Protect the working tree

Assume every pre-existing modification belongs to the user or another agent.
The working tree may be dirty. Inspect git status --short before editing and
inspect the final diff afterward.

Never discard, revert, overwrite, or mass-format unrelated changes. Do not use
these destructive commands unless the user explicitly requests the operation:

~~~
git reset --hard
git checkout -- <path>
git restore <path>
git clean -fd
git clean -fdx
~~~

If a task overlaps an already-modified file, preserve the existing work and make
the smallest additive or clearly scoped change. Keep generated files, caches,
temporary outputs, and local secrets out of commits unless the task explicitly
requires them. Never expose credentials or tokens in command output, logs, tests,
fixtures, screenshots, or documentation.

## 4. Repository map and ownership

~~~
coworker/                 Python package and agent runtime
coworker/teams/           Team domain, contracts, persistence, routing, runtime,
                          permissions, sandbox, agents, connectors, sharing
coworker/server/          FastAPI application and local server/session management
coworker/providers/       Model-provider implementations and capability routing
coworker/connectors/      External integrations, identities, relays, senders
coworker/automation/      Scheduled and event-triggered work
coworker/tools/           Local file, shell, git, web, and agent tools
coworker/memory/          Memory stores and memory tools
surfaces/gui/             React + TypeScript GUI and Playwright suites
surfaces/gui/src-tauri/   Tauri desktop shell and process supervision
stt/                      Rust speech-to-text sidecar
packaging/                Dev bootstrap, PyInstaller, installers, and updates
tests/                    Python backend and integration tests
plan/                     Target architecture and implementation plans
docs/                     Documentation and configuration examples
~~~

Find the canonical owner before adding logic. In particular:

- domain invariants belong in domain/runtime code, not only the GUI;
- authorization belongs in backend/runtime enforcement, not client visibility;
- durable state belongs in persistence, not transient WebSocket state;
- routing belongs in routing/coordinator code, not presentation components;
- provider-specific behavior belongs behind provider/capability abstractions;
- Team execution must use canonical Team contracts and the shared work pipeline.

Do not duplicate a rule in an endpoint, component, or helper merely because the
existing owner is inconvenient to reach.

## 5. Astra naming and compatibility

The current product brand is Astra. Legacy names such as
coworker and ocw remain valid in package names, commands, internal modules,
compatibility paths, or historical APIs.

Do not rename legacy identifiers for cosmetic consistency. A rename can affect
CLI compatibility, persisted configuration, installer behavior, environment
variables, integrations, tests, and upgrade paths. Treat it as an explicit,
backward-compatible migration task.

## 6. Team invariants

Changes touching coworker/teams/, Team API routes, Team GUI, Team automations,
or Team integrations must preserve these invariants.

### Canonical work pipeline

Direct messages, group messages, tasks, automations, connector events, and agent
handoffs must converge on the canonical WorkRequest flow:

~~~
source event
  → durable persistence
  → deterministic routing and authorization
  → durable delivery
  → lease and heartbeat
  → scoped context package
  → isolated sandbox/runtime
  → messages, task events, artifacts, or approvals
  → terminal state
  → projections, notifications, and trace
~~~

Do not create a private execution path that bypasses permissions, budgets,
delivery semantics, tracing, or persistence.

### Agents and isolation

- Persistent agents are visible, durable teammates.
- Ephemeral subagents are internal execution helpers.
- Persistent agents must not implicitly share one mutable coding workspace.
- Coding work should use per-task worktrees, snapshots, or an equivalent
  isolation boundary.
- A separate directory is not automatically a security sandbox.

### Routing and state

- Routing is deterministic before model execution where the architecture says so.
- Models may propose work but may not override recipients, permissions, budgets,
  sandbox boundaries, or state-machine transitions.
- Task dependency readiness is enforced in the backend/runtime, not only by UI
  disabled states.
- A builder must not approve its own protected task.

### Durability and delivery

REST/query stores are authoritative. WebSocket events are live projections and
must tolerate reconnect and cursor-based catch-up behavior.

The delivery model is at-least-once with idempotency. Do not claim exactly-once
execution without a deliberate architecture change that actually provides it.
For every retryable side effect, consider duplicate delivery, idempotency keys,
crash recovery, lease expiry, and partial success.

## 7. Security and governance

Governance is a backend security boundary, not a frontend feature. Effective
authority is the intersection of all applicable constraints:

~~~
platform ceiling
∩ workspace role
∩ requester authority
∩ agent role
∩ plugin/tool policy
∩ connector grant
∩ conversation policy
∩ task scope
∩ sandbox policy
∩ approval decision
~~~

Never turn this intersection into a permissive union or rely on the client to
enforce it. Hidden buttons, disabled controls, route visibility, and client-side
role state are UX only.

Prompt text, model output, or user-provided natural language must not silently
grant permissions, identities, sandbox access, elevated roles, or approval
exemptions. Preserve human-only floors and approval/reviewer behavior.

For changes involving permissions, shell commands, files, tools, connectors,
secrets, or agent-to-agent communication, answer these questions in code and
tests:

~~~
Who requests the action?
Who owns the resource?
Which workspace applies?
Which agent executes it?
What exact capability is requested?
Which policy grants it?
Does an approval apply?
Can prompt content bypass the rule?
Can retries duplicate the side effect?
What sensitive data is persisted or logged?
~~~

Use existing redaction and secret-reference mechanisms. Never log API keys,
OAuth tokens, bearer tokens, cookies, or connector credentials.

## 8. Connectors and context boundaries

Connector authority is scoped to the requester, workspace, agent, identity, and
specific action. The existence of a locally stored account does not authorize an
agent to use it.

Respect the Team context scopes:

- private agent memory;
- direct-message memory;
- group shared memory;
- task memory;
- workspace knowledge;
- run scratch context.

Direct-message content must not enter group context without an explicit,
authorized share operation. Retrieve the minimum context needed; do not merge
all available conversations or memories as a shortcut.

## 9. Persistence, migrations, and concurrency

Persistence changes are high-risk. Before editing schema or repositories:

1. inspect current schema and migration behavior;
2. identify fresh-database and existing-database paths;
3. preserve existing data and compatibility;
4. keep migrations idempotent where expected;
5. verify constraints and indexes are created in valid dependency order;
6. test upgrade, restart, crash, and retry behavior where relevant.

Never delete or rebuild user data to make a migration pass. Do not assume a test
fixture represents every legacy installation.

For coordinator, delivery, automation, sandbox, or runtime changes, explicitly
consider retries, duplicate work, cancellation, process crash, restart, lease
expiry, timeout, concurrent workers, stale state, partial success, and budget
exhaustion. Use canonical transition validation where it exists.

## 10. API, frontend, and provider boundaries

FastAPI endpoints are transport boundaries, not the primary owner of domain
behavior. When changing an API contract, inspect all GUI consumers, tests,
automations, and connector consumers. Prefer additive evolution and preserve
the meaning of existing fields unless a breaking change is explicit.

The GUI reflects server state. It must not become the source of truth for
permissions, task readiness, run completion, persistence, agent ownership, or
connector grants.

For relevant UI changes, cover or consciously verify:

~~~
loading · empty · partial · full · offline · denied · error · recovery/reconnect
~~~

Preserve existing behavior, keyboard interaction, and important system state.
A visual change must not silently remove capabilities, change routing/defaults,
hide approvals, or turn explicit approval into implicit approval.

Astra is provider-agnostic. Do not hard-code one provider's assumptions into
canonical agent behavior. Keep optional provider dependencies lazy where
possible so unrelated functionality can still import and run.

## 11. Dependencies and packaging

Before adding a dependency, check whether existing dependencies or the standard
library are sufficient. Consider Python 3.10+, Node 20+, desktop bundling,
PyInstaller, Tauri, macOS, Windows, license compatibility, binary requirements,
bundle size, and local-first/offline behavior.

The CI-supported GUI workflow uses npm and surfaces/gui/package-lock.json.
Do not casually switch package managers or rewrite lockfiles unrelated to the
task.

Packaging is part of correctness. Changes to dependencies, paths, environment
variables, spawned processes, bundled resources, or sidecar behavior must be
evaluated in both development and packaged-app contexts when applicable.

## 12. Documentation and planning

Keep documentation synchronized with implemented behavior. Do not document an
intended architecture as if it already shipped.

For architecture-level changes, update plan/target-state.md first or in the
same coherent change, then update affected task plans. Completed plans belong in
plan/done/ according to plan/README.md.

Prefer one canonical document over overlapping explanations. Keep docs dense:
state the conclusion, contracts, risks, and verification evidence without filler.

## 13. Discovery and implementation discipline

When .codegraph/ exists, use CodeGraph before text search for architectural or
symbol-relationship questions. Otherwise use rg and rg --files.

During implementation:

- keep the diff scoped;
- reuse canonical abstractions;
- avoid speculative abstractions and unrelated cleanup;
- fix the causal boundary, not only the visible symptom;
- add a regression test for a fixed defect when practical;
- update tests and contracts together when behavior intentionally changes.

For bugs, trace:

~~~
symptom → failing boundary → root cause → fix → regression test
~~~

## 14. Verification commands

Use targeted tests while iterating, then broaden verification according to risk.
The repository's primary commands are:

~~~bash
# Backend
.venv/bin/pytest tests -q

# GUI, from surfaces/gui/
npm ci                 # dependency bootstrap when needed
npx tsc --noEmit       # typecheck
npm test               # unit tests
npm run build          # production frontend build
npm run e2e            # hermetic Playwright suite

# Desktop/package work, from the repository root as documented
bash packaging/build_dmg.sh
./packaging/build_windows.ps1

# Always inspect scope and whitespace
git status --short
git diff --check
git diff
~~~

Choose verification by affected surface:

- pure Python behavior: targeted test, then nearby backend tests;
- API behavior: domain tests, endpoint tests, and relevant GUI contract tests;
- persistence/migration: repository tests plus fresh and upgrade database paths;
- Team runtime: lifecycle, authorization, retry, crash, and idempotency tests;
- GUI workflow: component test, typecheck, and affected E2E spec;
- packaging/runtime: relevant build or sidecar verification in addition to tests.

Do not claim that the entire suite passes when only targeted tests ran. Report
exact commands and results.

## 15. Baseline failures and completion standard

The repository can contain pre-existing failures or unrelated user changes. When
validation fails, determine whether the current diff caused it, report unrelated
baseline failures separately, and do not change unrelated systems just to make
the whole suite green.

A task is done only when all applicable conditions hold:

- requested behavior is implemented;
- the correct architectural owner was changed;
- security, durability, isolation, and compatibility invariants remain intact;
- appropriate regression coverage exists;
- risk-appropriate tests/typecheck/builds pass;
- git diff --check passes;
- the final diff has no unexplained task-owned changes;
- pre-existing user work remains preserved;
- limitations and unverified areas are explicitly reported.

Use an evidence-based completion report:

~~~
Outcome:
<what changed>

Verification:
- <command>: PASS/FAIL

Not verified:
- <anything not run>

Known unrelated baseline issues:
- <only when applicable>

Files changed:
- <task-owned files>
~~~

Never equate “code written” or “agent says it works” with completion.

## 16. High-risk areas

Treat these areas as high-risk and inspect callers plus tests before editing:

~~~
coworker/permissions.py
coworker/reviewer.py
coworker/risk.py
coworker/secrets.py
coworker/teams/contracts.py
coworker/teams/permissions.py
coworker/teams/persistence.py
coworker/teams/coordinator.py
coworker/teams/runtime.py
coworker/teams/sandbox.py
coworker/teams/secrets.py
coworker/server/app.py
coworker/server/manager.py
coworker/connectors/
coworker/automation/
packaging/
surfaces/gui/src-tauri/
~~~

These modules are large or cross-cutting in the current repository. Do not add
logic based only on where a nearby function happens to be. Do not perform a
broad rewrite during an unrelated task.

## 17. Anti-patterns

Avoid:

- coding before locating the canonical owner;
- fixing a symptom without tracing state flow;
- frontend-only authorization;
- parallel sources of truth;
- silent fallbacks that change semantics;
- broad exception handling that hides defects;
- hard-coded provider assumptions;
- implicit secret propagation;
- shared mutable coding workspaces between agents;
- unbounded agent-to-agent loops;
- model-controlled security policy;
- exactly-once claims without exactly-once guarantees;
- mass formatting or rewriting unrelated files;
- declaring success without running the required verification.

Optimize in this order:

~~~
correctness → preserved invariants → security → recoverability
→ testability → maintainability → simplicity → implementation speed
~~~
