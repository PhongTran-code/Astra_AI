# Systematic Debugging Protocol

This protocol prevents symptom patches by requiring evidence from reproduction through regression verification.
It applies to backend, API, persistence, WebSocket, GUI, packaging, concurrency, and connector defects.

## Completion contract

A bug fix is ready for handoff only when the record contains:

- the observed symptom and the expected behavior;
- the actual end-to-end flow and the boundary where it fails;
- a failing test or a minimal reproduction with its limitation stated;
- the root cause and its canonical owner;
- the fix and the invariant it restores;
- the targeted regression command, broader checks required by risk, and their results.

Do not change production code merely because a plausible fix is visible.
Do not close a bug because a test passes if the original failure was never reproduced or explained.

## Phase 1: Trace the actual state flow

Start from the symptom and walk toward the source of truth before editing production code.
Trace only the boundaries the defect actually crosses; do not force every bug into a database-to-UI diagram.

For each boundary, record:

| Boundary | Input | Output | Owner | Invariant | Evidence |
|---|---|---|---|---|---|
| Persistence/schema | | | | | |
| Domain/runtime | | | | | |
| API/transport | | | | | |
| WebSocket/events | | | | | |
| GUI/projection | | | | | |
| Process/external service | | | | | |

Use `plan/target-state.md` for the canonical Team execution flow and state model.
Use the runtime reference for service ports and startup order.

Look for the first divergence between the expected value/state and the observed value/state.
The first failing boundary is the repair candidate; downstream symptoms are evidence, not ownership.

## Phase 2: Prove the failure before fixing it

### Deterministic behavior

1. Write the smallest regression test that expresses the user-visible failure or broken invariant.
2. Run that test and observe a failure caused by the defect.
3. Keep the failure output as the baseline for the fix.
4. Change production code only after the failing baseline is real.

Example commands:

```bash
.venv/bin/pytest tests/test_server.py -q
cd surfaces/gui && npx vitest run src/api.auth.test.ts src/streamGate.test.ts
```

Use the narrowest existing test target that reaches the failing boundary.
A test that only exercises a mock helper is not proof of an API, persistence, or delivery defect.

### Runtime-only or non-deterministic behavior

When a defect cannot be represented faithfully by a deterministic test, capture a minimal reproduction before the fix:

- exact command, inputs, environment, and involved process IDs;
- relevant logs, status codes, event sequence, or screenshot;
- the expected result and the observed result;
- why a failing automated test cannot represent the defect;
- the most durable regression check that can be added afterward.

Examples include startup failures, timing races, packaging behavior, visual rendering, and a real external-service response.
This is an exception to test-first execution, not an exemption from evidence or regression coverage.

## Phase 3: Fix the root boundary

Choose the smallest repair at the first failing owner.

- Fix a schema or persistence contract at the persistence boundary, not by weakening every consumer.
- Fix domain invariants in runtime/domain code, not only by hiding invalid states in the GUI.
- Fix authorization in backend enforcement, not by hiding a button.
- Fix API contract mismatches at the contract owner and update all consumers.
- Fix WebSocket delivery with durable state and catch-up semantics, not with a transient UI retry loop.
- Fix concurrency with lease, idempotency, cancellation, or transition logic, not with an arbitrary sleep.
- Fix connector failures at the I/O boundary with a real error result, not fabricated fallback data.

The protocol forbids symptom-only patches, not a particular syntax.
An `if` or fallback is valid when it expresses the correct boundary rule and its reachable state is understood.

For persistence changes, inspect both fresh-database and existing-database paths before writing a migration.
Preserve data, verify constraint/index order, and test restart, retry, crash, and idempotency behavior when relevant.

## Phase 4: Run the regression boundary

Run checks in this order:

1. the new or targeted failing test, now expected to pass;
2. nearby domain and endpoint tests that share the changed contract;
3. the affected GUI unit, typecheck, build, or E2E checks;
4. a runtime smoke test of the repaired flow;
5. the full relevant suite when the change crosses a subsystem or the risk warrants it.

Use the repository commands from `docs/ref/runtime.md`.
Do not claim the entire repository passes when only targeted checks ran.
Separate pre-existing failures from failures caused by the current change.

The final record must state the exact command, result, and any boundary that remains unverified.

## Boundary-specific regression questions

| Boundary | Minimum questions |
|---|---|
| Persistence/migration | Does fresh setup work? Does existing data survive? Are retries and restarts safe? |
| Backend/API | Are authorization, validation, status codes, and response meaning preserved? |
| WebSocket/delivery | Does reconnect catch up? Can duplicate delivery occur safely? Are terminal states durable? |
| GUI | Are loading, empty, partial, full, offline, denied, error, and recovery states preserved? |
| Concurrency/runtime | What happens on timeout, cancellation, crash, lease expiry, and concurrent workers? |
| Packaging/process | Does the packaged path resolve the same resources and subprocesses as development? |
| External connector | Are credentials scoped and redacted? Does one item failure stay a real, isolated result? |

## Handoff record

```text
Symptom: <what the user observed>
Expected: <behavior or invariant>
Flow: <actual boundaries crossed>
Failing boundary: <canonical owner>
Baseline: <failing test or minimal reproduction>
Root cause: <why the boundary diverged>
Fix: <smallest owner-level repair>
Regression: <exact commands and results>
Unverified: <none, or the precise remaining boundary>
```

Decision history for this protocol and its companion agent entrypoint is recorded in [the AIE execution protocol decision record](../research/ai-first-engineering-protocols.md).
