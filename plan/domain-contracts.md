# Astra Team domain contract v1

Status: frozen for implementation  
Contract version: 1  
Code: `coworker/teams/contracts.py`, `routing.py`, `lifecycle.py`, `sharing.py`, and
`api_contracts.py`

## Contract rules

1. IDs are opaque. Business logic never parses identity or ownership from an ID.
2. A new Astra-owned record uses one canonical prefix from `DomainIdPrefix` plus
   an opaque UUID. Imported legacy IDs remain valid only through an explicit mapping.
3. `team_id` is not a new Astra domain ID. Existing TeamRegistry uses are legacy;
   Slack's `team_id` is stored as `external_workspace_id` under a connector binding.
4. A mutable aggregate has exactly one state owner. Other modules issue commands or
   consume events; they do not update another aggregate's state directly.
5. Every command that may change state carries requester, idempotency, correlation,
   and optimistic-version identity. Every WorkRequest additionally pins its source.
6. REST is the durable command/query boundary. Realtime events accelerate projection
   updates; a client always catches up by sequence through REST after reconnect.
7. Persist the source event before routing or invoking a model.
8. Role describes responsibility and authority. Plugins/tools describe capability.
   Sandboxes describe execution boundaries. These concepts are never interchangeable.

## Canonical identity names

| ID | Meaning | Notes |
|---|---|---|
| `workspace_id` | Astra ownership and access boundary | Never a project path or Slack team |
| `actor_id` | Stable human, agent, or system identity | Display name and handle may change |
| `membership_id` | One actor's workspace membership | Carries role and lifecycle |
| `agent_definition_id` | Reusable custom-agent definition | Mutable head points to revisions |
| `agent_revision_id` | Immutable definition snapshot | Pinned by every run |
| `agent_id` | Persistent agent installation in a workspace | Owns sandbox and private memory |
| `conversation_id` | DM or group | Independent from a model session |
| `thread_id` | Thread rooted at one message | A task has one canonical thread |
| `message_id` | Stable logical message | Edits create message revisions |
| `task_id` | Durable workspace task | Never a board-local integer in new data |
| `crew_run_id` | Coordinated work graph | Parent of related requests/runs |
| `request_id` | Canonical WorkRequest | One logical dispatch intent |
| `delivery_id` | One request-recipient inbox record | Retried through attempts, not replaced |
| `run_id` | One pinned execution attempt | Uses one agent revision and sandbox job |
| `sandbox_id` | Persistent agent execution boundary | Provider-independent identity |
| `artifact_id` | Logical published output | Revisions are immutable and hashed |
| `approval_id` | One consequential-action decision | Expires and is revalidated before use |
| `external_workspace_id` | Provider-owned workspace identifier | Scoped by connector/account binding |

New code must not use `team_id` as an alias for workspace, conversation, crew, or
connector workspace. Migration tables may retain a `legacy_team_id` column.

## Actors and roles

`ActorKind` is exactly `human`, `agent`, or `system`. An actor is attribution, not
authority by itself. Effective authority is the intersection defined in
`target-state.md` and is evaluated for the original requester and executing agent.

Agent governance roles are finite: Supervisor, Explorer, Builder, Verifier, and
Operator. A custom title such as “SEO Strategist” composes one base role with skills,
plugins, tools, model policy, and connector requirements; it does not invent a new
privilege class.

## Entities and authoritative state owners

| Aggregate | State owner | Contract |
|---|---|---|
| Workspace, membership, invitation | Identity service | Workspace ACL and lifecycle |
| Agent definition and immutable revision | Agent catalog | Revision content never mutates |
| Agent installation | Agent service | Status, active revision, policy, sandbox link |
| Conversation, thread, message | Conversation service | Append/revision/tombstone semantics |
| Task and dependency graph | Task service | Task state, owner, reviewer, readiness |
| Crew run, WorkRequest, delivery, run | Work coordinator | Queue, lease, retry, cancel, budget |
| Sandbox, mount, snapshot, job | Sandbox provider | External isolation and health truth |
| Artifact and immutable revision | Artifact service | Blob metadata, ACL, provenance, tombstone |
| Approval and grant | Approval/permission service | Decision, expiry, revocation, audit |
| Memory and context pin/share | Context service | Scope, provenance, retention |
| Automation | Automation scheduler | Trigger and execution history |
| Connector event/binding | Connector service | External identity and event dedupe |
| Activity/notification | Notification service | Unread projection and preferences |

The current `SessionManager`, `TeamRegistry`, `ChatStore`, and `TeamStore` are legacy
adapters during cutover. They must call the new owners; they are not parallel sources
of truth.

## Agent definition, revision, and installation

- An agent definition owns display identity, handle policy, description, base role,
  and a pointer to its current immutable revision.
- A revision snapshots instructions, skills, plugins, tools, model policy, requested
  connectors, sandbox requirements, memory policy, reply policy, budgets, concurrency,
  and approval policy. Editing creates a revision; it never edits a running snapshot.
- An installation binds one definition to one workspace, one current revision, one
  sandbox, grants, private memory, and lifecycle state.
- Pause rejects new deliveries but preserves queue and data. Archive rejects all new
  work and retains attribution. Reset creates an auditable sandbox/memory operation;
  it does not rewrite history. Delete creates a tombstone and follows retention policy.
- Export excludes credentials, grants, memory, local paths, and machine secrets.

## Conversation contracts

### Direct messages

- A DM has exactly one persistent agent member and at least one authorized human.
- A human message targets exactly that one agent.
- A display-name or handle change never changes membership or historical mentions.
- DM history and artifacts remain in the DM scope unless an explicit ContextShare is
  recorded.

### Groups

- Membership is actor-ID based and records one history policy: `all_history`,
  `from_join`, or `selected_context`.
- Mentions store actor IDs, while the renderer resolves the current handle.
- Explicit agent mentions win over reply mode and preserve conversation member order.
- Multiple mentions create one delivery per unique agent.
- In coordinator mode, an unmentioned human message targets the configured coordinator.
- Without a coordinator, the message is persisted but no agent runs; UI asks for a
  mention.
- Mention-only mode never wakes an unmentioned agent.
- All-messages mode is explicit policy and remains bounded by `max_fanout`.
- `@all` must be enabled, returns a target/cost preview, and is not dispatchable until
  confirmed. Every route respects the hard fan-out limit.
- A free-form agent message never wakes another agent. Bot-to-bot execution requires a
  structured HandoffContract.

`ConversationRoutePolicy` and `route_message()` are the executable source of truth for
these rules. No UI or runtime may add a second routing algorithm.

## Task, crew, and handoff contracts

- A task has one active owner, optional independent reviewer, acceptance criteria,
  canonical thread, dependencies, and immutable event history.
- Dependency readiness is derived. Starting an `open` task requires every dependency
  to be `done`.
- Dependency insertion rejects self-links and cycles before commit.
- The owner may start, block, resume, and submit its work for review. Protected
  `review → done` requires a reviewer who is not the active owner.
- Assignment and claim use optimistic versioning and idempotency; two agents cannot
  acquire ownership concurrently.
- A crew run groups all work under one coordinated graph and supplies parent
  cancellation/budget scope.
- A handoff carries original requester, sender, recipient, task, purpose, inputs,
  expected output, budget, idempotency, correlation, depth, and agent path.
- The recipient may not already occur in the handoff path. Depth may not exceed the
  WorkBudget handoff cap. A handoff creates a normal WorkRequest and durable delivery.

## Canonical WorkRequest

Every entry point produces the same immutable shape:

- identity: `request_id`, `workspace_id`, `idempotency_key`, `correlation_id`,
  optional `causation_id`;
- attribution: original `requester` and one or more resolved `target_agent_ids`;
- source: one of direct message, group message, task, automation, connector event, or
  handoff plus a typed `source_ref`;
- context: conversation/thread/task links, context references, and input artifact IDs;
- intent: instruction and expected output;
- controls: effective permission scope snapshot, priority, WorkBudget, and timestamp.

Direct-message WorkRequests have exactly one target. Source-specific references are
validated by `WorkRequest`. Permission snapshots are immutable and cannot be expanded
by prompts or tools.

## State machines and transition authority

The complete legal edges live in `lifecycle.STATE_EDGES`; every edge has a non-empty
entry in `TRANSITION_AUTHORITIES` and every machine has one `STATE_OWNERS` entry.

| Machine | Authoritative writer | Human/agent command authority |
|---|---|---|
| Agent | Agent service/runtime | owner/admin may pause, resume, archive, reprovision |
| Sandbox | Sandbox provider | admin may request suspend, reset, archive |
| Delivery | Work coordinator | requester may cancel through coordinator |
| Run | Work coordinator/runtime | requester may cancel; approval service may resume |
| Task | Task service | owner works; independent reviewer closes; admin coordinates |
| Approval | Approval service | authorized approver decides; requester may cancel |

Illegal state edges fail before authorization. Legal edges then require an allowed
relationship authority. Services record the actual actor, requester, reason, prior
version, and resulting event.

## Delivery, retry, offline, and cancellation

- Delivery is at-least-once. `(workspace_id, target_agent_id, idempotency_key)` is
  unique for one logical dispatch.
- Claim is an atomic lease. Heartbeats extend it; process death lets it expire and be
  reclaimed. A lease is not success.
- Dispatch records an attempt. Acknowledgement occurs only after the run boundary is
  durably created. Model/tool failure cannot acknowledge a delivery as successful.
- Retry creates/updates an attempt with bounded backoff. Exhaustion moves to visible
  dead-letter state. Consequential side effects require their own provider idempotency
  key or are marked non-retriable.
- Offline sandbox leaves the delivery pending/retryable and emits visible health state.
- Cancellation propagates crew → request → delivery → run → sandbox job. Terminal
  records stay terminal, and non-reversible external actions are reported explicitly.

## API and realtime envelopes

- Mutations use `CommandEnvelope`: requester, workspace, idempotency, correlation,
  optional causation and expected version, typed command name, immutable payload.
- Reads use `QueryEnvelope`: requester, workspace, correlation, bounded limit, opaque
  cursor, immutable filters.
- Durable/realtime facts use `DomainEvent`: event ID/type, workspace, attributed actor,
  aggregate type/ID, correlation/causation, sequence, schema version, immutable payload,
  and timezone-aware timestamp.
- Clients discard already-applied sequence numbers and use REST catch-up after gaps.
  WebSocket arrival order is not treated as durable truth.
- Duplicate commands with the same key and same canonical payload return the original
  result. Reusing a key with different payload returns `idempotency_conflict`.
- An `expected_version` mismatch returns `stale_revision`; no partial side effect occurs.

The error taxonomy is `ApiErrorCode`. Responses include a safe message, correlation ID,
retryability, optional retry delay, and redacted details. Internal exceptions and secrets
never cross this envelope.

## Context and memory boundaries

The only scopes are agent-private, direct-message, group, task, workspace, and
run-scratch. A record always carries both scope kind and scope ID.

- Workspace knowledge may be selected into a narrower context after normal permission
  checks.
- Every other durable cross-boundary publication creates an explicit ContextShare.
- Agent-private or DM material never enters group, task, or workspace memory implicitly.
- Run scratch is discarded by default; publishing any part creates a scoped artifact,
  task event, message, or ContextShare with provenance.
- Group membership does not grant access to DMs, private agent memory, private artifacts,
  or unrelated task context.
- Search indexes and context builders apply the same ACL as direct reads.

## Artifact publication

An artifact revision is immutable and records logical artifact/revision IDs, source run,
optional source task/ref, publisher, original requester, visibility, scope ID, safe
filename, MIME type, size, SHA-256, idempotency, correlation, metadata, and timestamp.

Visibility is exactly one of agent-private, DM, group, task, or workspace and maps to
one ContextBoundary. “File exists in a sandbox” is not publication. New revisions never
overwrite old blobs. Archive and delete use aggregate events/tombstones; provenance and
hash metadata remain according to retention policy.

## Loading, archive, delete, and recovery semantics

- Queries distinguish loading, empty, partial page, full page, offline cache, forbidden,
  and error states. An empty array is not an error or an authorization denial.
- Archive is reversible and removes an aggregate from default active lists. Delete is a
  tombstone plus retention workflow, not an immediate cascading filesystem erase.
- Historical attribution survives agent, member, conversation, task, and artifact
  archival/deletion.
- A failed create/provision operation remains queryable with error state and recovery
  action. Retrying uses the original idempotency key or a new explicit command.
- Restarts rebuild projections from durable rows/events and reclaim expired leases; they
  do not infer success from a missing worker process.

## Compatibility mapping

| Current source | New destination | Rule |
|---|---|---|
| `SessionRecord.session_id` plain chat | Legacy session link on conversation/run | Keep JSONL readable; do not rename in place |
| `SessionRecord.team.team_id` | `legacy_team_id` mapping to group conversation and crew history | Never copy into `workspace_id` |
| `SessionRecord.team.actor` | Agent installation display/legacy actor mapping | Mint stable `agent_id`; preserve attribution |
| `TeamRegistry` lead/workers | Agent installations plus initial group membership | Registry becomes compatibility adapter |
| `ChatStore.chat_groups.group_id` | `conversation_id` mapping | Migrate messages/cursors idempotently |
| `ChatStore.chat_messages.seq` | Import order and legacy source ref | Mint stable message IDs |
| `TeamStore.space` | Workspace mapping by resolved project | Preserve exact space for hash verification |
| `TeamStore.team_items.id` | Legacy task mapping to stable `task_id` | Preserve integer in `legacy_item_id` |
| `TeamStore.team_events` | Imported task/audit events | Never rewrite hashed fields |
| Slack `team_id` | Connector binding `external_workspace_id` | Provider namespace remains unchanged |
| Automation destination session | Conversation/thread/task destination link | Preserve run history |

Migration writes mapping tables and new rows in a transaction after a verified backup.
It does not dual-write indefinitely. Existing board hash chains are verified before and
after, because changing `space` or hashed payloads would invalidate them.

## Required flow walkthroughs

1. DM/group message is persisted, deterministically routed, authorized, converted to a
   WorkRequest, delivered, leased, run, published, and projected back to conversation.
2. Task, automation, connector event, and handoff enter the same pipeline after their
   source event is durable.
3. Approval pauses a run without consuming its delivery; decision revalidates grants and
   resumes or terminates the run.
4. Duplicate input resolves to the original command/request/message; stale revision is
   rejected; unauthorized requester creates a denial audit without a delivery.
5. Parent cancel terminates descendants where reversible and reports external actions.
6. Offline sandbox preserves queued work. Restart reclaims expired leases and resumes
   from durable state.
7. Dependency cycle is rejected at command time; incomplete dependencies remain visible
   but cannot dispatch.

These walkthroughs are acceptance tests for downstream tasks, not optional examples.
