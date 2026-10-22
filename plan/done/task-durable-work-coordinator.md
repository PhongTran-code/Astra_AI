# T04 — Durable work coordinator

Status: complete
Dependencies: T01, T02  
Unlocks: T05–T15

## Outcome

Route every execution entry point through one durable WorkRequest, delivery,
lease, run, retry, cancellation, and budget pipeline.

## Checklist

- [x] Implement canonical WorkRequest creation for every source type.
- [x] Persist source event before routing.
- [x] Resolve recipients deterministically before model execution.
- [x] Create durable inbox deliveries with idempotency keys.
- [x] Implement atomic lease claim, heartbeat, expiry, and recovery.
- [x] Separate delivery acceptance from run outcome.
- [x] Ack only after the documented durable boundary.
- [x] Add safe retry/backoff and visible dead-letter records.
- [x] Add per-agent concurrency and priority queues.
- [x] Let direct steering outrank queued background work.
- [x] Add hierarchical cancellation for group, task, handoff, delivery, and run.
- [x] Add token, cost, model-call, tool-call, time, wake, fan-out, and handoff budgets.
- [x] Execute claimed deliveries through a revision-scoped worker runtime and persist
      output/usage before successful ACK.
- [x] Make consequential tool retries idempotent or explicitly non-retriable.
- [x] Split coordinator responsibilities out of `SessionManager`.

## Acceptance criteria

- [x] Model errors never consume a delivery as successful.
- [x] Duplicate inputs create one logical WorkRequest.
- [x] Process restart reclaims expired leases and preserves pending work.
- [x] A completed external side effect is not repeated blindly after replay.
- [x] Stop group cancels all descendant work and reports what cannot be undone.
- [x] Budget exhaustion pauses work with a user-visible recovery action.

## Verification

- Kill the process at every boundary from persist through ack.
- Duplicate, reorder, delay, and replay events.
- Test busy agent, offline agent, expired lease, dead-letter, and manual redrive.

## Likely code surfaces

- `coworker/server/manager.py`
- `coworker/engine.py`
- `coworker/teams/store.py`
- `coworker/automation/scheduler.py`
- New coordinator, delivery, lease, and run services

## Evidence

`tests/test_team_coordinator.py` covers canonical source submissions, deterministic
fan-out, leases, retry/dead-letter recovery, cancellation, idempotency, budget
pause/resume, and lifecycle gates for paused/offline/archived agents. The canonical
`TeamWorkerRuntime` claims a delivery, runs the revision-scoped harness, persists
agent output before ACK, heartbeats the lease, records model/tool usage, and keeps
ordinary provider failures retryable. Consequential external and sandbox tools enforce
idempotency keys or fail-closed non-retriable guarantees on replay. Hierarchical scope
and task cancellation cleanly terminate live local engines and recursively cancel
attached subtasks and descendant workrequests.

Latest verification: full `tests/test_team_*.py` suite (323 tests) passes cleanly.
