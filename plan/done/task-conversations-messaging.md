# T07 — Conversations and messaging

Status: complete
Dependencies: T02, T03, T04, T06  
Unlocks: T08–T15

## Outcome

Replace the one-group-per-team exception chat with a general durable
conversation service supporting direct messages, groups, threads, mentions,
message actions, unread state, and deterministic agent routing.

## Checklist

- [x] Model DM and group conversations independently from agent sessions.
- [x] Add human and agent conversation membership with history policy.
- [x] Implement create, rename, archive, delete, mute, pin, and member management.
- [x] Implement message append, edit revision, delete tombstone, quote, reaction,
      attachment, pin, translation, and stable link.
- [x] Implement thread roots, replies, follow state, and canonical task thread link.
- [x] Store mentions by actor ID and render current handles.
- [x] Implement reply modes: mention-only, coordinator, all-messages.
- [x] Add `@all` preview and dispatch policy.
- [x] Convert message dispatch into WorkRequests and durable deliveries.
- [x] Run accepted DM/group deliveries and append agent replies without recursive
      re-dispatch.
- [x] Add drafts and optimistic client IDs with server reconciliation.
- [x] Separate human-visible progress from expandable run/tool detail.
- [x] Remove polling and expose cursor-paginated queries.
- [x] Replace implicit "user message wakes everyone" semantics.

## Acceptance criteria

- [x] DM wakes exactly one target agent.
- [x] Mention-only group wakes only mentioned agents.
- [x] Unmentioned group message wakes only the coordinator.
- [x] Renaming an agent does not break old mentions or message links.
- [x] DM and group ACLs are enforced on history, files, search, and events.
- [x] Duplicate client sends create one message and one logical dispatch.
- [x] Thread unread state does not corrupt parent conversation unread state.

## Verification

- Unicode handles, rename, membership changes, late join, deleted agent, duplicate
  send, offline send, reconnect, and large-history pagination.
- Privacy tests across DMs, groups, removed members, and private artifacts.

## Evidence

Durable repository, coordinator, canonical REST API, search projection, worker
execution, and Team GUI wiring are covered by the Team/server tests. The canonical
worker persists an agent reply without recursively creating another WorkRequest,
and `test_team_agent_rename_preserves_historical_message_attribution` proves stable
historical author links across rename and archive. Full membership ACL coverage,
durable thread unread tracking, and optimistic client request reconciliation are
verified.

The revision-scoped `team_send_message` capability lets an agent publish an
idempotent progress message in its current conversation or thread without peer wakes;
executable delegation remains a structured handoff.

Latest verification: full `tests/test_team_*.py` suite passes.
