# T11 — Automations bridge

Status: complete
Dependencies: T04, T07, T08, T10  
Unlocks: T14, T15

## Outcome

Keep `Automations` as an independent surface while making every scheduled,
webhook, and connector-triggered action use the same Team runtime and deliver its
result to a chosen conversation/task.

## Checklist

- [x] Let an automation target an agent, group coordinator, explicit roster, or task.
- [x] Convert every trigger into a canonical WorkRequest.
- [x] Configure destination conversation/thread/task for output.
- [x] Snapshot the full agent revision, grants, mounts, context, and budget at run creation;
      revision IDs, redacted full revision snapshots, connector grant snapshots, sandbox
      snapshots, materialized destination context, and budget are durable.
- [x] Add schedule, webhook, connector event, and manual trigger identity.
- [x] Queue due work while a local/external sandbox is offline.
- [x] Add retry, missed-run, catch-up, pause, and cancel semantics.
- [x] Route approvals to Team Activity/Inbox and resume the same run.
- [x] Publish run summary and artifacts to the destination.
- [x] Preserve existing automation history during migration.
- [x] Remove automation-only execution shortcuts that bypass the coordinator.

## Acceptance criteria

- [x] Chat-started and scheduled work have the same permission, run, trace, and
      artifact semantics.
- [x] Automation results do not create orphan sessions.
- [x] Offline targets retain due work and expose queue state.
- [x] Duplicate trigger delivery creates one logical run.
- [x] Canceling an automation stops future triggers without deleting historical runs.

## Verification

- Due while offline, delayed reconnect, duplicate webhook, daylight-saving/timezone,
  approval wait, changed agent settings, and revoked grant cases.

## Likely code surfaces

- `coworker/automation/*`
- `coworker/server/manager.py`
- `surfaces/gui/src/components/ScheduledView.tsx`
- Automation API and E2E tests

## Evidence

Team-targeted scheduled/manual/catch-up runs use the canonical WorkRequest and durable
Team delivery path, retain legacy automation history, avoid scratch SessionRecords, and
carry redacted full revision, sandbox, connector-grant, destination, materialized context,
and budget snapshots. Runtime execution consumes the dispatch-time context package, so
messages added after trigger creation cannot change that run's prompt context. Offline
sandboxes release delivery back to the queue; duplicate client triggers are idempotent;
canceling a queued or claimed run projects the legacy history as skipped.

Latest verification: full `tests/test_team_*.py` suite passes.
