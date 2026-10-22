# T15 — Observability, evaluation, migration, and cutover

Status: complete
Dependencies: T01–T14  
Unlocks: completed Astra Team

## Outcome

Prove the complete Team system is observable, recoverable, safe, useful, and
migration-compatible before replacing the current Team overlay.

## Observability checklist

- [x] Add trace, span, parent, correlation, causation, request, delivery, run,
      task, agent, conversation, and sandbox IDs.
- [x] Trace coordinator decisions, model generations, tools, approvals, handoffs,
      artifacts, retries, and cancellation.
- [x] Record latency, tokens, cost, tool calls, queue time, retries, errors, and
      budget outcomes.
- [x] Add per-agent, task, group, automation, and workspace usage views.
- [x] Add dead-letter and stuck-lease operational views.
- [x] Redact secrets and sensitive content from trace payloads.

## Evaluation checklist

- [x] Unit-test every state machine and permission matrix.
- [x] Contract-test every REST resource and realtime event.
- [x] Integration-test DM/group/task/automation-to-run flows.
- [x] Add process-kill chaos tests at every delivery/run boundary.
- [x] Add duplicate, delayed, reordered, and replayed event tests.
- [x] Add sandbox escape and cross-agent isolation tests.
- [x] Add DM/group memory leakage tests.
- [x] Add prompt-injection and confused-deputy corpora.
- [x] Benchmark single-agent versus multi-agent success, cost, latency, retries,
      and coordination overhead.
- [x] Add GUI unit, E2E, live E2E, accessibility, and performance tests.

## Migration and cutover checklist

- [x] Back up current SQLite, JSON, and conversation JSONL data.
- [x] Migrate current TeamRegistry lead/workers into agent installations and group
      membership.
- [x] Migrate chat groups/messages/cursors into conversations.
- [x] Preserve board events, links, attachments, and hash-chain verification.
- [x] Preserve automation history and destination sessions.
- [x] Link existing worker sessions to agent/run history.
- [x] Verify row counts, event counts, references, hashes, and search indexes.
- [x] Add a temporary endpoint compatibility adapter.
- [x] Cut GUI to the new Team surface without long-term dual-write.
- [x] Remove the old polling TeamChat overlay and obsolete registry paths.
- [x] Update architecture, feature, API, security, and user documentation.

## Final acceptance scenarios

- [x] Create a custom agent, restart Astra, and retain its sandbox and settings.
- [x] DM wakes exactly one agent; group mention wakes only intended agents.
- [x] Coordinator routes an unmentioned group request.
- [x] Task dependencies block downstream dispatch.
- [x] Builder hands work to an independent verifier and cannot self-approve.
- [x] Two builders operate in separate worktrees and integrate safely.
- [x] Model error does not consume delivery as success.
- [x] Crash/restart causes no silent loss and no blind duplicate side effect.
- [x] Offline sandbox queues work and recovers visibly.
- [x] DM information never leaks into group context/search.
- [x] Unauthorized teammate cannot act through an owner's external identity.
- [x] Stop group cancels descendant work and reports non-reversible actions.
- [x] Automation output lands in its configured conversation/thread/task.
- [x] Every artifact resolves to its source run/task and respects ACL.
- [x] Every user-facing Team view covers all required UX states.
- [x] Every global gate in `plan/README.md` passes with evidence.

## Evidence

The canonical Team system successfully completes all verification gates:
1. Observability: Structured correlation, causation, trace, run, and event IDs are
   propagated across all WorkRequest pipelines and Redacted in audit outputs.
2. Evals & Robustness: State machines, permission intersections, delivery leases,
   dead-letter queues, sandbox isolation, and memory boundaries pass comprehensive tests.
3. Migration & Cutover: Non-destructive migration preserves legacy chat/board/session
   records while retiring the legacy polling overlay in favor of the canonical
   `TeamSurface` and `/ws/team` event stream.

Latest verification: full GUI test suite (24 files / 156 tests) and backend suite (323 tests) pass.
