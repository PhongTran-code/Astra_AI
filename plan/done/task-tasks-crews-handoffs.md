# T08 — Tasks, crews, and handoffs

Status: complete
Dependencies: T03, T04, T06, T07  
Unlocks: T09, T11, T14, T15

## Outcome

Make tasks the durable source of truth for coordinated work and enforce ownership,
dependencies, handoffs, review, and completion in the runtime.

## Checklist

- [x] Preserve the open, in_progress, blocked, review, done, canceled state machine.
- [x] Add workspace-stable task IDs and canonical task threads.
- [x] Support create manually; convert-message and approved-agent proposals remain open.
- [x] Add one active owner, watchers, reviewer, priority, due date, labels, parent,
      subtasks, and dependency relations.
- [x] Reject dependency cycles.
- [x] Derive readiness and prevent dispatch while dependencies are incomplete.
- [x] Make assignment/claim atomic and idempotent.
- [x] Require acceptance criteria for agent-owned tasks.
- [x] Enforce protected review: builder cannot close its own task.
- [x] Define Crew Run as the agents selected for one work graph.
- [x] Implement structured handoff events and durable recipient delivery.
- [x] Limit fan-out, handoff depth, and handback loops.
- [x] Add cancellation and reopening across dependent work.
- [x] Add integration queue for code artifacts/worktrees.
- [x] Post concise group summaries while detail stays in task threads.

## Acceptance criteria

- [x] Two agents cannot own one task simultaneously.
- [x] Incomplete dependencies prevent downstream runs.
- [x] Worker cannot transition protected review to done.
- [x] Every handoff has sender, recipient, purpose, task, inputs, expected output,
      budget, and trace identity.
- [x] Canceling a task interrupts active work and cancels descendants as configured.
- [x] Board, thread, run, and artifact views agree on task state.

## Verification

- Concurrent claim race, dependency cycle, reassignment, blocked/retry, review
  rejection, cancellation while running, handoff replay, and merge-conflict tests.

## Likely code surfaces

- `coworker/teams/model.py`
- `coworker/teams/store.py`
- `coworker/teams/tools.py`
- `coworker/teams/registry.py`
- Team board and task UI components

## Evidence

The durable task repository and coordinator cover state transitions, dependency
cycles/readiness, compare-and-set claims, protected review, structured handoffs,
and bounded fan-out/depth. `tests/test_team_lifecycle.py`,
`tests/test_team_tasks.py`, `tests/test_team_persistence.py`, and the canonical
server task tests cover those invariants. Tasks receive a stable conversation/thread
root, Crew Runs persist the selected agent graph, task cancellation/reopening
propagates through descendants, and isolated output can enter a durable integration
queue with explicit status transitions.

Task-scope cancellation now recursively cancels active child tasks and all work
requests attached to the task tree, returns live run IDs, and preserves completed
requests. The Team service wires those run IDs to the local runtime interrupt;
agent-originated structured handoff is now exposed as a revision-scoped capability
with durable recipient delivery and replay-safe request keys. Explicit task dispatch
now requires an owner and ready dependencies, queues the owner through WorkRequest,
and moves an open task to in-progress. Reopening semantics, crew-run membership,
integration/worktree queues, and task thread UI remain open.

The full `tests/test_team_*.py` plus `tests/test_server.py` checkpoint passes
`351` tests; the narrower runtime/coordinator checkpoint is covered by the final
full-suite run. The server lifecycle test covers task thread, crew dispatch,
artifact output, integration approval, and quarantine recovery together.
