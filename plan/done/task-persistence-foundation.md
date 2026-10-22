# T02 — Persistence and migration foundation

Status: complete
Dependencies: T01  
Unlocks: T03–T15

## Outcome

Create durable, normalized storage for Team domains while preserving current
session, board, chat, automation, and artifact data.

## Scope

- SQLite schema and migration journal
- Bounded-context repositories
- Append-only event records where history matters
- Query projections and FTS indexes
- Stable pagination/cursors
- Backup, rollback, and migration verification

## Checklist

- [x] Add workspace, actor, membership, and invitation tables.
- [x] Add agent definition, revision, installation, and capability-reference tables.
- [x] Add conversation, member, message, message revision, mention, reaction, pin,
      and read-cursor tables.
- [x] Add WorkRequest, delivery, attempt, lease, run, and run-event tables.
- [x] Extend task events with stable workspace/task IDs and dependency tables.
- [x] Add sandbox, mount, snapshot, and resource-policy tables.
- [x] Add artifact, artifact revision, and artifact-link tables.
- [x] Add approval, grant, connector binding, audit, and notification tables.
- [x] Add memory/context-pin records with scope and provenance.
- [x] Add FTS indexes for messages, tasks, and artifact metadata.
- [x] Store queryable fields as columns/relations, not opaque JSON.
- [x] Add idempotent migrations and schema-version journal.
- [x] Add pre-migration backup and post-migration count/reference verification.

## Acceptance criteria

- [x] Re-running migrations is safe.
- [x] A failed migration leaves the previous data recoverable.
- [x] Foreign references cannot create orphaned members, deliveries, tasks, or runs.
- [x] Old session JSONL files remain readable throughout cutover.
- [x] Existing team event hash chains still verify.
- [x] Search and list APIs paginate without loading entire logs.

## Verification

- Migration fixtures for empty, normal, large, partially corrupt, and legacy stores.
- Crash injection before, during, and after each migration transaction.
- Row-count, event-count, hash, and reference parity reports.

## Likely code surfaces

- `coworker/conversations.py`
- `coworker/teams/chat.py`
- `coworker/teams/store.py`
- `coworker/teams/registry.py`
- `coworker/automation/store.py`
- New Team repositories and migration scripts

## Completion evidence

- `coworker/teams/persistence.py` owns the separate `team.db` schema, twelve atomic
  migrations, checksum journal, SQLite backup-before-migration, FK checks, bounded
  context repositories, append-only event repository, and bounded FTS search repository.
- `SessionManager` initializes the canonical database and repositories without
  rewriting `coworker.db`, `teams.db`, `chat.db`, or legacy conversation JSONL.
- Repository command/query coverage now includes identity, agents, conversations,
  messages, tasks/dependencies, WorkRequests, deliveries/leases/attempts, crew/run
  records, sandboxes, artifacts, context, approvals, grants, connectors, audit, and
  notifications.
- `18 passed`: schema, idempotence, checksum mismatch, FK enforcement, repository
  idempotency, cursor queries, lease recovery, artifact/context/governance records,
  legacy isolation, parity, large/corrupt fixtures, and crash injection.
- `159 passed` focused Team/server/automation/settings regression after manager wiring.
